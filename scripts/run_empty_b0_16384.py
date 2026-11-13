#!/usr/bin/env python3
"""Rerun official empty-B0 (model, item) pairs at max_tokens=16384.

Lives outside feedback_eval/*.py so the 4096 official freeze hash does not
change. Writes a new protocol/store. Official records are read-only. This is a
sensitivity experiment, not a continuation of feedback-eval-official-hkbu-v1.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from feedback_eval.cli import (  # noqa: E402
    _http_client_label,
    _write_execution_summary,
    build_experiment_freeze,
    load_split_items,
)
from feedback_eval.client import build_http_client, classify_generation  # noqa: E402
from feedback_eval.environment import apply_env_models, load_dotenv_file  # noqa: E402
from feedback_eval.execution import CallCappedClient  # noqa: E402
from feedback_eval.runner import CONDITIONS, load_prompt_templates, run_item  # noqa: E402
from feedback_eval.store import (  # noqa: E402
    RunStore,
    StoreError,
    _atomic_write_json,
    call_cap_is_confirmed,
    default_prior_ledger_candidates,
    inspect_prior_ledger,
    load_mapping_yaml,
    record_key,
)

logger = logging.getLogger(__name__)

_SENSITIVITY_CONFIG = _REPO_ROOT / "configs" / "feedback_eval" / "sensitivity_empty_b0_16384.yaml"
DEFAULT_OFFICIAL_DIR = _REPO_ROOT / "runs" / "feedback_eval" / "official"
DEFAULT_OUT_DIR = _REPO_ROOT / "runs" / "feedback_eval" / "sensitivity_empty_b0_16384"


def _records_dir(store_dir: Path) -> Path:
    return Path(store_dir) / "records"


def _model_of(record: dict[str, Any]) -> str:
    return str((record.get("settings") or {}).get("model") or record.get("model") or "")


def select_empty_b0_pairs(official_dir: Path) -> list[tuple[str, str]]:
    """Return (model, item_id) pairs whose official B0 was empty_response.

    B1–B3 on those pairs are rerun too because empty B0 made their
    follow-up messages invalid. Later-condition empties with a successful
    B0 are left on the 4096 official record.
    """
    pairs: set[tuple[str, str]] = set()
    for path in _records_dir(official_dir).glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("condition") != "B0":
            continue
        if record.get("accounting_status") != "empty_response":
            continue
        pairs.add((_model_of(record), str(record.get("item_id"))))
    ordered = sorted(pairs, key=lambda row: (row[0], int(row[1]) if str(row[1]).isdigit() else row[1]))
    logger.info("Selected %s empty-B0 pairs from %s", len(ordered), official_dir)
    return ordered


def run_selected_pairs(
    pairs: list[tuple[str, str]],
    items: list[dict[str, Any]],
    models: list[str],
    clients: dict[str, Any],
    config: dict[str, Any],
    out_dir: Path | None = None,
    official_dir: Path | None = None,
) -> dict[str, Any]:
    """Run B0–B3 only for selected (model, item) pairs, in model then item order."""
    by_id = {str(item["key"]): item for item in items}
    pair_set = set(pairs)
    missing_items = sorted({item_id for _, item_id in pairs if item_id not in by_id})
    if missing_items:
        raise KeyError(f"empty-B0 item ids missing from split: {missing_items}")
    calls = 0
    stopped = None
    for model in models:
        for item in items:
            item_id = str(item["key"])
            if (model, item_id) not in pair_set:
                continue
            logger.info("Sensitivity run_item model=%s key=%s", model, item_id)
            result = run_item(item, clients[model], {**config, "model": model})
            calls += int(result.get("n_client_calls") or 0)

    store = config.get("store")
    statuses: dict[str, int] = {}
    b0_recovered = 0
    b0_still_empty = 0
    for model, item_id in pairs:
        for condition in CONDITIONS:
            key = None
            record = None
            if store is not None:
                key = record_key(
                    config.get("protocol", "feedback-eval-dev-v1"),
                    model,
                    item_id,
                    condition,
                    int(config.get("repeat") or 0),
                )
                record = store.get_record(key)
            status = (record or {}).get("accounting_status") or "missing"
            statuses[status] = statuses.get(status, 0) + 1
            if condition != "B0":
                continue
            generation = (record or {}).get("generation") or {}
            if classify_generation(generation) == "success" and str(
                generation.get("text") or ""
            ).strip():
                b0_recovered += 1
            else:
                b0_still_empty += 1

    expected = len(pairs) * len(CONDITIONS)
    accounted = sum(statuses.values())
    summary = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "official_dir": str(official_dir) if official_dir is not None else None,
        "out_dir": str(out_dir) if out_dir is not None else None,
        "n_pairs": len(pairs),
        "pairs": [{"model": model, "item_id": item_id} for model, item_id in pairs],
        "expected": expected,
        "accounted": accounted,
        "missing": expected - accounted,
        "statuses": statuses,
        "success": statuses.get("success", 0),
        "empty_response": statuses.get("empty_response", 0),
        "generation_failures": (
            statuses.get("error", 0)
            + statuses.get("timeout", 0)
            + statuses.get("rate_limit", 0)
        ),
        "b0_recovered": b0_recovered,
        "b0_still_empty": b0_still_empty,
        "calls_this_invocation": calls,
        "stopped": stopped,
        "max_tokens": (config.get("parameters") or {}).get("max_tokens"),
        "note": (
            "Sensitivity rerun of official empty B0 pairs at max_tokens=16384. "
            "Do not merge into the frozen 4096 official store."
        ),
    }
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        _atomic_write_json(Path(out_dir) / "sensitivity_summary.json", summary)
    logger.info(
        "Sensitivity finished expected=%s success=%s b0_recovered=%s still_empty=%s calls=%s",
        expected,
        summary["success"],
        b0_recovered,
        b0_still_empty,
        calls,
    )
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    print(f"n_pairs={summary['n_pairs']}")
    print(f"expected={summary['expected']}")
    print(f"accounted={summary['accounted']}")
    print(f"success={summary['success']}")
    print(f"empty_response={summary['empty_response']}")
    print(f"generation_failures={summary['generation_failures']}")
    print(f"b0_recovered={summary['b0_recovered']}")
    print(f"b0_still_empty={summary['b0_still_empty']}")
    print(f"actual_calls={summary['calls_this_invocation']}")
    print(f"stopped={summary.get('stopped')}")
    print(f"max_tokens={summary.get('max_tokens')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rerun official empty B0 pairs at max_tokens=16384"
    )
    parser.add_argument("--official-dir", type=Path, default=DEFAULT_OFFICIAL_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--config",
        type=Path,
        default=_SENSITIVITY_CONFIG,
    )
    parser.add_argument(
        "--paid",
        action="store_true",
        help="Authorize live HKBU calls. Without this flag the script exits.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    if not args.paid:
        print("sensitivity_refused reason=missing_paid_flag")
        logger.warning("Refusing live sensitivity run without --paid")
        return 2
    official_dir = Path(args.official_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    if official_dir == out_dir:
        print("sensitivity_refused reason=out_dir_is_official")
        logger.error("Refusing to write 16384 records into the official store")
        return 1

    load_dotenv_file(_REPO_ROOT / ".env")
    pairs = select_empty_b0_pairs(official_dir)
    if not pairs:
        print("sensitivity_refused reason=no_empty_b0_pairs")
        return 1

    development = apply_env_models(
        load_mapping_yaml(Path(args.config)), dict(os.environ)
    )
    freeze, fingerprint = build_experiment_freeze(
        _REPO_ROOT, development, dict(os.environ)
    )
    prior = inspect_prior_ledger(default_prior_ledger_candidates(_REPO_ROOT))
    store = RunStore(out_dir)
    try:
        store.initialize_frozen(fingerprint, prior, freeze)
    except StoreError as exc:
        logger.error("Sensitivity freeze refused: %s", exc)
        print(f"sensitivity_refused reason={type(exc).__name__}")
        return 1

    _atomic_write_json(
        out_dir / "selected_pairs.json",
        {
            "n_pairs": len(pairs),
            "pairs": [{"model": model, "item_id": item_id} for model, item_id in pairs],
            "official_dir": str(official_dir),
            "protocol": development["protocol"],
            "max_tokens": development["parameters"]["max_tokens"],
        },
    )

    wanted_ids = {item_id for _, item_id in pairs}
    items = [
        item
        for item in load_split_items(_REPO_ROOT, development, "test")
        if str(item["key"]) in wanted_ids
    ]
    models = [str(model["id"]) for model in development["models"]]
    timeout_s = float((development.get("retry") or {}).get("timeout_s") or 600.0)
    max_retries = int((development.get("retry") or {}).get("rate_limit_retries") or 1)
    cap = development.get("cost_cap") or {}
    call_counter = {"n": 0}
    clients: dict[str, Any] = {}
    for model in development["models"]:
        model_id = str(model["id"])
        inner = build_http_client(model, timeout_s=timeout_s, max_retries=max_retries)
        if call_cap_is_confirmed(cap):
            clients[model_id] = CallCappedClient(
                inner, int(cap["call_cap"]), call_counter
            )
        else:
            clients[model_id] = inner
    templates = load_prompt_templates()
    item_config = {
        **fingerprint,
        "store": store,
        "prompts": templates,
        "parameters": development["parameters"],
        "protocol": development["protocol"],
    }
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    try:
        summary = run_selected_pairs(
            pairs,
            items,
            models,
            clients,
            item_config,
            out_dir=out_dir,
            official_dir=official_dir,
        )
    except Exception as exc:
        print(f"sensitivity_stopped reason={type(exc).__name__}")
        return 4

    _write_execution_summary(
        out_dir,
        {
            "expected": summary["expected"],
            "accounted": summary["accounted"],
            "success": summary["success"],
            "empty_response": summary["empty_response"],
            "generation_failures": summary["generation_failures"],
            "missing": summary["missing"],
            "truncated": 0,
            "cost_unknown": summary["accounted"],
            "calls_this_invocation": summary["calls_this_invocation"],
            "stopped": summary.get("stopped"),
            "statuses": summary["statuses"],
        },
        {
            "client": _http_client_label(development),
            "paid": True,
            "ready_for_official_run": freeze.get("ready_for_official_run"),
            "n_pairs": summary["n_pairs"],
            "b0_recovered": summary["b0_recovered"],
            "b0_still_empty": summary["b0_still_empty"],
            "cost_cap": development["cost_cap"],
            "prior_balance": prior.get("balance"),
            "ledger_calls": store.load_ledger().get("calls"),
            "spent_this_run": store.load_ledger().get("spent_this_run"),
            "max_tokens": development["parameters"]["max_tokens"],
            "protocol": development["protocol"],
        },
    )
    _print_summary(summary)
    return 0 if summary["missing"] == 0 and not summary.get("stopped") else 4


if __name__ == "__main__":
    raise SystemExit(main())
