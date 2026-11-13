"""B0–B3 runner tests.

Branches must share one initial answer, never see each other, and still revise
when B0 already passed. Offline only: FakeClient, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from feedback_eval.cli import main
from feedback_eval.client import FakeClient
from feedback_eval.feedback import make_feedback
from feedback_eval.runner import load_prompt_templates, run_item, synthetic_conjunction_item
from feedback_eval.scoring import score
from feedback_eval.store import RunStore, inspect_prior_ledger, record_key
from ifbench import instructions_registry

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPTS_PATH = _REPO_ROOT / "configs" / "feedback_eval" / "prompts.json"
_SPLIT_PATH = _REPO_ROOT / "configs" / "feedback_eval" / "split.json"
_OFFICIAL_JSONL = _REPO_ROOT / "data" / "IFBench_test.jsonl"


def _ok(text: str) -> dict:
    return {
        "text": text,
        "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
        "finish_reason": "stop",
        "latency_ms": 11.0,
        "error": None,
    }


def _conjunction_item(key: str = "c1") -> dict:
    return {
        "key": key,
        "prompt": "Say hello. Use at least 2 different coordinating conjunctions in the response.",
        "instruction_id_list": ["count:conjunctions"],
        "kwargs": [{"small_n": 2}],
    }


def _store_config(tmp_path: Path, prompts: dict) -> dict:
    from feedback_eval.dataset import file_sha256

    fingerprint = {
        "protocol": "feedback-eval-dev-v1",
        "data_sha256": "dry-run-synthetic",
        "model": "mock-model",
        "prompt_sha256": file_sha256(_PROMPTS_PATH),
        "code_version": "store-v1",
        "parameters": {"temperature": 0.0, "max_tokens": 128, "seed": 42},
    }
    store = RunStore(tmp_path)
    store.initialize(fingerprint, inspect_prior_ledger([]))
    return {
        **fingerprint,
        "store": store,
        "prompts": prompts,
    }


def test_b0_called_once_and_revision_branches_are_independent() -> None:
    item = _conjunction_item()
    b0_text = "I wanted tea and cake but they had none so I left."
    client = FakeClient(
        [
            _ok(b0_text),
            _ok("B1 and but so revised"),
            _ok("B2 and but so revised"),
            _ok("B3 and but so revised"),
        ]
    )
    result = run_item(item, client, {"model": "mock-model", "protocol": "feedback-eval-dev-v1", "prompts": load_prompt_templates(), "parameters": {"temperature": 0.0, "max_tokens": 128}})
    assert len(client.calls) == 4
    assert client.calls[0]["messages"] == [{"role": "user", "content": item["prompt"]}]
    for call in client.calls[1:]:
        messages = call["messages"]
        assert messages[0] == {"role": "user", "content": item["prompt"]}
        assert messages[1] == {"role": "assistant", "content": b0_text}
        assert messages[2]["role"] == "user"
        assert "B1 and but so revised" not in json.dumps(messages)
        assert "B2 and but so revised" not in json.dumps(messages)
        assert "B3 and but so revised" not in json.dumps(messages)
    b1_user = client.calls[1]["messages"][2]["content"]
    b2_user = client.calls[2]["messages"][2]["content"]
    b3_user = client.calls[3]["messages"][2]["content"]
    assert "Verifier (strict)" not in b1_user
    assert "Verifier (strict)" in b2_user
    assert "one line per constraint" in b3_user
    assert "one line per constraint" not in b2_user
    assert result["B0"]["generation"]["text"] == b0_text
    assert result["B1"]["generation"]["text"] == "B1 and but so revised"
    assert result["B0"]["accounting_status"] == "success"
    assert result["B1"]["accounting_status"] == "success"
    assert result["B2"]["accounting_status"] == "success"
    assert result["B3"]["accounting_status"] == "success"


def test_empty_generation_is_classified_without_store() -> None:
    """Store-less runs still need accounting_status for summarize/reconcile keys."""
    item = _conjunction_item()
    empty = {
        "text": "",
        "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
        "finish_reason": "stop",
        "latency_ms": 1.0,
        "error": None,
    }
    client = FakeClient([empty, empty, empty, empty])
    result = run_item(
        item,
        client,
        {
            "model": "mock-model",
            "protocol": "feedback-eval-dev-v1",
            "prompts": load_prompt_templates(),
            "parameters": {"temperature": 0.0, "max_tokens": 128},
        },
    )
    assert result["B0"]["accounting_status"] == "empty_response"
    assert result["B3"]["accounting_status"] == "empty_response"


def test_passed_initial_answer_is_still_revised() -> None:
    item = _conjunction_item()
    passing = "I wanted tea and cake but they had none so I left."
    client = FakeClient([_ok(passing), _ok("rev1 and but"), _ok("rev2 and but"), _ok("rev3 and but")])
    result = run_item(
        item,
        client,
        {
            "model": "mock-model",
            "protocol": "feedback-eval-dev-v1",
            "prompts": load_prompt_templates(),
            "parameters": {"temperature": 0.0},
        },
    )
    assert result["B0"]["score"]["strict"]["all_pass"] is True
    assert len(client.calls) == 4
    allow = "may leave the answer unchanged"
    assert allow in client.calls[2]["messages"][2]["content"].lower()
    assert allow in client.calls[3]["messages"][2]["content"].lower()
    assert allow not in client.calls[1]["messages"][2]["content"].lower()


def test_b2_b3_feedback_matches_official_strict() -> None:
    item = _conjunction_item()
    failing = "Hello."
    client = FakeClient(
        [_ok(failing), _ok("r1 and but"), _ok("r2 and but"), _ok("r3 and but")]
    )
    result = run_item(
        item,
        client,
        {
            "model": "mock-model",
            "protocol": "feedback-eval-dev-v1",
            "prompts": load_prompt_templates(),
            "parameters": {"temperature": 0.0},
        },
    )
    official = score(item, failing)
    assert result["B0"]["score"]["strict"]["all_pass"] is official["strict"]["all_pass"] is False
    assert result["B0"]["score"]["strict"]["per_constraint"] == official["strict"]["per_constraint"]
    expected_b2 = make_feedback(item, official["strict"], "B2")
    expected_b3 = make_feedback(item, official["strict"], "B3")
    assert expected_b2 in client.calls[2]["messages"][2]["content"]
    assert expected_b3 in client.calls[3]["messages"][2]["content"]
    assert "2.0" not in client.calls[3]["messages"][2]["content"]


def test_verifier_exception_does_not_invent_b2_b3_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cls = instructions_registry.INSTRUCTION_DICT["count:conjunctions"]

    def boom(self, value: str) -> bool:
        raise RuntimeError("verifier boom")

    monkeypatch.setattr(cls, "check_following", boom)
    item = _conjunction_item()
    client = FakeClient([_ok("and but so"), _ok("revised and but")])
    result = run_item(
        item,
        client,
        {
            "model": "mock-model",
            "protocol": "feedback-eval-dev-v1",
            "prompts": load_prompt_templates(),
            "parameters": {"temperature": 0.0},
        },
    )
    assert len(client.calls) == 2
    assert result["B2"]["generation"]["text"] is None
    assert result["B3"]["generation"]["text"] is None
    assert result["B2"]["feedback_error"]
    assert result["B3"]["feedback_error"]
    assert "PASSED" not in (result["B2"].get("feedback") or "")
    assert "FAILED" not in (result["B3"].get("feedback") or "")


def test_revision_prompts_keep_task_and_forbid_extra_wrappers() -> None:
    templates = load_prompt_templates()
    shared = templates["revision_shared"].lower()
    output_rule = templates["output_rule"].lower()
    assert "original" in shared
    assert "final answer" in output_rule or "only" in output_rule
    assert "json" not in output_rule
    assert templates["allow_unchanged_if_passed"]


def test_dev_item_feedback_quotes_prompt_not_test_outputs() -> None:
    """Structural check on a development-split item. Do not tune on test outputs."""
    from feedback_eval.dataset import load_items
    from feedback_eval.feedback import extract_requirements, make_feedback

    split = json.loads(_SPLIT_PATH.read_text(encoding="utf-8"))
    items = {str(item["key"]): item for item in load_items(_OFFICIAL_JSONL)}
    dev_item = items[split["dev_ids"][0]]
    requirements = extract_requirements(dev_item)
    fake_strict = {
        "all_pass": False,
        "per_constraint": [False] * len(requirements),
        "error": None,
    }
    text = make_feedback(dev_item, fake_strict, "B3")
    for requirement in requirements:
        assert requirement in dev_item["prompt"]
        assert requirement in text
    assert split["dev_ids"][0] not in split["test_ids"]


def test_four_items_resume_does_not_regenerate(tmp_path: Path) -> None:
    prompts = load_prompt_templates()
    config = _store_config(tmp_path, prompts)
    items = [synthetic_conjunction_item(str(index)) for index in range(4)]
    first = FakeClient([_ok(f"ans{i} and but so") for i in range(16)])
    for item in items:
        run_item(item, first, config)
    assert len(first.calls) == 16
    stored = config["store"].get_record(
        record_key("feedback-eval-dev-v1", "mock-model", "0", "B0", 0)
    )
    assert stored is not None
    assert stored["accounting_status"] == "success"
    second = FakeClient([])
    for item in items:
        run_item(item, second, config)
    assert second.calls == []


def test_dry_run_reports_call_counts_and_resumes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["dry-run", "--n-items", "4", "--store-dir", str(tmp_path)])
    first = capsys.readouterr().out
    assert "planned_calls=16" in first
    assert "actual_calls=16" in first
    main(["dry-run", "--n-items", "4", "--store-dir", str(tmp_path)])
    second = capsys.readouterr().out
    assert "actual_calls=0" in second
    assert "planned_calls=16" in second
