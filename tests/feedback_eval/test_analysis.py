"""Task 8 pairing, denominators, bootstrap, and blinded review pack."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from feedback_eval.analysis import (
    bootstrap_paired_diffs,
    build_review_pack,
    hydrate_overlay_record,
    load_records,
    overlay_records,
    rate,
    summarize,
)
from feedback_eval.cli import main
from feedback_eval.store import record_key


def _success(
    model: str,
    item_id: str,
    condition: str,
    *,
    strict: bool,
    loose: bool | None = None,
) -> dict:
    if loose is None:
        loose = strict
    return {
        "accounting_status": "success",
        "item_id": str(item_id),
        "condition": condition,
        "record_key": record_key("analysis-test", model, item_id, condition, 0),
        "settings": {"model": model, "max_tokens": 4096},
        "generation": {
            "text": "ok",
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            "finish_reason": "stop",
            "latency_ms": 10.0,
            "error": None,
        },
        "score": {
            "strict": {"all_pass": strict, "per_constraint": [strict], "error": None},
            "loose": {"all_pass": loose, "per_constraint": [loose], "error": None},
            "instruction_id_list": ["count:conjunctions"],
        },
        "cost": {"amount": None, "currency": None, "status": "unknown"},
        "messages": [{"role": "user", "content": f"prompt-{item_id}"}],
    }


def _empty(model: str, item_id: str, condition: str) -> dict:
    row = _success(model, item_id, condition, strict=False)
    row["accounting_status"] = "empty_response"
    row["generation"]["text"] = ""
    row["score"]["strict"]["all_pass"] = False
    return row


def test_repair_and_regress_hand_calc_and_null_denominator() -> None:
    """Plan: 2/4 repair, 1/5 regress; unmatched items do not pair; 0/0 is null."""
    records = []
    # Four paired B0 fails; B1 repairs two of them. Item 10 is B1-only and must not pair.
    records.append(_success("m", "1", "B0", strict=False))
    records.append(_success("m", "1", "B1", strict=True))
    records.append(_success("m", "2", "B0", strict=False))
    records.append(_success("m", "2", "B1", strict=True))
    records.append(_success("m", "3", "B0", strict=False))
    records.append(_success("m", "3", "B1", strict=False))
    records.append(_success("m", "4", "B0", strict=False))
    records.append(_success("m", "4", "B1", strict=False))
    # Five B0 passes; B1 regresses one.
    for item_id, b1 in (("5", True), ("6", True), ("7", True), ("8", True), ("9", False)):
        records.append(_success("m", item_id, "B0", strict=True))
        records.append(_success("m", item_id, "B1", strict=b1))
    # Item 10 has B1 only: cannot pair.
    records.append(_success("m", "10", "B1", strict=True))

    summary = summarize(records)
    repair = summary["by_model"]["m"]["repair"]["B1"]
    regress = summary["by_model"]["m"]["regress"]["B1"]
    assert repair == {"n": 2, "d": 4, "value": 0.5}
    assert regress == {"n": 1, "d": 5, "value": 0.2}
    assert "10" not in summary["by_model"]["m"]["paired_item_ids"]["B1"]

    empty = summarize([])
    assert empty["by_model"] == {}
    assert rate(0, 0)["value"] is None
    assert summarize([_success("m", "x", "B0", strict=True)])["by_model"]["m"]["repair"][
        "B1"
    ]["value"] is None


def test_missing_and_empty_stay_out_of_success_denominator() -> None:
    """missing_data_rule: report_missing_do_not_impute."""
    records = [
        _success("m", "1", "B0", strict=True),
        _empty("m", "2", "B0"),
        _success("m", "3", "B0", strict=False),
    ]
    summary = summarize(records)
    b0 = summary["by_model"]["m"]["strict_prompt"]["B0"]
    assert b0 == {"n": 1, "d": 2, "value": 0.5}
    assert summary["accounting"]["success"] == 2
    assert summary["accounting"]["empty_response"] == 1
    assert summary["accounting"]["missing"] == 0


def test_bootstrap_is_deterministic_and_item_clustered() -> None:
    records = []
    for item_id in ("1", "2", "3", "4"):
        records.append(_success("m", item_id, "B0", strict=False))
        records.append(_success("m", item_id, "B2", strict=True))
    first = bootstrap_paired_diffs(records, n_resamples=40, seed=42)
    second = bootstrap_paired_diffs(records, n_resamples=40, seed=42)
    assert first == second
    diff = first["m"]["B2"]["strict_prompt_diff"]
    assert diff["n_units"] == 4
    assert diff["point"] == 1.0
    assert diff["low"] <= diff["point"] <= diff["high"]


def test_review_pack_hides_model_and_condition_and_shuffles() -> None:
    records = [
        _success("gpt-4.1", "11", "B0", strict=True),
        _success("gpt-4o", "11", "B2", strict=False),
        _empty("gpt-4.1", "70", "B3"),
    ]
    pack = build_review_pack(
        records, item_ids=["11", "70"], seed=42, models=["gpt-4.1", "gpt-4o"]
    )
    assert len(pack["entries"]) == 2 * 2 * 4
    visible = json.dumps(pack["entries"])
    assert "gpt-4.1" not in visible
    assert "gpt-4o" not in visible
    assert '"B0"' not in visible
    assert '"B2"' not in visible
    keyed = {row["review_id"]: row for row in pack["key"]}
    assert {row["review_id"] for row in pack["entries"]} == set(keyed)
    sample = pack["entries"][0]
    assert sample["judgment"] is None
    assert sample["review_id"] == keyed[sample["review_id"]]["review_id"]
    assert keyed[sample["review_id"]]["model"] in {"gpt-4.1", "gpt-4o"}
    assert keyed[sample["review_id"]]["condition"] in {"B0", "B1", "B2", "B3"}
    other = build_review_pack(
        records, item_ids=["11", "70"], seed=7, models=["gpt-4.1", "gpt-4o"]
    )
    assert [row["review_id"] for row in pack["entries"]] != [
        row["review_id"] for row in other["entries"]
    ] or [row["prompt"] for row in pack["entries"]] != [
        row["prompt"] for row in other["entries"]
    ]


def test_load_records_and_rebuild_matches_hand_summary(tmp_path: Path) -> None:
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    rows = [
        _success("a", "1", "B0", strict=False),
        _success("a", "1", "B1", strict=True),
    ]
    for row in rows:
        (records_dir / f"{row['record_key']}.json").write_text(
            json.dumps(row), encoding="utf-8"
        )
    loaded = load_records(tmp_path)
    assert len(loaded) == 2
    rebuilt = summarize(loaded)
    assert rebuilt["by_model"]["a"]["repair"]["B1"] == {"n": 1, "d": 1, "value": 1.0}


def test_cli_analyze_and_review_pack_write_store_outputs(
    tmp_path: Path, capsys
) -> None:
    """analyze / review-pack rebuild from records and hide identities in blinded.json."""
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    rows = [
        _success("gpt-4.1", "11", "B0", strict=False),
        _success("gpt-4.1", "11", "B1", strict=True),
        _success("gpt-4.1", "11", "B2", strict=True),
        _success("gpt-4.1", "11", "B3", strict=False),
        _empty("gpt-4o", "70", "B0"),
    ]
    for row in rows:
        (records_dir / f"{row['record_key']}.json").write_text(
            json.dumps(row), encoding="utf-8"
        )
    freeze = {
        "protocol": "analysis-test",
        "freeze_sha256": "abc",
        "parameters": {"max_tokens": 4096},
        "models": [{"id": "gpt-4.1"}, {"id": "gpt-4o"}],
        "human_review": {"item_ids": ["11", "70"], "seed": 42, "n_items": 2},
    }
    (tmp_path / "freeze.json").write_text(json.dumps(freeze), encoding="utf-8")

    assert main(["analyze", "--store-dir", str(tmp_path), "--n-resamples", "20"]) == 0
    analysis = json.loads((tmp_path / "analysis.json").read_text(encoding="utf-8"))
    assert analysis["summary"]["by_model"]["gpt-4.1"]["repair"]["B1"]["n"] == 1
    assert "cost_status=unknown" in capsys.readouterr().out

    assert main(["review-pack", "--store-dir", str(tmp_path)]) == 0
    blinded = json.loads((tmp_path / "review_pack" / "blinded.json").read_text(encoding="utf-8"))
    key = json.loads((tmp_path / "review_pack" / "key.json").read_text(encoding="utf-8"))
    assert "key" not in blinded
    assert blinded["n_entries"] == 16
    assert blinded["single_rater"] is True
    visible = json.dumps(blinded["entries"])
    assert "gpt-4.1" not in visible
    assert '"B0"' not in visible
    assert len(key["key"]) == 16


def test_overlay_replaces_empty_keeps_existing_success() -> None:
    """Recovered text replaces empty/error; a 4096 success is not swapped."""
    base = [
        _empty("m", "1", "B0"),
        _success("m", "2", "B0", strict=True),
    ]
    recovered = _success("m", "1", "B0", strict=False)
    recovered["settings"]["max_tokens"] = 16384
    challenger = _success("m", "2", "B0", strict=False)
    challenger["settings"]["max_tokens"] = 16384
    merged, replaced = overlay_records(base, [("sens", [recovered, challenger])])
    assert merged[0]["settings"]["max_tokens"] == 16384
    assert merged[0]["score"]["strict"]["all_pass"] is False
    assert merged[1]["settings"]["max_tokens"] == 4096
    assert merged[1]["score"]["strict"]["all_pass"] is True
    assert len(replaced) == 1
    assert replaced[0]["item_id"] == "1"


def test_hydrate_probe_repairs_condition_and_accounting(tmp_path: Path) -> None:
    """The 32768 sidecar was written with condition=B0; identity follows the source."""
    src = {
        "condition": "B3",
        "item_id": "200",
        "settings": {"model": "gpt-4o"},
    }
    src_path = tmp_path / "src.json"
    src_path.write_text(json.dumps(src), encoding="utf-8")
    probe = {
        "condition": "B0",
        "item_id": "200",
        "settings": {"model": "gpt-4o", "max_tokens": 32768},
        "generation": {
            "text": "hello",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "finish_reason": "stop",
            "error": None,
            "latency_ms": 1.0,
        },
        "comparison": {"official_source": str(src_path)},
    }
    out = hydrate_overlay_record(probe)
    assert out["condition"] == "B3"
    assert out["accounting_status"] == "success"


_OFFICIAL_STORE = Path("runs/feedback_eval/official")


def test_official_store_rebuilds_ledger_denominators() -> None:
    """Live official records must rebuild without imputing empty/error rows."""
    records_dir = _OFFICIAL_STORE / "records"
    if not records_dir.is_dir():
        pytest.skip("official store not present")
    summary = summarize(load_records(_OFFICIAL_STORE))
    ledger = json.loads((_OFFICIAL_STORE / "ledger.json").read_text(encoding="utf-8"))
    assert summary["accounting"]["success"] == ledger["calls"]["success"]
    assert summary["accounting"]["empty_response"] == ledger["calls"]["empty_response"]
    assert summary["accounting"]["error"] == ledger["calls"]["error"]
    assert summary["by_model"]["gpt-4.1"]["usable_ssss"] > 0
    assert summary["by_model"]["gpt-4o"]["usable_ssss"] > 0
