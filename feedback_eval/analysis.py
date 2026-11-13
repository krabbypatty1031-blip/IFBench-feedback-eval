"""Paired metrics, missing-data reporting, and blinded review packs.

Success denominators never include empty, error, or missing identities.
Repair and regress require a usable B0 and Bx pair for the same model/item.
Cost stays unknown when prices are unverified.
"""

from __future__ import annotations

import json
import logging
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from feedback_eval.client import classify_generation
from feedback_eval.runner import CONDITIONS

logger = logging.getLogger(__name__)

_DEFAULT_BOOTSTRAP = 1000
# Higher rank replaces a weaker official identity. Success never yields to
# another success: the first complete answer stays the reported one.
_STATUS_RANK = {
    "success": 40,
    "missing_usage": 30,
    "truncated": 25,
    "empty_response": 10,
    "error": 5,
    "rate_limit": 5,
    "timeout": 5,
    "unclassified": 0,
    "missing": 0,
}


def rate(n: int, d: int) -> dict[str, Any]:
    """Return n/d, or null when the denominator is zero."""
    if d <= 0:
        return {"n": int(n), "d": int(d), "value": None}
    return {"n": int(n), "d": int(d), "value": float(n) / float(d)}


def load_records(store_dir: Path) -> list[dict[str, Any]]:
    """Load every JSON record under store_dir/records. No imputation."""
    records_dir = Path(store_dir) / "records"
    rows: list[dict[str, Any]] = []
    for path in sorted(records_dir.glob("*.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    logger.info("Loaded %s records from %s", len(rows), records_dir)
    return rows


def _identity(record: dict[str, Any]) -> tuple[str, str, str]:
    return (_model_of(record), str(record.get("item_id")), str(record.get("condition")))


def _status_rank(record: dict[str, Any] | None) -> int:
    if not record:
        return -1
    status = str(record.get("accounting_status") or "unclassified")
    rank = int(_STATUS_RANK.get(status, 0))
    # A scored success is preferred over a probe that still lacks verifiers.
    if status == "success" and (record.get("score") or {}).get("strict"):
        rank += 1
    return rank


def hydrate_overlay_record(
    record: dict[str, Any],
    items_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fill accounting/score and repair probe rows that were saved as B0."""
    rec = dict(record)
    source = (rec.get("comparison") or {}).get("official_source")
    if source:
        src_path = Path(source)
        if not src_path.is_file():
            alt = Path(__file__).resolve().parents[1] / source
            src_path = alt if alt.is_file() else src_path
        if src_path.is_file():
            src = json.loads(src_path.read_text(encoding="utf-8"))
            rec["condition"] = str(src.get("condition") or rec.get("condition"))
            rec["item_id"] = str(src.get("item_id") or rec.get("item_id"))
            rec.setdefault("settings", {})
            if not rec["settings"].get("model"):
                rec["settings"]["model"] = _model_of(src) or _model_of(rec)
            logger.info(
                "Hydrated probe identity model=%s item=%s condition=%s",
                _model_of(rec),
                rec.get("item_id"),
                rec.get("condition"),
            )
    if not rec.get("accounting_status") and rec.get("generation"):
        rec["accounting_status"] = classify_generation(rec.get("generation") or {})
    needs_score = rec.get("accounting_status") == "success" and not rec.get("score")
    if needs_score and items_by_id:
        item = items_by_id.get(str(rec.get("item_id")))
        if item is not None:
            from feedback_eval.scoring import score

            text = str((rec.get("generation") or {}).get("text") or "")
            rec["score"] = score(item, text)
            logger.info(
                "Scored overlay item=%s condition=%s strict=%s",
                rec.get("item_id"),
                rec.get("condition"),
                (rec["score"].get("strict") or {}).get("all_pass"),
            )
    return rec


def overlay_records(
    base: list[dict[str, Any]],
    overlay_groups: list[tuple[str, list[dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Replace official identities when an overlay has strictly better accounting.

    Token caps are ignored. A 4096 success is never replaced by another success.
    Identities that do not exist in `base` are skipped so the 2080 grid stays closed.
    """
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for record in base:
        key = _identity(record)
        by_key[key] = record
        order.append(key)
    replaced: list[dict[str, Any]] = []
    for source, rows in overlay_groups:
        for record in rows:
            key = _identity(record)
            current = by_key.get(key)
            if current is None:
                logger.warning(
                    "Skipping overlay identity not in official grid %s from %s",
                    key,
                    source,
                )
                continue
            if _status_rank(record) <= _status_rank(current):
                continue
            merged = dict(record)
            merged["overlay_source"] = source
            merged["overlay_replaced_status"] = current.get("accounting_status")
            by_key[key] = merged
            replaced.append(
                {
                    "model": key[0],
                    "item_id": key[1],
                    "condition": key[2],
                    "from_status": current.get("accounting_status"),
                    "to_status": record.get("accounting_status"),
                    "from_max_tokens": (current.get("settings") or {}).get("max_tokens"),
                    "to_max_tokens": (record.get("settings") or {}).get("max_tokens"),
                    "source": source,
                }
            )
            logger.info(
                "Overlay replaced %s/%s/%s %s -> %s via %s",
                key[0],
                key[1],
                key[2],
                current.get("accounting_status"),
                record.get("accounting_status"),
                source,
            )
    logger.info("Overlay replaced %s identities", len(replaced))
    return [by_key[key] for key in order], replaced


def _model_of(record: dict[str, Any]) -> str:
    return str((record.get("settings") or {}).get("model") or record.get("model") or "")


def _strict_pass(record: dict[str, Any] | None) -> bool | None:
    if not record or record.get("accounting_status") != "success":
        return None
    return bool(((record.get("score") or {}).get("strict") or {}).get("all_pass"))


def _loose_pass(record: dict[str, Any] | None) -> bool | None:
    if not record or record.get("accounting_status") != "success":
        return None
    return bool(((record.get("score") or {}).get("loose") or {}).get("all_pass"))


def _index_by_model_item(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, dict]]:
    by: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for record in records:
        by[(_model_of(record), str(record.get("item_id")))][str(record.get("condition"))] = record
    return by


def _accounting(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {
        "success": 0,
        "empty_response": 0,
        "error": 0,
        "rate_limit": 0,
        "timeout": 0,
        "truncated": 0,
        "missing_usage": 0,
        "missing": 0,
    }
    for record in records:
        status = str(record.get("accounting_status") or "unclassified")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _constraint_rate(success_rows: list[dict[str, Any]], which: str) -> dict[str, Any]:
    n_pass = 0
    n_total = 0
    for record in success_rows:
        block = (record.get("score") or {}).get(which) or {}
        flags = block.get("per_constraint")
        if not isinstance(flags, list):
            flags = [bool(block.get("all_pass"))]
        n_total += len(flags)
        n_pass += sum(1 for flag in flags if flag)
    return rate(n_pass, n_total)


def _latency_tokens(success_rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = []
    prompt = []
    completion = []
    reasoning = []
    for record in success_rows:
        generation = record.get("generation") or {}
        if generation.get("latency_ms") is not None:
            latencies.append(float(generation["latency_ms"]))
        usage = generation.get("usage") or {}
        if usage.get("prompt_tokens") is not None:
            prompt.append(int(usage["prompt_tokens"]))
        if usage.get("completion_tokens") is not None:
            completion.append(int(usage["completion_tokens"]))
        details = usage.get("completion_tokens_details") or {}
        if details.get("reasoning_tokens") is not None:
            reasoning.append(int(details["reasoning_tokens"]))
    return {
        "latency_ms_n": len(latencies),
        "latency_ms_mean": (sum(latencies) / len(latencies)) if latencies else None,
        "prompt_tokens_sum": sum(prompt) if prompt else None,
        "completion_tokens_sum": sum(completion) if completion else None,
        "reasoning_tokens_sum": sum(reasoning) if reasoning else None,
        "cost": {"amount": None, "currency": None, "status": "unknown"},
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Paired strict/loose rates, repair/regress, and accounting. Missing stays missing."""
    by = _index_by_model_item(records)
    models = sorted({model for model, _ in by})
    by_model: dict[str, Any] = {}
    for model in models:
        items = sorted({item_id for m, item_id in by if m == model})
        strict_prompt: dict[str, Any] = {}
        loose_prompt: dict[str, Any] = {}
        repair: dict[str, Any] = {}
        regress: dict[str, Any] = {}
        paired_ids: dict[str, list[str]] = {}
        usable_ssss = 0
        success_rows: list[dict[str, Any]] = []
        n_constraints_explore: dict[str, dict[str, int]] = {
            "single": {"pass": 0, "n": 0},
            "double": {"pass": 0, "n": 0},
        }
        for item_id in items:
            conds = by[(model, item_id)]
            if all(conds.get(c, {}).get("accounting_status") == "success" for c in CONDITIONS):
                usable_ssss += 1
            b0 = conds.get("B0")
            b0_ok = _strict_pass(b0)
            for condition in CONDITIONS:
                rec = conds.get(condition)
                if rec and rec.get("accounting_status") == "success":
                    success_rows.append(rec)
                    if condition == "B0":
                        n_ids = rec.get("score", {}).get("instruction_id_list") or []
                        bucket = "double" if len(n_ids) >= 2 else "single"
                        n_constraints_explore[bucket]["n"] += 1
                        if b0_ok:
                            n_constraints_explore[bucket]["pass"] += 1
            for condition in CONDITIONS:
                rec = conds.get(condition)
                passed = _strict_pass(rec)
                loose = _loose_pass(rec)
                strict_prompt.setdefault(condition, {"n": 0, "d": 0})
                loose_prompt.setdefault(condition, {"n": 0, "d": 0})
                if passed is not None:
                    strict_prompt[condition]["d"] += 1
                    strict_prompt[condition]["n"] += int(passed)
                if loose is not None:
                    loose_prompt[condition]["d"] += 1
                    loose_prompt[condition]["n"] += int(loose)
            for condition in ("B1", "B2", "B3"):
                bx = conds.get(condition)
                b0_flag = _strict_pass(b0)
                bx_flag = _strict_pass(bx)
                repair.setdefault(condition, {"n": 0, "d": 0})
                regress.setdefault(condition, {"n": 0, "d": 0})
                paired_ids.setdefault(condition, [])
                if b0_flag is None or bx_flag is None:
                    continue
                paired_ids[condition].append(item_id)
                if b0_flag is False:
                    repair[condition]["d"] += 1
                    repair[condition]["n"] += int(bx_flag is True)
                if b0_flag is True:
                    regress[condition]["d"] += 1
                    regress[condition]["n"] += int(bx_flag is False)
        by_model[model] = {
            "n_items": len(items),
            "usable_ssss": usable_ssss,
            "strict_prompt": {
                condition: rate(vals["n"], vals["d"])
                for condition, vals in strict_prompt.items()
            },
            "loose_prompt": {
                condition: rate(vals["n"], vals["d"])
                for condition, vals in loose_prompt.items()
            },
            "strict_constraint": _constraint_rate(success_rows, "strict"),
            "loose_constraint": _constraint_rate(success_rows, "loose"),
            "repair": {
                condition: rate(vals["n"], vals["d"]) for condition, vals in repair.items()
            },
            "regress": {
                condition: rate(vals["n"], vals["d"]) for condition, vals in regress.items()
            },
            "paired_item_ids": paired_ids,
            "explore_n_constraints_b0": {
                name: rate(vals["pass"], vals["n"])
                for name, vals in n_constraints_explore.items()
            },
            "usage": _latency_tokens(success_rows),
        }
    return {
        "n_records": len(records),
        "accounting": _accounting(records),
        "missing_data_rule": "report_missing_do_not_impute",
        "by_model": by_model,
        "cost": {"amount": None, "currency": None, "status": "unknown"},
    }


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (p / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def bootstrap_paired_diffs(
    records: list[dict[str, Any]],
    n_resamples: int = _DEFAULT_BOOTSTRAP,
    seed: int = 42,
    groups: list[list[str]] | None = None,
) -> dict[str, Any]:
    """Item- or homology-clustered bootstrap of Bx minus B0 strict prompt rates."""
    by = _index_by_model_item(records)
    group_of: dict[str, str] = {}
    for index, ids in enumerate(groups or []):
        label = f"g{index}"
        for item_id in ids:
            group_of[str(item_id)] = label
    out: dict[str, Any] = {}
    rng = random.Random(seed)
    for model in sorted({m for m, _ in by}):
        # Cluster usable pairs by homology group, else by item id.
        clustered: dict[str, dict[str, list[tuple[bool, bool]]]] = defaultdict(
            lambda: {c: [] for c in ("B1", "B2", "B3")}
        )
        items = sorted({item_id for m, item_id in by if m == model})
        for item_id in items:
            conds = by[(model, item_id)]
            b0 = _strict_pass(conds.get("B0"))
            cluster = group_of.get(item_id, f"item:{item_id}")
            for condition in ("B1", "B2", "B3"):
                bx = _strict_pass(conds.get(condition))
                if b0 is None or bx is None:
                    continue
                clustered[cluster][condition].append((b0, bx))
        model_block: dict[str, Any] = {}
        for condition in ("B1", "B2", "B3"):
            unit_ids = [cid for cid, payload in clustered.items() if payload[condition]]
            n_pairs = sum(len(clustered[cid][condition]) for cid in unit_ids)

            def _diff(unit_list: list[str], cond: str = condition) -> float | None:
                n_b0 = n_bx = d = 0
                for cid in unit_list:
                    for b0_flag, bx_flag in clustered[cid][cond]:
                        d += 1
                        n_b0 += int(b0_flag)
                        n_bx += int(bx_flag)
                if d == 0:
                    return None
                return n_bx / d - n_b0 / d

            point = _diff(unit_ids)
            draws: list[float] = []
            if unit_ids:
                for _ in range(int(n_resamples)):
                    sample = [rng.choice(unit_ids) for _ in unit_ids]
                    value = _diff(sample)
                    if value is not None:
                        draws.append(value)
            model_block[condition] = {
                "strict_prompt_diff": {
                    "n_units": len(unit_ids),
                    "n_pairs": n_pairs,
                    "point": point,
                    "low": _percentile(draws, 2.5),
                    "high": _percentile(draws, 97.5),
                    "n_resamples": int(n_resamples),
                    "seed": int(seed),
                }
            }
        out[model] = model_block
    return out


def build_review_pack(
    records: list[dict[str, Any]],
    item_ids: list[str],
    seed: int,
    models: list[str],
) -> dict[str, Any]:
    """Shuffle frozen review identities; hide model and condition from the rater."""
    by = _index_by_model_item(records)
    prompt_by_item: dict[str, str] = {}
    for record in records:
        item_id = str(record.get("item_id"))
        if item_id in prompt_by_item:
            continue
        messages = record.get("messages") or []
        if messages and messages[0].get("content"):
            prompt_by_item[item_id] = str(messages[0]["content"])
    slots: list[dict[str, Any]] = []
    for item_id in item_ids:
        for model in models:
            for condition in CONDITIONS:
                rec = by.get((model, str(item_id)), {}).get(condition)
                text = ""
                status = "missing"
                if rec is not None:
                    status = str(rec.get("accounting_status") or "unclassified")
                    text = str((rec.get("generation") or {}).get("text") or "")
                slots.append(
                    {
                        "item_id": str(item_id),
                        "model": model,
                        "condition": condition,
                        "prompt": prompt_by_item.get(str(item_id), ""),
                        "response": text,
                        "accounting_status": status,
                    }
                )
    rng = random.Random(int(seed))
    order = list(range(len(slots)))
    rng.shuffle(order)
    entries = []
    key = []
    for index, slot_i in enumerate(order, start=1):
        slot = slots[slot_i]
        review_id = f"R{index:03d}"
        entries.append(
            {
                "review_id": review_id,
                "prompt": slot["prompt"],
                "response": slot["response"],
                "accounting_status": slot["accounting_status"],
                "judgment": None,
                "notes": None,
            }
        )
        key.append(
            {
                "review_id": review_id,
                "item_id": slot["item_id"],
                "model": slot["model"],
                "condition": slot["condition"],
            }
        )
    logger.info(
        "Built blinded review pack n=%s seed=%s n_items=%s",
        len(entries),
        seed,
        len(item_ids),
    )
    return {
        "seed": int(seed),
        "n_items": len(item_ids),
        "n_entries": len(entries),
        "labels": ["complete", "partial", "incomplete", "cannot_judge"],
        "single_rater": True,
        "entries": entries,
        "key": key,
        "note": (
            "Single-rater content check against the original task. "
            "Not a second verifier and not inter-rater reliability."
        ),
    }


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
