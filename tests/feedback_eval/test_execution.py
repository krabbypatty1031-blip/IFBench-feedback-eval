"""Task 7 offline scheduling and accounting rehearsal."""

from pathlib import Path

import pytest

from feedback_eval.cli import main
from feedback_eval.client import FakeClient, OpenAICompatClient
from feedback_eval.execution import BudgetExhausted, BudgetedClient, rehearse, summarize
from feedback_eval.runner import synthetic_conjunction_item
from feedback_eval.store import RunStore, inspect_prior_ledger


def ok():
    return {"text": "hello and but so", "usage": {"prompt_tokens": 1,
            "completion_tokens": 1, "total_tokens": 2}, "finish_reason": "stop",
            "latency_ms": 1.0, "error": None}


@pytest.fixture
def config(tmp_path):
    store = RunStore(tmp_path)
    config = {"protocol": "rehearsal", "data_sha256": "synthetic",
              "model": "a,b", "prompt_sha256": "synthetic", "code_version": "v1",
              "parameters": {"temperature": 0.0}}
    store.initialize(config, inspect_prior_ledger([]))
    return {**config, "store": store}


def test_stops_and_resumes_without_duplicate_calls(config):
    items = [synthetic_conjunction_item("1"), synthetic_conjunction_item("2")]
    first = rehearse(items, ["a", "b"], FakeClient([ok()] * 16), config, 5)
    assert first["calls_this_invocation"] == 5
    assert first["stopped"] == "simulated_call_limit"
    assert first["statuses"] == {"success": 5, "missing": 11}
    second = rehearse(items, ["a", "b"], FakeClient([ok()] * 16), config, 16)
    assert second["calls_this_invocation"] == 11
    assert second["statuses"] == {"success": 16}
    assert second["expected"] == second["accounted"] == 16
    third = rehearse(items, ["a", "b"], FakeClient([]), config, 0)
    assert third["calls_this_invocation"] == 0


def test_rehearsal_rejects_live_clients_and_duplicate_ids(config):
    item = synthetic_conjunction_item("1")
    with pytest.raises(TypeError):
        rehearse([item], ["a", "b"], object(), config, 4)
    with pytest.raises(ValueError):
        rehearse([item, item], ["a", "b"], FakeClient([]), config, 4)
    with pytest.raises(ValueError):
        rehearse([item], ["a", "a"], FakeClient([]), config, 4)


def test_summarize_counts_missing_and_links_scores(config):
    items = [synthetic_conjunction_item("1"), synthetic_conjunction_item("2")]
    models = ["a", "b"]
    empty = summarize(items, models, config)
    assert empty["expected"] == 16
    assert empty["missing"] == 16
    assert empty["success"] == 0
    assert empty["cost_unknown"] == 16
    assert empty["incomplete"] is True
    assert len(empty["score_links"]) == 16
    assert {row["strict_all_pass"] for row in empty["score_links"]} == {None}

    rehearse(items, models, FakeClient([ok()] * 16), config, 16)
    filled = summarize(items, models, config)
    assert filled["success"] == 16
    assert filled["missing"] == 0
    assert filled["generation_failures"] == 0
    assert filled["accounted"] == filled["expected"]
    assert all(row["has_text"] for row in filled["score_links"])
    assert all(row["strict_all_pass"] is not None for row in filled["score_links"])


def test_budgeted_client_stops_before_second_call(tmp_path: Path):
    store = RunStore(tmp_path)
    price = {
        "input_per_million": 1.0,
        "output_per_million": 1.0,
        "currency": "CNY",
        "source": "verified",
    }
    config = {
        "protocol": "rehearsal",
        "data_sha256": "synthetic",
        "model": "a",
        "prompt_sha256": "synthetic",
        "code_version": "v1",
        "parameters": {"temperature": 0.0, "max_tokens": 1, "seed": 42},
    }
    store.initialize(
        config,
        inspect_prior_ledger([]),
        unit_prices_by_model={"a": price},
        this_run_currency="CNY",
    )
    inner = FakeClient([ok(), ok()])
    client = BudgetedClient(
        inner,
        store,
        "a",
        price,
        cost_cap=0.000003,
        wallet=1.0,
        max_tokens=1,
        input_hold_tokens=1,
    )
    first = client.generate([{"role": "user", "content": "x"}], {"max_tokens": 1, "model": "a"})
    store.put_record(
        "k1",
        {
            "record_key": "k1",
            "settings": {"model": "a"},
            "messages": [{"role": "user", "content": "x"}],
            "generation": first,
        },
    )
    with pytest.raises(BudgetExhausted):
        client.generate([{"role": "user", "content": "y"}], {"max_tokens": 1, "model": "a"})


def test_offline_official_cli_reports_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "run-official",
            "--offline",
            "--n-items",
            "2",
            "--store-dir",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr().out
    assert code == 0
    assert "expected=16" in captured
    assert "actual_calls=16" in captured
    assert "client=FakeClient" in captured
    summary = (tmp_path / "execution_summary.json").read_text(encoding="utf-8")
    assert "FakeClient" in summary


def test_paid_official_cli_blocked_without_auth(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["run-official", "--paid", "--n-items", "1", "--store-dir", str(tmp_path)])
    captured = capsys.readouterr().out
    assert code == 3
    assert "official_run_blocked" in captured
    assert not list(tmp_path.glob("records/*.json"))


def test_paid_official_cli_uses_injected_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prior = {
        "status": "ok",
        "spent": None,
        "balance": 54.24,
        "currency": "CNY",
        "paths": [],
        "unit_prices": None,
    }
    monkeypatch.setenv("FEEDBACK_EVAL_AUTHORIZE_SPEND", "1")
    monkeypatch.setenv("API_KEY", "secret-key")
    monkeypatch.setattr("feedback_eval.cli._refresh_prior_wallet", lambda *a, **k: prior)
    monkeypatch.setattr("feedback_eval.cli.inspect_prior_ledger", lambda paths: prior)

    def fake_generate(self: OpenAICompatClient, messages: list[dict], settings: dict) -> dict:
        assert "secret-key" not in str(messages)
        return ok()

    monkeypatch.setattr(OpenAICompatClient, "generate", fake_generate)
    code = main(
        [
            "run-official",
            "--paid",
            "--n-items",
            "1",
            "--store-dir",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr().out
    assert "secret-key" not in captured
    assert code == 0
    assert "client=OpenAICompatClient" in captured
    assert "expected=8" in captured
    assert "actual_calls=8" in captured
    dumped = "".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.json"))
    assert "secret-key" not in dumped
