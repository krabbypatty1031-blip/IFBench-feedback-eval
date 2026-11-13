#!/usr/bin/env python3
"""Quarantine non-success official identities, then resume the frozen run.

Lives outside feedback_eval/*.py so the freeze code hash does not change.
Failed originals are moved, not rewritten. Ledger counters are recounted from
the records that remain. Success rows stay in place for resume-skip.
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

# Allow `python scripts/retry_official_failed.py` from the IFBench root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from feedback_eval.cli import (  # noqa: E402
    _OFFICIAL_CONFIG,
    _repo_root,
    _resolve_config_path,
    build_experiment_freeze,
    cmd_run_official_paid,
)
from feedback_eval.environment import apply_env_models, load_dotenv_file  # noqa: E402
from feedback_eval.store import (  # noqa: E402
    _CALL_COUNTERS,
    _atomic_write_json,
    load_mapping_yaml,
    validate_resume,
)

logger = logging.getLogger(__name__)

# Generation accounting states that are not usable model answers.
RETRYABLE_STATUSES = frozenset(
    {
        "empty_response",
        "error",
        "rate_limit",
        "timeout",
        "truncated",
        "missing_usage",
    }
)
ARCHIVE_ROOT_NAME = "failed_archive"


def _records_dir(store_dir: Path) -> Path:
    return Path(store_dir) / "records"


def _load_record(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def list_failed_record_paths(store_dir: Path) -> list[Path]:
    """Return record files whose accounting_status is retryable."""
    failed: list[Path] = []
    for path in sorted(_records_dir(store_dir).glob("*.json")):
        record = _load_record(path)
        status = record.get("accounting_status")
        if status in RETRYABLE_STATUSES:
            failed.append(path)
        elif status != "success":
            logger.warning("Leaving unclassified status=%s path=%s", status, path.name)
    return failed


def rebuild_ledger_from_records(store_dir: Path) -> dict[str, Any]:
    """Recount call statuses from remaining records. Do not invent spend=0."""
    store_dir = Path(store_dir)
    ledger_path = store_dir / "ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    counts = {name: 0 for name in _CALL_COUNTERS}
    pending = 0
    for path in _records_dir(store_dir).glob("*.json"):
        status = _load_record(path).get("accounting_status")
        if status not in counts:
            counts[status] = 0
        counts[status] += 1
        if status == "timeout":
            pending += 1
    ledger["calls"] = counts
    ledger["pending_unverified"] = pending
    # Remaining costs are still unmetered on HKBU; do not write a fake zero.
    spent = dict(ledger.get("spent_this_run") or {})
    if spent.get("status") != "metered":
        spent["amount"] = None
        spent["status"] = "unknown"
        ledger["spent_this_run"] = spent
    _atomic_write_json(ledger_path, ledger)
    logger.info("Rebuilt ledger calls=%s pending_unverified=%s", counts, pending)
    return ledger


def quarantine_failed_records(
    store_dir: Path, stamp: str | None = None
) -> dict[str, Any]:
    """Move retryable records into failed_archive/<stamp>/ and recount the ledger.

    Why move rather than delete: the first-pass failures stay auditable, and
    put_record refuses to overwrite a different payload for the same identity.
    """
    store_dir = Path(store_dir)
    failed_paths = list_failed_record_paths(store_dir)
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_root = store_dir / ARCHIVE_ROOT_NAME / stamp
    archive_records = archive_root / "records"
    moved: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    if failed_paths:
        archive_records.mkdir(parents=True, exist_ok=False)
    for path in failed_paths:
        record = _load_record(path)
        dest = archive_records / path.name
        dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.unlink()
        status = str(record.get("accounting_status"))
        status_counts[status] = status_counts.get(status, 0) + 1
        moved.append(
            {
                "filename": path.name,
                "record_key": record.get("record_key"),
                "item_id": record.get("item_id"),
                "condition": record.get("condition"),
                "model": (record.get("settings") or {}).get("model"),
                "accounting_status": status,
            }
        )
        logger.info(
            "Archived failed key=%s status=%s item=%s %s",
            record.get("record_key"),
            status,
            record.get("item_id"),
            record.get("condition"),
        )
    manifest = {
        "schema_version": "failed-archive-v1",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stamp": stamp,
        "n_moved": len(moved),
        "statuses": status_counts,
        "records": moved,
        "note": (
            "First-pass generation failures. Identities were removed from "
            "records/ so the frozen official schedule can regenerate them."
        ),
    }
    archive_root.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(archive_root / "manifest.json", manifest)
    rebuild_ledger_from_records(store_dir)
    logger.info("Quarantined n_moved=%s stamp=%s", manifest["n_moved"], stamp)
    return manifest


def check_stored_freeze_matches_current(store_dir: Path, config_path: Path | None = None) -> None:
    """Refuse to resume if the on-disk freeze no longer matches current sources."""
    repo_root = _repo_root()
    load_dotenv_file(repo_root / ".env")
    development = apply_env_models(
        load_mapping_yaml(_resolve_config_path(config_path or _OFFICIAL_CONFIG, repo_root)),
        dict(os.environ),
    )
    current, _fingerprint = build_experiment_freeze(
        repo_root, development, dict(os.environ)
    )
    existing = json.loads((Path(store_dir) / "freeze.json").read_text(encoding="utf-8"))
    validate_resume(existing, current)
    logger.info("Stored freeze still matches current sources")


def main(argv: list[str] | None = None) -> int:
    """Quarantine failed official identities, then optionally resume --paid."""
    parser = argparse.ArgumentParser(
        description="Archive non-success official records and resume the frozen run"
    )
    parser.add_argument("--store-dir", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="YAML mapping; defaults to configs/feedback_eval/official.yaml",
    )
    parser.add_argument(
        "--quarantine-only",
        action="store_true",
        help="Move failed records and rebuild the ledger; do not call models",
    )
    parser.add_argument(
        "--skip-freeze-check",
        action="store_true",
        help="Do not compare freeze.json to current sources (debug only)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    store_dir = args.store_dir
    if not args.skip_freeze_check:
        check_stored_freeze_matches_current(store_dir, args.config)
    manifest = quarantine_failed_records(store_dir)
    print(f"quarantined={manifest['n_moved']}")
    print(f"archive_stamp={manifest['stamp']}")
    print(f"statuses={json.dumps(manifest['statuses'], sort_keys=True)}")
    if args.quarantine_only:
        return 0
    logger.info("Resuming official paid schedule after quarantine")
    return cmd_run_official_paid(store_dir=store_dir, config_path=args.config)


if __name__ == "__main__":
    sys.exit(main())
