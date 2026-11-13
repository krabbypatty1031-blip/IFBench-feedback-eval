#!/usr/bin/env python3
"""Probe whether a higher max_tokens recovers empty official B0 answers.

Lives outside feedback_eval/*.py so the official freeze code hash does not
change. Writes only to a sidecar directory; the 4096 official store is
read-only. This is a sensitivity check, not a continuation of the frozen run.
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

from feedback_eval.client import (  # noqa: E402
    build_http_client,
    classify_generation,
    sanitize_settings,
)
from feedback_eval.environment import load_dotenv_file  # noqa: E402
from feedback_eval.store import _atomic_write_json, load_mapping_yaml  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_ITEM_IDS = ("200", "232")
DEFAULT_MAX_TOKENS = 8192
_OFFICIAL_CONFIG = _REPO_ROOT / "configs" / "feedback_eval" / "official.yaml"


def _records_dir(store_dir: Path) -> Path:
    return Path(store_dir) / "records"


def _load_record(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _model_of(record: dict[str, Any]) -> str:
    return str((record.get("settings") or {}).get("model") or record.get("model") or "")


def load_empty_b0_baselines(
    official_dir: Path,
    item_ids: list[str],
    models: list[str] | None = None,
    condition: str = "B0",
) -> list[dict[str, Any]]:
    """Collect empty_response rows for the requested identities and condition.

    Default B0: empty assistant text there is what turns B1–B3 into HTTP 400.
    Later conditions reuse the stored revision messages and only raise max_tokens.
    """
    wanted_ids = {str(item_id) for item_id in item_ids}
    wanted_models = {str(model) for model in models} if models else None
    wanted_condition = str(condition)
    baselines: list[dict[str, Any]] = []
    for path in sorted(_records_dir(official_dir).glob("*.json")):
        record = _load_record(path)
        if record.get("condition") != wanted_condition:
            continue
        if record.get("accounting_status") != "empty_response":
            continue
        item_id = str(record.get("item_id"))
        model = _model_of(record)
        if item_id not in wanted_ids:
            continue
        if wanted_models is not None and model not in wanted_models:
            continue
        usage = (record.get("generation") or {}).get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        baselines.append(
            {
                "model": model,
                "item_id": item_id,
                "messages": list(record.get("messages") or []),
                "settings": dict(record.get("settings") or {}),
                "baseline_status": record.get("accounting_status"),
                "baseline_finish_reason": (record.get("generation") or {}).get(
                    "finish_reason"
                ),
                "baseline_completion_tokens": usage.get("completion_tokens"),
                "baseline_reasoning_tokens": details.get("reasoning_tokens"),
                "baseline_text_chars": len(
                    str((record.get("generation") or {}).get("text") or "")
                ),
                "condition": wanted_condition,
                "source_path": str(path),
            }
        )
    baselines.sort(key=lambda row: (row["model"], int(row["item_id"])))
    logger.info(
        "Loaded %s empty %s baselines ids=%s models=%s",
        len(baselines),
        wanted_condition,
        sorted(wanted_ids),
        sorted(wanted_models) if wanted_models else "all",
    )
    return baselines


def build_probe_settings(baseline_settings: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    """Copy frozen generation settings and replace only the output cap."""
    settings = sanitize_settings(dict(baseline_settings or {}))
    settings["max_tokens"] = int(max_tokens)
    return settings


def _usage_view(generation: dict[str, Any] | None) -> dict[str, Any]:
    usage = (generation or {}).get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    text = str((generation or {}).get("text") or "")
    return {
        "status": classify_generation(generation or {}),
        "finish_reason": (generation or {}).get("finish_reason"),
        "error": (generation or {}).get("error"),
        "text_chars": len(text),
        "has_text": bool(text.strip()),
        "text_preview": text[:160],
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "latency_ms": (generation or {}).get("latency_ms"),
    }


def run_probes(
    clients: dict[str, Any],
    baselines: list[dict[str, Any]],
    max_tokens: int,
    out_dir: Path,
    official_dir: Path | None = None,
) -> dict[str, Any]:
    """Generate each baseline B0 at the probe cap and write a sidecar summary."""
    out_dir = Path(out_dir)
    records_dir = out_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    n_recovered = 0
    for index, baseline in enumerate(baselines):
        model = baseline["model"]
        client = clients[model]
        settings = build_probe_settings(baseline["settings"], max_tokens)
        logger.info(
            "Probe B0 item=%s model=%s max_tokens=%s",
            baseline["item_id"],
            model,
            max_tokens,
        )
        generation = client.generate(list(baseline["messages"]), settings)
        view = _usage_view(generation)
        if view["has_text"]:
            n_recovered += 1
        row = {
            "item_id": baseline["item_id"],
            "model": model,
            "max_tokens": max_tokens,
            "baseline": {
                "status": baseline["baseline_status"],
                "finish_reason": baseline["baseline_finish_reason"],
                "completion_tokens": baseline["baseline_completion_tokens"],
                "reasoning_tokens": baseline["baseline_reasoning_tokens"],
                "text_chars": baseline["baseline_text_chars"],
            },
            "probe": view,
            "official_source": baseline.get("source_path"),
        }
        results.append(row)
        _atomic_write_json(
            records_dir / f"{index:02d}_{model}_{baseline['item_id']}_B0.json",
            {
                "item_id": baseline["item_id"],
                "condition": "B0",
                "model": model,
                "messages": baseline["messages"],
                "generation": generation,
                "settings": settings,
                "comparison": row,
                "note": "Sensitivity probe. Not part of the official 4096 freeze.",
            },
        )
    summary = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "official_dir": str(official_dir) if official_dir is not None else None,
        "out_dir": str(out_dir),
        "max_tokens": int(max_tokens),
        "n_calls": len(results),
        "n_recovered_text": n_recovered,
        "n_still_empty": sum(1 for row in results if not row["probe"]["has_text"]),
        "results": results,
        "note": (
            "Higher max_tokens probe of official empty B0 identities. "
            "Do not merge these rows into the frozen 4096 official store."
        ),
    }
    _atomic_write_json(out_dir / "summary.json", summary)
    logger.info(
        "Probe finished n_calls=%s recovered=%s still_empty=%s",
        summary["n_calls"],
        summary["n_recovered_text"],
        summary["n_still_empty"],
    )
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    print(f"n_calls={summary['n_calls']}")
    print(f"n_recovered_text={summary['n_recovered_text']}")
    print(f"n_still_empty={summary['n_still_empty']}")
    print(f"max_tokens={summary['max_tokens']}")
    for row in summary["results"]:
        probe = row["probe"]
        print(
            "item={item} model={model} status={status} chars={chars} "
            "completion={comp} reasoning={reason} finish={finish}".format(
                item=row["item_id"],
                model=row["model"],
                status=probe["status"],
                chars=probe["text_chars"],
                comp=probe["completion_tokens"],
                reason=probe["reasoning_tokens"],
                finish=probe["finish_reason"],
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe empty official B0 rows at a higher max_tokens"
    )
    parser.add_argument(
        "--official-dir",
        type=Path,
        default=_REPO_ROOT / "runs" / "feedback_eval" / "official",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_REPO_ROOT / "runs" / "feedback_eval" / "probe_empty_b0_8192",
    )
    parser.add_argument("--item-ids", default=",".join(DEFAULT_ITEM_IDS))
    parser.add_argument("--models", default="gpt-4.1,gpt-4o")
    parser.add_argument("--condition", default="B0")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--timeout-s", type=float, default=600.0)
    parser.add_argument(
        "--paid",
        action="store_true",
        help="Authorize live HKBU calls. Without this flag the script exits.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    if not args.paid:
        print("probe_refused reason=missing_paid_flag")
        logger.warning("Refusing live probe without --paid")
        return 2

    load_dotenv_file(_REPO_ROOT / ".env")
    item_ids = [part.strip() for part in str(args.item_ids).split(",") if part.strip()]
    models = [part.strip() for part in str(args.models).split(",") if part.strip()]
    baselines = load_empty_b0_baselines(
        args.official_dir, item_ids, models, condition=str(args.condition)
    )
    if not baselines:
        print("probe_refused reason=no_empty_b0_baselines")
        logger.error("No empty B0 baselines for ids=%s models=%s", item_ids, models)
        return 1

    development = load_mapping_yaml(_OFFICIAL_CONFIG)
    timeout_s = float(args.timeout_s)
    max_retries = int((development.get("retry") or {}).get("rate_limit_retries") or 1)
    clients: dict[str, Any] = {}
    for model in development["models"]:
        model_id = str(model["id"])
        if model_id in models:
            clients[model_id] = build_http_client(
                model, timeout_s=timeout_s, max_retries=max_retries
            )
    missing = [model for model in models if model not in clients]
    if missing:
        print(f"probe_refused reason=missing_clients models={','.join(missing)}")
        return 1

    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    summary = run_probes(
        clients,
        baselines,
        max_tokens=int(args.max_tokens),
        out_dir=args.out_dir,
        official_dir=args.official_dir,
    )
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
