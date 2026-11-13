"""16384 sensitivity rerun of official empty-B0 pairs.

The helper lives in scripts/ so the official freeze code hash stays pinned.
The 4096 official store must remain read-only.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from feedback_eval.client import FakeClient
from feedback_eval.runner import synthetic_conjunction_item
from feedback_eval.store import RunStore, inspect_prior_ledger, record_key


def _mod():
    """Load scripts/run_empty_b0_16384.py by path. Not a hashed package file."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_empty_b0_16384.py"
    spec = importlib.util.spec_from_file_location("run_empty_b0_16384", path)
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


def _empty() -> dict:
    return {
        "text": "",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 4096,
            "completion_tokens_details": {"reasoning_tokens": 4096},
            "total_tokens": 4106,
        },
        "finish_reason": "stop",
        "latency_ms": 1.0,
        "error": None,
    }


def _write(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record), encoding="utf-8")


def test_selects_only_empty_b0_model_item_pairs(tmp_path: Path) -> None:
    """Successful B0 and later-condition empties without empty B0 are skipped."""
    records = tmp_path / "records"
    records.mkdir()
    _write(
        records / "a.json",
        {
            "accounting_status": "empty_response",
            "condition": "B0",
            "item_id": "200",
            "settings": {"model": "gpt-4.1"},
        },
    )
    _write(
        records / "b.json",
        {
            "accounting_status": "success",
            "condition": "B0",
            "item_id": "200",
            "settings": {"model": "gpt-4o"},
        },
    )
    _write(
        records / "c.json",
        {
            "accounting_status": "empty_response",
            "condition": "B1",
            "item_id": "168",
            "settings": {"model": "gpt-4.1"},
        },
    )
    pairs = _mod().select_empty_b0_pairs(tmp_path)
    assert pairs == [("gpt-4.1", "200")]


def test_schedule_reruns_only_selected_pairs_at_16384(tmp_path: Path) -> None:
    """The already-good model is not regenerated; official records stay put."""
    official = tmp_path / "official"
    (official / "records").mkdir(parents=True)
    _write(
        official / "records" / "empty.json",
        {
            "accounting_status": "empty_response",
            "condition": "B0",
            "item_id": "1",
            "settings": {"model": "a", "max_tokens": 4096},
        },
    )
    out_dir = tmp_path / "sensitivity"
    store = RunStore(out_dir)
    store.initialize(
        {
            "protocol": "sensitivity-test",
            "data_sha256": "synthetic",
            "model": "a,b",
            "prompt_sha256": "synthetic",
            "code_version": "v1",
            "parameters": {"max_tokens": 16384, "temperature": 0.0, "seed": 42},
        },
        inspect_prior_ledger([]),
    )
    items = [synthetic_conjunction_item("1")]
    client_a = FakeClient([_ok(), _ok(), _ok(), _ok()])
    client_b = FakeClient([])
    summary = _mod().run_selected_pairs(
        pairs=[("a", "1")],
        items=items,
        models=["a", "b"],
        clients={"a": client_a, "b": client_b},
        config={
            "protocol": "sensitivity-test",
            "store": store,
            "repeat": 0,
            "parameters": {"max_tokens": 16384, "temperature": 0.0, "seed": 42},
        },
        out_dir=out_dir,
        official_dir=official,
    )
    assert summary["n_pairs"] == 1
    assert summary["expected"] == 4
    assert summary["calls_this_invocation"] == 4
    assert summary["success"] == 4
    assert client_a.calls[0]["settings"]["max_tokens"] == 16384
    assert client_b.calls == []
    official_after = json.loads((official / "records" / "empty.json").read_text())
    assert official_after["settings"]["max_tokens"] == 4096
    assert official_after["accounting_status"] == "empty_response"
    key = record_key("sensitivity-test", "a", "1", "B0", 0)
    saved = store.get_record(key)
    assert saved is not None
    assert saved["settings"]["max_tokens"] == 16384
