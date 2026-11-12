"""Offline CLI for audit, dry-run, and later freeze/run entry points.

`dry-run` uses FakeClient only. It never constructs OpenAICompatClient.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from feedback_eval.client import FakeClient
from feedback_eval.dataset import file_sha256
from feedback_eval.runner import PROMPTS_PATH, load_prompt_templates, run_item, synthetic_conjunction_item
from feedback_eval.store import (
    STORE_SCHEMA_VERSION,
    RunStore,
    inspect_prior_ledger,
)

logger = logging.getLogger(__name__)

_OK_USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}


def _fake_ok(text: str) -> dict[str, Any]:
    return {
        "text": text,
        "usage": dict(_OK_USAGE),
        "finish_reason": "stop",
        "latency_ms": 1.0,
        "error": None,
    }


def _fingerprint_config(model: str) -> dict[str, Any]:
    return {
        "protocol": "feedback-eval-dev-v1",
        "data_sha256": "dry-run-synthetic",
        "model": model,
        "prompt_sha256": file_sha256(PROMPTS_PATH),
        "code_version": STORE_SCHEMA_VERSION,
        "parameters": {"temperature": 0.0, "max_tokens": 128, "seed": 42},
    }


def dry_run(
    n_items: int,
    store_dir: Path,
    client: FakeClient | None = None,
) -> dict[str, Any]:
    """Run n synthetic items through B0–B3 with a fake client. No network."""
    planned_calls = n_items * 4
    model = "fake-dry-run"
    fingerprint = _fingerprint_config(model)
    store = RunStore(store_dir)
    store.initialize(fingerprint, inspect_prior_ledger([]))
    templates = load_prompt_templates()
    config = {**fingerprint, "store": store, "prompts": templates}
    if client is None:
        script = [
            _fake_ok(f"item{item_index} cond{cond} hello and but so")
            for item_index in range(n_items)
            for cond in range(4)
        ]
        client = FakeClient(script)
    actual_before = len(client.calls)
    items = [synthetic_conjunction_item(str(index)) for index in range(n_items)]
    for item in items:
        run_item(item, client, config)
    actual_calls = len(client.calls) - actual_before
    logger.info(
        "dry-run n_items=%s planned_calls=%s actual_calls=%s store=%s",
        n_items,
        planned_calls,
        actual_calls,
        store_dir,
    )
    print(f"planned_calls={planned_calls}")
    print(f"actual_calls={actual_calls}")
    print(f"store_dir={store_dir}")
    return {
        "planned_calls": planned_calls,
        "actual_calls": actual_calls,
        "n_items": n_items,
        "store_dir": str(store_dir),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry. dry-run never performs paid or networked calls."""
    parser = argparse.ArgumentParser(prog="feedback_eval")
    sub = parser.add_subparsers(dest="cmd", required=True)
    dry = sub.add_parser("dry-run", help="Offline FakeClient B0–B3 over synthetic items")
    dry.add_argument("--n-items", type=int, default=4)
    dry.add_argument("--store-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.cmd == "dry-run":
        dry_run(n_items=args.n_items, store_dir=args.store_dir)
        return 0
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
