"""Label the remaining 220-item blinded pack with the same content rubric as n=40.

Does not overwrite review_pack/. Empty or error text is cannot_judge. Verifier
checklists and copy-span-only replies are incomplete. The original user task
is judged, not IFBench constraint following.
"""
from __future__ import annotations

import collections
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "runs/feedback_eval/official"
PACK = STORE / "review_pack_remaining_220"
ORIG = STORE / "review_pack"
OUT_REPORT = ROOT / "outputs/report_materials"
CONDITIONS = ("B0", "B1", "B2", "B3")

# Hand labels for the few success rows that are not complete. Everything else
# with usable text is complete, matching the n=40 pack.
MANUAL = {
    # Verifier PASS line only; no task answer.
    "R834": ("incomplete", "Verifier PASS line only; no definitions of preludes/caprichos/romances/ballads."),
    "R1056": ("incomplete", "Verifier PASS line only; no screenplay."),
    # Item 287: copied a prompt span; did not explain the quadratic question.
    "R1460": ("incomplete", "Copied a prompt span; no method for the quadratic question."),
    "R1690": ("incomplete", "Copied a prompt span; no method for the quadratic question."),
    "R333": ("incomplete", "Copied a prompt span; no method for the quadratic question."),
    "R1261": ("incomplete", "Copied a prompt span; no method for the quadratic question."),
    "R350": ("incomplete", "Copied a prompt span; no method for the quadratic question."),
    "R050": ("incomplete", "Copied a prompt span; no method for the quadratic question."),
    "R1171": ("incomplete", "Copied a prompt span; no method for the quadratic question."),
    "R162": ("incomplete", "Copied a prompt span; no method for the quadratic question."),
    # Item 284: copied a prompt span; no paper explanation.
    "R232": ("incomplete", "Copied a prompt span; no explanation of the paper."),
    "R013": ("incomplete", "Copied a prompt span; no explanation of the paper."),
    # Item 286: copied words 0-7; no added appealing sentence.
    "R1355": ("incomplete", "Copied a prompt span; no sentence making the house appealing."),
    "R817": ("incomplete", "Copied a prompt span; no sentence making the house appealing."),
    "R164": ("incomplete", "Copied a prompt span; no sentence making the house appealing."),
    # Item 251 SAT blank: off-topic two-word string.
    "R1340": ("incomplete", "Off-topic 'Two works.'; does not complete the SAT blank."),
    # Item 251: one-word attempt that is not a listed choice.
    "R106": ("partial", "One-word 'Due.' is on-topic but does not select a listed choice."),
}


def content_rank(label: str) -> int:
    """Order labels for paired repair/regress: higher is more complete."""
    return {"cannot_judge": -1, "incomplete": 0, "partial": 1, "complete": 2}[label]


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def summarize(rows: list[dict], n_items: int) -> dict:
    labels = collections.Counter(r["label"] for r in rows)
    by_condition = collections.defaultdict(collections.Counter)
    by_model = collections.defaultdict(collections.Counter)
    vs_strict = collections.defaultdict(lambda: {"n": 0, "strict_pass": 0, "strict_fail": 0, "no_score": 0})
    non_complete = []
    for row in rows:
        by_condition[row["condition"]][row["label"]] += 1
        by_model[row["model"]][row["label"]] += 1
        block = vs_strict[row["label"]]
        block["n"] += 1
        if row.get("strict") is True:
            block["strict_pass"] += 1
        elif row.get("strict") is False:
            block["strict_fail"] += 1
        else:
            block["no_score"] += 1
        if row["label"] != "complete":
            non_complete.append(
                {
                    "review_id": row["review_id"],
                    "item_id": row["item_id"],
                    "model": row["model"],
                    "condition": row["condition"],
                    "label": row["label"],
                    "notes": row["notes"],
                    "strict": row.get("strict"),
                }
            )
    paired = {}
    by_id = {(r["model"], r["item_id"]): {} for r in rows}
    for row in rows:
        by_id[(row["model"], row["item_id"])][row["condition"]] = row
    for cond in CONDITIONS[1:]:
        n_pairs = repair = regress = same = 0
        for ident, conds in by_id.items():
            b0 = conds.get("B0")
            bx = conds.get(cond)
            if not b0 or not bx:
                continue
            if b0["label"] == "cannot_judge" or bx["label"] == "cannot_judge":
                continue
            n_pairs += 1
            r0, rx = content_rank(b0["label"]), content_rank(bx["label"])
            if rx > r0:
                repair += 1
            elif rx < r0:
                regress += 1
            else:
                same += 1
        paired[cond] = {"n_pairs": n_pairs, "repair": repair, "regress": regress, "same": same}
    n = len(rows)
    return {
        "n": n,
        "n_items": n_items,
        "labels": dict(labels),
        "complete_rate_among_judged": (
            labels["complete"] / (n - labels.get("cannot_judge", 0))
            if n > labels.get("cannot_judge", 0)
            else None
        ),
        "by_condition": {c: dict(by_condition[c]) for c in CONDITIONS},
        "by_model": {m: dict(by_model[m]) for m in sorted(by_model)},
        "vs_strict": dict(vs_strict),
        "content_paired": paired,
        "non_complete": non_complete,
        "criterion": "original_user_task_not_ifbench_constraints",
        "single_rater": True,
    }


def main() -> None:
    from feedback_eval.cli import _prepare_analysis_records
    from feedback_eval.analysis import _model_of, _strict_pass

    freeze = json.loads((STORE / "freeze.json").read_text())
    records, replaced, _ = _prepare_analysis_records(STORE, freeze, None, False)
    rec_idx = {(_model_of(r), str(r["item_id"]), r["condition"]): r for r in records}
    blinded = json.loads((PACK / "blinded.json").read_text())
    keys = json.loads((PACK / "key.json").read_text())["key"]
    key_by = {k["review_id"]: k for k in keys}
    labeled = []
    for entry in blinded["entries"]:
        rid = entry["review_id"]
        k = key_by[rid]
        status = entry.get("accounting_status") or "missing"
        rec = rec_idx.get((k["model"], str(k["item_id"]), k["condition"]))
        strict = None
        if rec is not None and rec.get("accounting_status") == "success":
            strict = bool(_strict_pass(rec))
        if status != "success" or not (entry.get("response") or "").strip():
            label, notes = "cannot_judge", f"No usable response ({status})."
        elif rid in MANUAL:
            label, notes = MANUAL[rid]
        else:
            label, notes = "complete", ""
        entry["judgment"] = label
        entry["notes"] = notes or None
        labeled.append(
            {
                "review_id": rid,
                "label": label,
                "notes": notes,
                "item_id": str(k["item_id"]),
                "model": k["model"],
                "condition": k["condition"],
                "strict": strict,
                "accounting_status": status,
            }
        )
    expected_manual = set(MANUAL)
    applied = {r["review_id"] for r in labeled if r["review_id"] in MANUAL}
    if applied != expected_manual:
        raise SystemExit(f"manual id mismatch missing={expected_manual-applied}")

    with (PACK / "judgments.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["review_id", "label", "notes"])
        for row in labeled:
            writer.writerow([row["review_id"], row["label"], row["notes"]])
    write_json(PACK / "blinded.json", blinded)
    remaining_summary = summarize(labeled, 220)
    remaining_summary.update(
        {
            "pack": "remaining_220",
            "seed": 42,
            "overlay_n_replaced_store": len(replaced),
            "note": (
                "Additional single-rater labels for the 220 test items outside "
                "the frozen n=40 sample. Does not replace the protocol sample."
            ),
        }
    )
    write_json(PACK / "judgment_summary.json", remaining_summary)

    orig_labels = list(csv.DictReader((ORIG / "judgments.csv").open()))
    orig_keys = {k["review_id"]: k for k in json.loads((ORIG / "key.json").read_text())["key"]}
    orig_rows = []
    for row in orig_labels:
        k = orig_keys[row["review_id"]]
        rec = rec_idx[(k["model"], str(k["item_id"]), k["condition"])]
        orig_rows.append(
            {
                "review_id": "orig-" + row["review_id"],
                "label": row["label"],
                "notes": row.get("notes") or "",
                "item_id": str(k["item_id"]),
                "model": k["model"],
                "condition": k["condition"],
                "strict": bool(_strict_pass(rec)) if rec.get("accounting_status") == "success" else None,
                "pack": "frozen_n40",
            }
        )
    rem_rows = [{**r, "review_id": "rem-" + r["review_id"], "pack": "remaining_220"} for r in labeled]
    combined = orig_rows + rem_rows
    combined_summary = summarize(combined, 260)
    combined_summary.update(
        {
            "n_frozen_sample": 320,
            "n_remaining": 1760,
            "packs": {
                "frozen_n40": remaining_summary and summarize(orig_rows, 40),
                "remaining_220": {k: remaining_summary[k] for k in ("n", "n_items", "labels", "by_condition", "vs_strict")},
            },
            "note": (
                "2080 labels = frozen 40-item sample plus the other 220 test items. "
                "Single rater; not inter-rater reliability."
            ),
        }
    )
    write_json(OUT_REPORT / "content_review_remaining_220.json", remaining_summary)
    write_json(OUT_REPORT / "content_review_all_260.json", combined_summary)
    with (OUT_REPORT / "content_review_remaining_220.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["review_id", "item_id", "model", "condition", "label", "notes", "strict"],
        )
        writer.writeheader()
        for row in labeled:
            writer.writerow({k: row[k] for k in writer.fieldnames})
    print(
        "remaining",
        remaining_summary["labels"],
        "combined",
        combined_summary["labels"],
    )


if __name__ == "__main__":
    main()
