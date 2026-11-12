"""CLI for audit, dry-run, freeze, budget estimate, and gated development trials.

`dry-run` and `trial-dev --offline` use FakeClient only. `trial-dev --paid`
never constructs an HTTP client unless authorization, an API key, a verified
balance, and a confirmed cost cap are all present.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from feedback_eval.client import FakeClient
from feedback_eval.dataset import file_sha256, split_fingerprint
from feedback_eval.runner import PROMPTS_PATH, load_prompt_templates, run_item, synthetic_conjunction_item
from feedback_eval.store import (
    STORE_SCHEMA_VERSION,
    RunStore,
    StoreError,
    _OFFICIAL_DATA_SHA256,
    _canonical_sha256,
    build_freeze,
    decide_paid_trial,
    default_prior_ledger_candidates,
    estimate_budget,
    inspect_prior_ledger,
    load_mapping_yaml,
    select_review_ids,
)

logger = logging.getLogger(__name__)

_OK_USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
_DEFAULT_CONFIG = Path("configs") / "feedback_eval" / "development.yaml"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


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


def _resolve_config_path(config: Path | None, repo_root: Path) -> Path:
    if config is None:
        return repo_root / _DEFAULT_CONFIG
    path = Path(config)
    return path if path.is_absolute() else repo_root / path


def _git_head(repo_root: Path) -> str | None:
    """Record HEAD when git is available. Missing git is not a zero hash."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        logger.warning("git rev-parse failed: %s", exc)
        return None
    if completed.returncode != 0:
        logger.warning("git rev-parse exit=%s", completed.returncode)
        return None
    return completed.stdout.strip() or None


def _code_sha256(repo_root: Path) -> str:
    """Pin student package files by name so the hash is path-independent."""
    files = sorted(
        (repo_root / "feedback_eval").glob("*.py"),
        key=lambda path: path.name,
    )
    payload = {
        "files": [
            {"path": path.name, "sha256": file_sha256(path)} for path in files
        ]
    }
    digest = _canonical_sha256(payload)
    logger.info("Hashed %s student modules -> %s", len(files), digest)
    return digest


def collect_source_hashes(repo_root: Path, development: dict[str, Any]) -> dict[str, str]:
    """Hash the official JSONL, split, prompts, homology, support catalog, and code."""
    paths = development["paths"]

    def resolve(relative: str) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else repo_root / path

    data_path = resolve(paths["data"])
    split_path = resolve(paths["split"])
    data_hash = file_sha256(data_path)
    if data_hash != _OFFICIAL_DATA_SHA256:
        raise ValueError(
            f"data sha256 {data_hash} is not the official IFBench file {_OFFICIAL_DATA_SHA256}"
        )
    split = json.loads(split_path.read_text(encoding="utf-8"))
    recomputed = split_fingerprint(split)
    if recomputed != split["split_sha256"]:
        raise ValueError("split.json split_sha256 does not match split_fingerprint()")
    if split["source_sha256"] != data_hash:
        raise ValueError("split.source_sha256 does not match the data file")
    hashes = {
        "data_sha256": data_hash,
        "split_sha256": split["split_sha256"],
        "prompt_sha256": file_sha256(resolve(paths["prompts"])),
        "homology_sha256": file_sha256(resolve(paths["homology"])),
        "constraint_support_sha256": file_sha256(resolve(paths["constraint_support"])),
        "code_sha256": _code_sha256(repo_root),
    }
    logger.info("Collected source hashes data=%s split=%s", data_hash, hashes["split_sha256"])
    return hashes


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_experiment_freeze(
    repo_root: Path,
    development: dict[str, Any],
    env: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the freeze record and the RunStore fingerprint from development.yaml."""
    hashes = collect_source_hashes(repo_root, development)
    split_path = repo_root / development["paths"]["split"]
    split = json.loads(split_path.read_text(encoding="utf-8"))
    review = dict(development["human_review"])
    review["item_ids"] = select_review_ids(
        split["test_ids"], int(review["n_items"]), int(review["seed"])
    )
    prior = inspect_prior_ledger(default_prior_ledger_candidates(repo_root))
    estimate = estimate_budget(development["budget_planning"], prior)
    paid = decide_paid_trial(env, prior, development["cost_cap"])
    freeze_config = {
        "protocol": development["protocol"],
        "code_version": development["code_version"],
        "git_commit": _git_head(repo_root),
        "models": development["models"],
        "parameters": development["parameters"],
        "scoring": development["scoring"],
        "feedback": development["feedback"],
        "retry": development["retry"],
        "human_review": review,
        "cost_cap": development["cost_cap"],
        "budget_estimate": estimate,
        "paid_trial": paid,
    }
    freeze = build_freeze(freeze_config, hashes)
    fingerprint = {
        "protocol": development["protocol"],
        "data_sha256": hashes["data_sha256"],
        "model": ",".join(
            str(model.get("id") or model["slot"]) for model in development["models"]
        ),
        "prompt_sha256": hashes["prompt_sha256"],
        "code_version": development["code_version"],
        "parameters": development["parameters"],
    }
    return freeze, fingerprint


def cmd_freeze(store_dir: Path, config_path: Path | None = None) -> int:
    """Write or resume a freeze.json. Does not call models."""
    repo_root = _repo_root()
    development = load_mapping_yaml(_resolve_config_path(config_path, repo_root))
    freeze, fingerprint = build_experiment_freeze(
        repo_root, development, dict(os.environ)
    )
    prior = inspect_prior_ledger(default_prior_ledger_candidates(repo_root))
    try:
        RunStore(store_dir).initialize_frozen(fingerprint, prior, freeze)
    except StoreError as exc:
        logger.error("Freeze refused: %s", exc)
        print(f"freeze_refused={type(exc).__name__}")
        print(f"reason={exc}")
        return 1
    print(f"freeze_sha256={freeze['freeze_sha256']}")
    print(f"ready_for_official_run={str(freeze['ready_for_official_run']).lower()}")
    print(f"planned_outputs={freeze['budget_estimate']['planned_outputs']}")
    print(f"paid_trial_allowed={str(freeze['paid_trial']['allowed']).lower()}")
    print(f"paid_trial_reason={freeze['paid_trial']['reason']}")
    return 0


def cmd_estimate_budget(config_path: Path | None = None) -> dict[str, Any]:
    """Print the 2,080-output plan. Unknown prices stay unknown."""
    repo_root = _repo_root()
    development = load_mapping_yaml(_resolve_config_path(config_path, repo_root))
    prior = inspect_prior_ledger(default_prior_ledger_candidates(repo_root))
    estimate = estimate_budget(development["budget_planning"], prior)
    print(json.dumps(estimate, indent=2, ensure_ascii=False, sort_keys=True))
    return estimate


def cmd_trial_dev_paid(store_dir: Path, config_path: Path | None = None) -> int:
    """Stop before HTTP unless every paid-trial gate passes."""
    repo_root = _repo_root()
    development = load_mapping_yaml(_resolve_config_path(config_path, repo_root))
    prior = inspect_prior_ledger(default_prior_ledger_candidates(repo_root))
    decision = decide_paid_trial(dict(os.environ), prior, development["cost_cap"])
    if decision["allowed"] and any(
        not model.get("id") or not model.get("api_base") for model in development["models"]
    ):
        decision = {"allowed": False, "reason": "model_id_unspecified"}
    store_dir.mkdir(parents=True, exist_ok=True)
    _write_json(store_dir / "paid_trial.json", decision)
    if not decision["allowed"]:
        print(f"paid_trial_blocked reason={decision['reason']}")
        logger.warning("Paid trial stopped: %s", decision["reason"])
        return 3
    # Reaching here would be a live spend. Keep it explicit and unused until
    # models, prices, and a numeric cap are frozen in a later authorized run.
    logger.error("Paid trial gate passed but live generate is not enabled in Task 6")
    print("paid_trial_blocked reason=live_generate_not_enabled")
    return 3


def cmd_trial_dev_offline(
    store_dir: Path,
    n_items: int | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """End-to-end FakeClient trial over two candidate slots. No network."""
    repo_root = _repo_root()
    development = load_mapping_yaml(_resolve_config_path(config_path, repo_root))
    freeze, fingerprint = build_experiment_freeze(
        repo_root, development, dict(os.environ)
    )
    prior = inspect_prior_ledger(default_prior_ledger_candidates(repo_root))
    store = RunStore(store_dir)
    store.initialize_frozen(fingerprint, prior, freeze)
    n_items = int(n_items if n_items is not None else development["trial"]["n_dev_items"])
    models = [str(model.get("id") or model["slot"]) for model in development["models"]]
    planned_calls = n_items * len(models) * 4
    script = [
        _fake_ok(f"{model} item{item_index} cond{cond} hello and but so")
        for model in models
        for item_index in range(n_items)
        for cond in range(4)
    ]
    client = FakeClient(script)
    templates = load_prompt_templates()
    items = [synthetic_conjunction_item(str(index)) for index in range(n_items)]
    actual_before = len(client.calls)
    for model in models:
        item_config = {
            **fingerprint,
            "model": model,
            "store": store,
            "prompts": templates,
        }
        for item in items:
            run_item(item, client, item_config)
    actual_calls = len(client.calls) - actual_before
    summary = {
        "client": "FakeClient",
        "paid": False,
        "n_items": n_items,
        "n_models": len(models),
        "models": models,
        "planned_calls": planned_calls,
        "actual_calls": actual_calls,
        "ledger_calls": store.load_ledger().get("calls"),
        "ready_for_official_run": freeze["ready_for_official_run"],
        "stop_reason": freeze["budget_estimate"]["stop_reason"],
    }
    _write_json(store_dir / "trial_summary.json", summary)
    logger.info(
        "offline trial planned_calls=%s actual_calls=%s",
        planned_calls,
        actual_calls,
    )
    print(f"planned_calls={planned_calls}")
    print(f"actual_calls={actual_calls}")
    print(f"client={summary['client']}")
    print(f"paid={str(summary['paid']).lower()}")
    return summary


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Paid generate is gated; dry-run and offline trial never network."""
    parser = argparse.ArgumentParser(prog="feedback_eval")
    sub = parser.add_subparsers(dest="cmd", required=True)
    dry = sub.add_parser("dry-run", help="Offline FakeClient B0–B3 over synthetic items")
    dry.add_argument("--n-items", type=int, default=4)
    dry.add_argument("--store-dir", type=Path, required=True)
    freeze_p = sub.add_parser("freeze", help="Write freeze.json from development.yaml")
    freeze_p.add_argument("--store-dir", type=Path, required=True)
    freeze_p.add_argument("--config", type=Path, default=None)
    estimate_p = sub.add_parser(
        "estimate-budget", help="Print 2080-output plan; unknown prices stay unknown"
    )
    estimate_p.add_argument("--config", type=Path, default=None)
    trial = sub.add_parser(
        "trial-dev", help="Offline FakeClient trial, or a gated paid probe"
    )
    trial.add_argument("--store-dir", type=Path, required=True)
    trial.add_argument("--offline", action="store_true")
    trial.add_argument("--paid", action="store_true")
    trial.add_argument("--n-items", type=int, default=None)
    trial.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.cmd == "dry-run":
        dry_run(n_items=args.n_items, store_dir=args.store_dir)
        return 0
    if args.cmd == "freeze":
        return cmd_freeze(store_dir=args.store_dir, config_path=args.config)
    if args.cmd == "estimate-budget":
        cmd_estimate_budget(config_path=args.config)
        return 0
    if args.cmd == "trial-dev":
        if args.paid:
            return cmd_trial_dev_paid(store_dir=args.store_dir, config_path=args.config)
        cmd_trial_dev_offline(
            store_dir=args.store_dir,
            n_items=args.n_items,
            config_path=args.config,
        )
        return 0
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
