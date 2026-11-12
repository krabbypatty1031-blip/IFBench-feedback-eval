"""Scoring and feedback tests.

Official strict/loose remain the source of pass/fail. Feedback may only quote
requirement text that already appears in the prompt; verifier kwargs and
exceptions must not be rewritten as ordinary failures.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import evaluation_lib
from feedback_eval.dataset import load_items
from feedback_eval.feedback import (
    ConstraintMappingError,
    VerifierFeedbackError,
    audit_constraint_support,
    extract_requirements,
    make_feedback,
)
from feedback_eval.scoring import score
from ifbench import instructions_registry

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OFFICIAL_TEST_JSONL = _REPO_ROOT / "data" / "IFBench_test.jsonl"
_SPLIT_PATH = _REPO_ROOT / "configs" / "feedback_eval" / "split.json"


def _item(
    key: str,
    prompt: str,
    instruction_ids: list[str],
    kwargs: list[dict] | None = None,
) -> dict:
    return {
        "key": key,
        "prompt": prompt,
        "instruction_id_list": instruction_ids,
        "kwargs": kwargs
        if kwargs is not None
        else [{} for _ in instruction_ids],
    }


def _conjunction_item() -> dict:
    return _item(
        "c1",
        "Say hello. Use at least 2 different coordinating conjunctions in the response.",
        ["count:conjunctions"],
        [{"small_n": 2}],
    )


def _official_score(item: dict, response: str, fn) -> evaluation_lib.OutputExample:
    inp = evaluation_lib.InputExample(
        key=item["key"],
        instruction_id_list=list(item["instruction_id_list"]),
        prompt=item["prompt"],
        kwargs=copy.deepcopy(item["kwargs"]),
    )
    return fn(inp, {item["prompt"]: response})


def test_score_matches_official_strict_and_loose_on_pass_and_fail() -> None:
    item = _conjunction_item()
    passing = "I wanted tea and cake but they had none so I left."
    failing = "Hello."
    for response in (passing, failing):
        ours = score(item, response)
        strict = _official_score(
            item, response, evaluation_lib.test_instruction_following_strict
        )
        loose = _official_score(
            item, response, evaluation_lib.test_instruction_following_loose
        )
        assert ours["strict"]["error"] is None
        assert ours["loose"]["error"] is None
        assert ours["strict"]["all_pass"] is strict.follow_all_instructions
        assert ours["strict"]["per_constraint"] == list(strict.follow_instruction_list)
        assert ours["loose"]["all_pass"] is loose.follow_all_instructions
        assert ours["loose"]["per_constraint"] == list(loose.follow_instruction_list)


def test_score_keeps_dual_constraint_order() -> None:
    item = _item(
        "d1",
        (
            "Use at least 2 different coordinating conjunctions in the response. "
            "Include exactly 1 numbers in the response."
        ),
        ["count:conjunctions", "count:numbers"],
        [{"small_n": 2}, {"N": 1}],
    )
    response = "I wanted tea and cake but they had none."
    ours = score(item, response)
    strict = _official_score(
        item, response, evaluation_lib.test_instruction_following_strict
    )
    assert ours["strict"]["per_constraint"] == list(strict.follow_instruction_list)
    assert len(ours["strict"]["per_constraint"]) == 2


def test_empty_response_is_failure_not_error() -> None:
    ours = score(_conjunction_item(), "")
    assert ours["strict"]["error"] is None
    assert ours["strict"]["all_pass"] is False
    assert ours["strict"]["per_constraint"] == [False]


def test_verifier_exception_is_error_not_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    cls = instructions_registry.INSTRUCTION_DICT["count:conjunctions"]

    def boom(self, value: str) -> bool:
        raise RuntimeError("verifier boom")

    monkeypatch.setattr(cls, "check_following", boom)
    ours = score(_conjunction_item(), "and but so")
    assert ours["strict"]["all_pass"] is None
    assert ours["strict"]["per_constraint"] is None
    assert ours["strict"]["error"] is not None
    assert "boom" in ours["strict"]["error"]


def test_score_does_not_mutate_item_kwargs() -> None:
    item = _conjunction_item()
    item["kwargs"][0]["unused"] = None
    before = copy.deepcopy(item["kwargs"])
    score(item, "and but")
    assert item["kwargs"] == before


def test_b2_feedback_reports_overall_strict_status_only() -> None:
    item = _conjunction_item()
    passing = score(item, "I wanted tea and cake but they had none so I left.")
    failing = score(item, "Hello.")
    passed_text = make_feedback(item, passing["strict"], "B2")
    failed_text = make_feedback(item, failing["strict"], "B2")
    assert "passed" in passed_text.lower()
    assert "failed" in failed_text.lower()
    assert "coordinating conjunctions" not in passed_text


def test_b3_feedback_uses_prompt_requirement_and_strict_order() -> None:
    item = _item(
        "d2",
        (
            "Use at least 2 different coordinating conjunctions in the response. "
            "Include exactly 1 numbers in the response."
        ),
        ["count:conjunctions", "count:numbers"],
        [{"small_n": 2}, {"N": 1}],
    )
    result = score(item, "I wanted tea and cake but they had none.")
    text = make_feedback(item, result["strict"], "B3")
    assert "2.0" not in text
    assert "Use at least 2 different coordinating conjunctions in the response." in text
    assert "Include exactly 1 numbers in the response." in text
    first = text.find("Use at least 2 different coordinating conjunctions")
    second = text.find("Include exactly 1 numbers")
    assert 0 <= first < second
    # Status strings follow the official per-constraint order.
    lines = [line for line in text.splitlines() if line.startswith("1.") or line.startswith("2.")]
    assert len(lines) == 2
    assert ("PASS" in lines[0]) is result["strict"]["per_constraint"][0]
    assert ("PASS" in lines[1]) is result["strict"]["per_constraint"][1]


def test_b3_rejects_unmappable_constraint_instead_of_guessing() -> None:
    item = _item(
        "bad",
        "Just say hello, with no output constraint written here.",
        ["count:conjunctions"],
        [{"small_n": 2}],
    )
    with pytest.raises(ConstraintMappingError, match="count:conjunctions"):
        extract_requirements(item)
    with pytest.raises(ConstraintMappingError):
        make_feedback(
            item,
            {"all_pass": False, "per_constraint": [False], "error": None},
            "B3",
        )


def test_make_feedback_rejects_verifier_errors() -> None:
    item = _conjunction_item()
    with pytest.raises(VerifierFeedbackError, match="boom"):
        make_feedback(
            item,
            {"all_pass": None, "per_constraint": None, "error": "RuntimeError: boom"},
            "B2",
        )


def test_extract_requirements_covers_official_dev_and_all_types() -> None:
    """Structural audit: prompts only. Do not drop types using model outputs."""
    items = load_items(_OFFICIAL_TEST_JSONL)
    split = json.loads(_SPLIT_PATH.read_text(encoding="utf-8"))
    dev_ids = set(split["dev_ids"])
    by_id = {str(item["key"]): item for item in items}
    for item_id in split["dev_ids"]:
        reqs = extract_requirements(by_id[item_id])
        assert len(reqs) == len(by_id[item_id]["instruction_id_list"])
        for req in reqs:
            assert req in by_id[item_id]["prompt"]
    support = audit_constraint_support(items, dev_ids)
    assert support["n_instruction_types"] == 58
    assert all(row["mapping"] == "supported" for row in support["types"].values())


def test_frozen_constraint_support_matches_rebuild() -> None:
    items = load_items(_OFFICIAL_TEST_JSONL)
    split = json.loads(_SPLIT_PATH.read_text(encoding="utf-8"))
    rebuilt = audit_constraint_support(items, set(split["dev_ids"]))
    frozen = json.loads(
        (_REPO_ROOT / "configs" / "feedback_eval" / "constraint_support.json").read_text(
            encoding="utf-8"
        )
    )
    assert frozen["n_instruction_types"] == rebuilt["n_instruction_types"] == 58
    assert frozen["types"].keys() == rebuilt["types"].keys()
    for instruction_id, row in rebuilt["types"].items():
        assert frozen["types"][instruction_id]["mapping"] == row["mapping"]
        assert frozen["types"][instruction_id]["n_items"] == row["n_items"]
