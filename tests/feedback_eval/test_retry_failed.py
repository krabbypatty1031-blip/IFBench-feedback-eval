"""Quarantine non-success identities so official resume can regenerate them.

The helper lives in scripts/ so student-package hashes (and freeze.json) stay
pinned. Success rows must not move; the ledger is recounted from remaining
files rather than subtracted by hand.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from feedback_eval.client import FakeClient
from feedback_eval.execution import run_schedule
from feedback_eval.runner import synthetic_conjunction_item
from feedback_eval.store import RunStore, inspect_prior_ledger, record_key


def _retry_mod():
    """Load scripts/retry_official_failed.py by path. Not a hashed package file."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "retry_official_failed.py"
    spec = importlib.util.spec_from_file_location("retry_official_failed", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ok() -> dict:
    return {
        "text": "hello and but so",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "finish_reason": "stop",
        "latency_ms": 1.0,
        "error": None,
    }


def _err(message: str = "HTTP 400: empty") -> dict:
    return {
        "text": "",
        "usage": None,
        "finish_reason": "error",
        "latency_ms": 1.0,
        "error": message,
    }


def _store(tmp_path: Path) -> RunStore:
    store = RunStore(tmp_path)
    store.initialize(
        {
            "protocol": "retry-test",
            "data_sha256": "synthetic",
            "model": "a,b",
            "prompt_sha256": "synthetic",
            "code_version": "v1",
            "parameters": {"temperature": 0.0},
        },
        inspect_prior_ledger([]),
    )
    return store


def test_quarantine_moves_failed_keeps_success_and_rebuilds_ledger(tmp_path: Path) -> None:
    retry = _retry_mod()
    store = _store(tmp_path)
    protocol = "retry-test"
    success_key = record_key(protocol, "a", "1", "B0", 0)
    failed_key = record_key(protocol, "a", "1", "B1", 0)
    store.put_record(
        success_key,
        {
            "record_key": success_key,
            "item_id": "1",
            "condition": "B0",
            "repeat": 0,
            "messages": [{"role": "user", "content": "hi"}],
            "generation": _ok(),
            "settings": {"model": "a"},
        },
    )
    store.put_record(
        failed_key,
        {
            "record_key": failed_key,
            "item_id": "1",
            "condition": "B1",
            "repeat": 0,
            "messages": [{"role": "user", "content": "hi"}],
            "generation": _err(),
            "settings": {"model": "a"},
        },
    )
    assert store.load_ledger()["calls"]["success"] == 1
    assert store.load_ledger()["calls"]["error"] == 1

    manifest = retry.quarantine_failed_records(tmp_path, stamp="testrun")
    assert manifest["n_moved"] == 1
    assert store.get_record(success_key) is not None
    assert store.get_record(failed_key) is None
    archived = list((tmp_path / "failed_archive" / "testrun" / "records").glob("*.json"))
    assert len(archived) == 1
    saved = json.loads(archived[0].read_text(encoding="utf-8"))
    assert saved["record_key"] == failed_key
    assert saved["accounting_status"] == "error"

    ledger = store.load_ledger()
    assert ledger["calls"]["success"] == 1
    assert ledger["calls"]["error"] == 0
    assert ledger["calls"]["empty_response"] == 0


def test_quarantine_is_noop_when_all_success(tmp_path: Path) -> None:
    retry = _retry_mod()
    store = _store(tmp_path)
    key = record_key("retry-test", "a", "1", "B0", 0)
    store.put_record(
        key,
        {
            "record_key": key,
            "item_id": "1",
            "condition": "B0",
            "repeat": 0,
            "messages": [{"role": "user", "content": "hi"}],
            "generation": _ok(),
            "settings": {"model": "a"},
        },
    )
    manifest = retry.quarantine_failed_records(tmp_path, stamp="noop")
    assert manifest["n_moved"] == 0
    assert store.get_record(key) is not None
    assert store.load_ledger()["calls"]["success"] == 1


def test_schedule_regenerates_only_quarantined_identities(tmp_path: Path) -> None:
    retry = _retry_mod()
    store = _store(tmp_path)
    items = [synthetic_conjunction_item("1")]
    models = ["a", "b"]
    config = {
        "protocol": "retry-test",
        "data_sha256": "synthetic",
        "model": "a,b",
        "prompt_sha256": "synthetic",
        "code_version": "v1",
        "parameters": {"temperature": 0.0},
        "store": store,
        "repeat": 0,
    }
    mixed = FakeClient([_ok(), _err(), _ok(), _ok(), _ok(), _ok(), _ok(), _ok()])
    first = run_schedule(items, models, {"a": mixed, "b": mixed}, config)
    assert first["statuses"]["success"] == 7
    assert first["statuses"]["error"] == 1
    retry.quarantine_failed_records(tmp_path, stamp="resume")
    after = retry.rebuild_ledger_from_records(tmp_path)
    assert after["calls"]["error"] == 0
    missing = run_schedule(
        items,
        models,
        {"a": FakeClient([_ok()]), "b": FakeClient([])},
        config,
    )
    assert missing["calls_this_invocation"] == 1
    assert missing["statuses"]["success"] == 8
    assert missing["missing"] == 0
