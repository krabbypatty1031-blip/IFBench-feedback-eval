"""Sensitivity probe for empty B0 at a higher max_tokens.

The helper lives in scripts/ so official freeze hashes stay pinned. Probe
output must never be written into the official store.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from feedback_eval.client import FakeClient


def _probe_mod():
    """Load scripts/probe_empty_b0_max_tokens.py by path. Not a hashed package file."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "probe_empty_b0_max_tokens.py"
    spec = importlib.util.spec_from_file_location("probe_empty_b0_max_tokens", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _empty_b0(*, item_id: str, model: str, max_tokens: int = 4096) -> dict:
    return {
        "accounting_status": "empty_response",
        "condition": "B0",
        "item_id": item_id,
        "messages": [{"role": "user", "content": f"prompt-{item_id}"}],
        "generation": {
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
        },
        "settings": {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "seed": 42,
            "thinking": {"type": "disabled"},
        },
    }


def _write_record(directory: Path, name: str, record: dict) -> None:
    path = directory / name
    path.write_text(json.dumps(record), encoding="utf-8")


def test_selects_only_empty_b0_for_requested_ids(tmp_path: Path) -> None:
    """Success rows and other conditions stay out of the probe set."""
    records = tmp_path / "records"
    records.mkdir()
    _write_record(records, "a.json", _empty_b0(item_id="200", model="gpt-4.1"))
    _write_record(records, "b.json", _empty_b0(item_id="232", model="gpt-4o"))
    success = _empty_b0(item_id="200", model="gpt-4o")
    success["accounting_status"] = "success"
    success["generation"]["text"] = "ok"
    _write_record(records, "c.json", success)
    other_cond = _empty_b0(item_id="200", model="gpt-4.1")
    other_cond["condition"] = "B1"
    other_cond["accounting_status"] = "error"
    _write_record(records, "d.json", other_cond)
    skipped_id = _empty_b0(item_id="249", model="gpt-4.1")
    _write_record(records, "e.json", skipped_id)

    probe = _probe_mod()
    baselines = probe.load_empty_b0_baselines(
        tmp_path, item_ids=["200", "232"], models=["gpt-4.1", "gpt-4o"]
    )
    keys = {(row["model"], row["item_id"]) for row in baselines}
    assert keys == {("gpt-4.1", "200"), ("gpt-4o", "232")}
    assert all(row["messages"][0]["content"].startswith("prompt-") for row in baselines)
    b3 = _empty_b0(item_id="200", model="gpt-4o")
    b3["condition"] = "B3"
    _write_record(records, "f.json", b3)
    later = probe.load_empty_b0_baselines(
        tmp_path, item_ids=["200"], models=["gpt-4o"], condition="B3"
    )
    assert [(row["model"], row["item_id"], row["condition"]) for row in later] == [
        ("gpt-4o", "200", "B3")
    ]


def test_probe_settings_only_raise_max_tokens() -> None:
    """Temperature, seed, and thinking stay frozen; only the output cap changes."""
    probe = _probe_mod()
    settings = probe.build_probe_settings(
        {
            "model": "gpt-4.1",
            "max_tokens": 4096,
            "temperature": 0.0,
            "seed": 42,
            "thinking": {"type": "disabled"},
        },
        max_tokens=8192,
    )
    assert settings["max_tokens"] == 8192
    assert settings["temperature"] == 0.0
    assert settings["seed"] == 42
    assert settings["thinking"] == {"type": "disabled"}
    assert settings["model"] == "gpt-4.1"


def test_run_probes_writes_sidecar_not_official(tmp_path: Path) -> None:
    """FakeClient calls use 8192 and land in the probe directory only."""
    official = tmp_path / "official"
    (official / "records").mkdir(parents=True)
    _write_record(
        official / "records",
        "empty.json",
        _empty_b0(item_id="200", model="gpt-4.1"),
    )
    out_dir = tmp_path / "probe"
    probe = _probe_mod()
    baselines = probe.load_empty_b0_baselines(
        official, item_ids=["200"], models=["gpt-4.1"]
    )
    client = FakeClient(
        [
            {
                "text": "recovered answer",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 200,
                    "completion_tokens_details": {"reasoning_tokens": 50},
                    "total_tokens": 210,
                },
                "finish_reason": "stop",
                "latency_ms": 2.0,
                "error": None,
            }
        ]
    )
    summary = probe.run_probes(
        {"gpt-4.1": client},
        baselines,
        max_tokens=8192,
        out_dir=out_dir,
        official_dir=official,
    )
    assert summary["n_calls"] == 1
    assert summary["n_recovered_text"] == 1
    assert client.calls[0]["settings"]["max_tokens"] == 8192
    assert (out_dir / "summary.json").is_file()
    assert list((official / "records").glob("*.json")) == [official / "records" / "empty.json"]
    official_after = json.loads((official / "records" / "empty.json").read_text())
    assert official_after["accounting_status"] == "empty_response"
    assert official_after["settings"]["max_tokens"] == 4096
