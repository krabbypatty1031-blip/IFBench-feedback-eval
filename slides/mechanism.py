"""Appendix script: how often each revision condition rewrites or keeps the B0 answer.

Purpose
    Explains why B2 beats B1 (slide 8): B1 rewrites most answers that already
    passed at B0, while B2/B3 mostly keep them. Read-only; no API calls.

Inputs (all read-only, relative to the IFBench repository root)
    runs/feedback_eval/official/records/*.json        official 4096-token run (2080 records)
    runs/feedback_eval/official/freeze.json           frozen protocol (selects the default overlays)
    runs/feedback_eval/sensitivity_empty_b0_16384/    rerun overlay (empty B0 -> 16384 tokens)
    runs/feedback_eval/probe_b3_32768/                rerun overlay (B3 -> 32768 tokens)
    runs/feedback_eval/retry_recovered_20261116/      remaining empty/error recoveries (16384/32768)
    data/IFBench_test.jsonl                           official items, used to score overlay probes
    The merge is the project's own one (feedback_eval.cli._prepare_analysis_records ->
    analysis.overlay_records): an official identity is replaced only if the rerun has
    strictly better accounting; a 4096-token success is never replaced. This is the same
    merged set behind RESULTS_TABLES.md.

Definitions
    Unit: one (model, item) pair where both B0 and the revision Bx are scorable
          (accounting_status == "success"); missing answers are excluded, not imputed.
    B0 pass / fail: official strict prompt-level verdict on the B0 answer (score.strict.all_pass).
    kept:      Bx answer text == B0 answer text after str.strip().
    rewritten: any other Bx answer (even a one-character change).
    regressed: B0 pass and Bx strict fail (matches Table 4 regression numerators).
    repaired:  B0 fail and Bx strict pass (matches Table 4 repair numerators).

How to run (from the IFBench repository root, same environment as the main analysis;
nltk 3.9.2 and emoji 2.15.0 as pinned in uv.lock)
    NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python scripts/mechanism.py \
        [--out outputs/report_materials/mechanism.json]

Output
    JSON keyed "<model>/<condition>" for gpt-4.1 and gpt-4o, conditions B1-B3, with counts
    b0_pass_kept, b0_pass_rewritten, b0_fail_kept, b0_fail_rewritten, regressed, repaired,
    plus the merge's n_replaced for traceability.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))

from feedback_eval.cli import _prepare_analysis_records  # noqa: E402

MODELS = ("gpt-4.1", "gpt-4o")
REVISIONS = ("B1", "B2", "B3")


def _text(rec: dict) -> str:
    return str((rec.get("generation") or {}).get("text") or "")


def _ok(rec: dict | None) -> bool:
    return bool(rec) and rec.get("accounting_status") == "success" and bool(
        ((rec.get("score") or {}).get("strict") or {}).get("all_pass") is not None
    )


def _model(rec: dict) -> str:
    return str((rec.get("settings") or {}).get("model") or rec.get("model"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="outputs/report_materials/mechanism.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    store = ROOT / "runs" / "feedback_eval" / "official"
    freeze = json.loads((store / "freeze.json").read_text(encoding="utf-8"))
    merged, replaced, _ = _prepare_analysis_records(store, freeze, None, False)
    by = {(_model(r), str(r["item_id"]), str(r["condition"])): r for r in merged}

    out: dict = {"_meta": {"n_records": len(merged), "n_replaced_by_overlay": len(replaced),
                           "kept_definition": "Bx text == B0 text after strip()"}}
    for m in MODELS:
        items = sorted({i for (mm, i, _c) in by if mm == m}, key=int)
        for c in REVISIONS:
            s = dict.fromkeys(["b0_pass_kept", "b0_pass_rewritten", "b0_fail_kept",
                               "b0_fail_rewritten", "regressed", "repaired"], 0)
            for i in items:
                b0, bx = by.get((m, i, "B0")), by.get((m, i, c))
                if not (_ok(b0) and _ok(bx)):
                    continue
                p0 = bool(b0["score"]["strict"]["all_pass"])
                px = bool(bx["score"]["strict"]["all_pass"])
                kept = _text(bx).strip() == _text(b0).strip()
                s[f"b0_{'pass' if p0 else 'fail'}_{'kept' if kept else 'rewritten'}"] += 1
                s["regressed"] += int(p0 and not px)
                s["repaired"] += int((not p0) and px)
            s["b0_pass_total"] = s["b0_pass_kept"] + s["b0_pass_rewritten"]
            s["b0_fail_total"] = s["b0_fail_kept"] + s["b0_fail_rewritten"]
            out[f"{m}/{c}"] = s

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
