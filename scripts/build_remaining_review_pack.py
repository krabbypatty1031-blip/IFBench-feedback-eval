"""Build a blinded content-review pack for the 220 test items not in freeze n=40.

Does not overwrite runs/feedback_eval/official/review_pack/.
Uses the same overlay records as official analysis.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "runs/feedback_eval/official"
OUT = STORE / "review_pack_remaining_220"


def remaining_item_ids(freeze: dict) -> list[str]:
    """220 leftover ids after the frozen seed-42 draw of 40."""
    split = json.loads((ROOT / "configs/feedback_eval/split.json").read_text())
    test_ids = [str(x) for x in split["test_ids"]]
    selected = [str(x) for x in freeze["human_review"]["item_ids"]]
    rng = random.Random(int(freeze["human_review"]["seed"]))
    pool = list(test_ids)
    rng.shuffle(pool)
    if pool[:40] != selected:
        raise SystemExit("freeze human_review ids do not match seed-42 draw")
    leftover = pool[40:]
    if len(leftover) != 220:
        raise SystemExit(f"expected 220 leftover ids, got {len(leftover)}")
    return leftover


def is_verifier_dump(text: str) -> bool:
    """True when the body is a PASS/FAIL checklist, not a task answer."""
    body = (text or "").strip()
    if not body:
        return False
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    pf = 0
    for ln in lines:
        if re.match(r"^\d+\.\s*(PASS|FAIL)\b", ln, re.I):
            pf += 1
        elif re.match(r"^(PASS|FAIL)\s*:", ln, re.I):
            pf += 1
        elif re.match(r"^Instruction\s+\d+\s*:\s*(PASS|FAIL)\b", ln, re.I):
            pf += 1
    if pf == 0:
        return False
    # Checklist dominates the response.
    return pf >= max(1, int(0.5 * len(lines))) or (pf >= 2 and len(body) < 1200)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-pack", action="store_true")
    args = parser.parse_args()

    from feedback_eval.cli import _prepare_analysis_records
    from feedback_eval.analysis import build_review_pack, write_json as write_analysis_json

    freeze = json.loads((STORE / "freeze.json").read_text())
    item_ids = remaining_item_ids(freeze)
    models = [m["id"] for m in freeze["models"]]
    records, replaced, sources = _prepare_analysis_records(STORE, freeze, None, False)
    pack = build_review_pack(records, item_ids, int(freeze["human_review"]["seed"]), models)
    if pack["n_entries"] != 1760:
        raise SystemExit(f"expected 1760 entries, got {pack['n_entries']}")

    OUT.mkdir(parents=True, exist_ok=True)
    flags = []
    for entry, key in zip(pack["entries"], pack["key"]):
        status = entry.get("accounting_status") or "missing"
        text = entry.get("response") or ""
        n_chars = len(text)
        n_words = len(text.split()) if text.strip() else 0
        row = {
            "review_id": entry["review_id"],
            "status": status,
            "n_chars": n_chars,
            "n_words": n_words,
            "verifier_dump": is_verifier_dump(text),
            "item_id": key["item_id"],
            "model": key["model"],
            "condition": key["condition"],
            "prompt_head": (entry.get("prompt") or "")[:180],
            "response_head": text[:240],
        }
        flags.append(row)

    n_success = sum(1 for r in flags if r["status"] == "success")
    n_fail = len(flags) - n_success
    n_dump = sum(1 for r in flags if r["verifier_dump"])
    n_short = sum(
        1
        for r in flags
        if r["status"] == "success" and not r["verifier_dump"] and r["n_words"] <= 40
    )
    print(f"items={len(item_ids)} entries={pack['n_entries']} overlay_replaced={len(replaced)}")
    print(f"success={n_success} non_success={n_fail} verifier_dump={n_dump} short_le40={n_short}")
    print(f"overlay_sources={sources}")

    if args.write_pack:
        blinded = {k: v for k, v in pack.items() if k != "key"}
        write_analysis_json(OUT / "blinded.json", blinded)
        write_analysis_json(
            OUT / "key.json",
            {"seed": pack["seed"], "n_entries": pack["n_entries"], "key": pack["key"]},
        )
        write_analysis_json(
            OUT / "_inventory.json",
            {
                "n_items": 220,
                "n_entries": 1760,
                "n_success": n_success,
                "n_non_success": n_fail,
                "n_verifier_dump": n_dump,
                "n_short_le40": n_short,
                "item_ids": item_ids,
                "flags": flags,
            },
        )
        print(f"wrote={OUT}")


if __name__ == "__main__":
    main()
