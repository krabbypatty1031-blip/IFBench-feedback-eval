"""Client, identity, and ledger tests.

Calls stay on FakeClient: no network and no billed API. Empty text, truncation,
timeouts, and missing usage are accounting states, not refusals or score fails.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from feedback_eval.client import (
    FakeClient,
    OpenAICompatClient,
    classify_generation,
    sanitize_settings,
)
from feedback_eval.store import (
    ConfigConflictError,
    IncompleteLedgerError,
    ResumeConflictError,
    RunStore,
    StoreError,
    config_fingerprint,
    inspect_prior_ledger,
    record_key,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _config(**overrides: object) -> dict:
    payload = {
        "protocol": "feedback-eval-dev-v1",
        "data_sha256": "d" * 64,
        "model": "mock-model",
        "prompt_sha256": "p" * 64,
        "code_version": "store-v1",
        "parameters": {"temperature": 0.0, "max_tokens": 128, "seed": 42},
    }
    payload.update(overrides)
    return payload


def _ok_generation() -> dict:
    return {
        "text": "hello and thanks but anyway",
        "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
        "finish_reason": "stop",
        "latency_ms": 12.5,
        "error": None,
    }


def test_record_key_is_stable_and_unique_per_identity() -> None:
    first = record_key("feedback-eval-dev-v1", "mock-model", "12", "B0", 0)
    second = record_key("feedback-eval-dev-v1", "mock-model", "12", "B0", 0)
    assert first == second
    assert first != record_key("feedback-eval-dev-v1", "mock-model", "12", "B1", 0)
    assert first != record_key("feedback-eval-dev-v1", "mock-model", "12", "B0", 1)


def test_fake_client_success_returns_required_fields() -> None:
    client = FakeClient([_ok_generation()])
    result = client.generate(
        [{"role": "user", "content": "hi"}],
        {"model": "mock-model", "temperature": 0.0},
    )
    assert set(result) >= {"text", "usage", "finish_reason", "latency_ms", "error"}
    assert result["text"] == "hello and thanks but anyway"
    assert result["error"] is None
    assert classify_generation(result) == "success"
    assert classify_generation(result) not in {"refusal", "score_fail"}


def test_timeout_rate_limit_truncation_empty_and_missing_usage_are_not_refusals() -> None:
    scripts = [
        {
            "text": None,
            "usage": None,
            "finish_reason": "timeout",
            "latency_ms": 30000,
            "error": "timeout",
        },
        {
            "text": None,
            "usage": None,
            "finish_reason": "rate_limit",
            "latency_ms": 20,
            "error": "429 Too Many Requests",
        },
        {
            "text": "partial answer",
            "usage": {"prompt_tokens": 4, "completion_tokens": 128, "total_tokens": 132},
            "finish_reason": "length",
            "latency_ms": 40,
            "error": None,
        },
        {
            "text": "visible text",
            "usage": None,
            "finish_reason": "stop",
            "latency_ms": 9,
            "error": None,
        },
        {
            "text": "",
            "usage": {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4},
            "finish_reason": "stop",
            "latency_ms": 8,
            "error": None,
        },
    ]
    expected = ["timeout", "rate_limit", "truncated", "missing_usage", "empty_response"]
    client = FakeClient(scripts)
    for status in expected:
        result = client.generate([{"role": "user", "content": "x"}], {"model": "mock-model"})
        assert classify_generation(result) == status
        assert classify_generation(result) not in {"refusal", "score_fail"}
        assert result.get("score_fail") is None


def test_sanitize_settings_drops_secrets() -> None:
    cleaned = sanitize_settings(
        {"model": "mock-model", "api_key": "secret-key", "Authorization": "Bearer secret-key"}
    )
    blob = json.dumps(cleaned)
    assert "secret-key" not in blob
    assert cleaned["model"] == "mock-model"


def test_openai_client_reads_api_key_from_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "secret-key")
    client = OpenAICompatClient(api_base="http://127.0.0.1:9/v1", model="mock-model")
    headers = client.headers()
    assert headers["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in repr(client)


def test_fingerprint_changes_when_model_or_prompts_change() -> None:
    base = config_fingerprint(_config())
    assert base == config_fingerprint(_config())
    assert base != config_fingerprint(_config(model="other-model"))
    assert base != config_fingerprint(_config(prompt_sha256="e" * 64))


def test_store_put_is_idempotent_but_rejects_different_content(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    store.initialize(_config(), inspect_prior_ledger([]))
    key = record_key("feedback-eval-dev-v1", "mock-model", "12", "B0", 0)
    record = {
        "record_key": key,
        "messages": [{"role": "user", "content": "hi"}],
        "generation": _ok_generation(),
    }
    store.put_record(key, record)
    store.put_record(key, record)
    changed = {
        "record_key": key,
        "messages": [{"role": "user", "content": "hi"}],
        "generation": {**_ok_generation(), "text": "different"},
    }
    with pytest.raises(ResumeConflictError):
        store.put_record(key, changed)


def test_config_fingerprint_mismatch_rejects_resume(tmp_path: Path) -> None:
    first = RunStore(tmp_path)
    first.initialize(_config(), inspect_prior_ledger([]))
    with pytest.raises(ConfigConflictError):
        RunStore(tmp_path).initialize(_config(model="other-model"), inspect_prior_ledger([]))


def test_incomplete_ledger_rejects_resume(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    store.initialize(_config(), inspect_prior_ledger([]))
    store.ledger_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(IncompleteLedgerError):
        RunStore(tmp_path).initialize(_config(), inspect_prior_ledger([]))


def test_missing_prior_ledger_is_unverified_not_zero() -> None:
    prior = inspect_prior_ledger([_REPO_ROOT / "runs" / "feedback_eval" / "ledger.json"])
    assert prior["status"] == "unverified_missing"
    assert prior["spent"] is None
    assert prior["balance"] is None
    assert prior["unit_prices"] is None
    assert prior["spent"] != 0


def test_initialize_rejects_known_amounts_on_unverified_prior(tmp_path: Path) -> None:
    """Missing ledgers may start a run only when spend/balance stay unknown.

    `spent is None` is the honest inspect_prior_ledger() result. Forging 0 or
    any other number would treat unverifiable history as a known amount.
    """
    honest = inspect_prior_ledger([])
    assert honest["spent"] is None
    assert honest["balance"] is None
    RunStore(tmp_path / "honest").initialize(_config(), honest)

    for forged in (
        {**honest, "spent": 0},
        {**honest, "spent": 0.0},
        {**honest, "spent": 12.5},
        {**honest, "balance": 0},
        {**honest, "status": "unverified_unparsed", "spent": 0},
    ):
        with pytest.raises(StoreError, match="unverified prior ledger"):
            RunStore(tmp_path / f"forged-{id(forged)}").initialize(_config(), forged)


def test_store_records_unverified_prior_and_unknown_timeout_cost(tmp_path: Path) -> None:
    prior = inspect_prior_ledger([])
    store = RunStore(tmp_path)
    store.initialize(_config(), prior)
    ledger = store.load_ledger()
    assert ledger["prior_ledger_status"] == "unverified_missing"
    assert ledger["prior_spent"] is None
    assert ledger["spent_this_run"]["status"] == "unknown"
    key = record_key("feedback-eval-dev-v1", "mock-model", "12", "B0", 0)
    store.put_record(
        key,
        {
            "record_key": key,
            "messages": [{"role": "user", "content": "hi"}],
            "generation": {
                "text": None,
                "usage": None,
                "finish_reason": "timeout",
                "latency_ms": 30000,
                "error": "timeout",
            },
        },
    )
    ledger = store.load_ledger()
    assert ledger["calls"]["timeout"] == 1
    assert ledger["pending_unverified"] == 1
    assert ledger["spent_this_run"]["amount"] is None


def test_budget_lock_serializes_unknown_cost_reservations(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    store.initialize(_config(), inspect_prior_ledger([]))

    def reserve() -> None:
        store.reserve_unknown_cost()

    threads = [threading.Thread(target=reserve) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert store.load_ledger()["pending_unverified"] == 8


def test_records_do_not_contain_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "secret-key")
    store = RunStore(tmp_path)
    store.initialize(_config(), inspect_prior_ledger([]))
    key = record_key("feedback-eval-dev-v1", "mock-model", "1", "B2", 0)
    store.put_record(
        key,
        {
            "record_key": key,
            "settings": {"model": "mock-model", "api_key": "secret-key"},
            "messages": [{"role": "user", "content": "hi"}],
            "generation": _ok_generation(),
        },
    )
    dumped = "".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.json"))
    assert "secret-key" not in dumped
