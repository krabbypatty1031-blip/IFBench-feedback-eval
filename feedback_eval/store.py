"""Atomic run records, config fingerprints, and budget accounting.

Identity is (protocol, model, item_id, condition, repeat), not prompt text.
A missing prior ledger is unverifiable: this module will not write spent=0 as
if the history were known. Timeout costs stay in pending_unverified.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from feedback_eval.client import classify_generation, sanitize_settings

logger = logging.getLogger(__name__)

STORE_SCHEMA_VERSION = "store-v1"
_REQUIRED_LEDGER_KEYS = {
    "schema_version",
    "config_fingerprint",
    "prior_ledger_status",
    "prior_spent",
    "prior_balance",
    "unit_prices",
    "spent_this_run",
    "pending_unverified",
    "calls",
    "incomplete",
}
_CALL_COUNTERS = (
    "success",
    "timeout",
    "rate_limit",
    "truncated",
    "empty_response",
    "missing_usage",
    "error",
)


class StoreError(Exception):
    """Base error for resume and budget safety."""


class ResumeConflictError(StoreError):
    """Same identity already stored a different generation."""


class ConfigConflictError(StoreError):
    """Run directory fingerprint does not match the current config."""


class IncompleteLedgerError(StoreError):
    """Ledger is missing, truncated, or marked incomplete."""


def record_key(
    protocol: str, model: str, item_id: str, condition: str, repeat: int
) -> str:
    """Stable identity for one protocol/model/item/condition/repeat tuple."""
    payload = {
        "protocol": protocol,
        "model": model,
        "item_id": str(item_id),
        "condition": condition,
        "repeat": int(repeat),
    }
    return _canonical_sha256(payload)


def config_fingerprint(config: dict[str, Any]) -> str:
    """Hash data, model, prompts, code version, and parameters. No secrets."""
    payload = {
        "protocol": config["protocol"],
        "data_sha256": config["data_sha256"],
        "model": config["model"],
        "prompt_sha256": config["prompt_sha256"],
        "code_version": config["code_version"],
        "parameters": config["parameters"],
    }
    return _canonical_sha256(payload)


def inspect_prior_ledger(candidates: list[Path]) -> dict[str, Any]:
    """Report whether a previous budget ledger exists.

    Missing files are `unverified_missing` with spent/balance/prices set to
    None. That is not a zero-spend history.
    """
    paths = [Path(path) for path in candidates]
    existing = [path for path in paths if path.is_file()]
    if not existing:
        logger.warning(
            "No prior ledger at %s; historical spend is unverifiable",
            [str(path) for path in paths],
        )
        return {
            "status": "unverified_missing",
            "paths": [str(path) for path in paths],
            "spent": None,
            "balance": None,
            "unit_prices": None,
            "note": "No prior ledger found; do not treat historical spend as zero.",
        }
    logger.info("Found prior ledger files: %s", [str(path) for path in existing])
    return {
        "status": "unverified_unparsed",
        "paths": [str(path) for path in existing],
        "spent": None,
        "balance": None,
        "unit_prices": None,
        "note": "Ledger files exist but amounts were not parsed; do not reset them.",
    }


def default_prior_ledger_candidates(repo_root: Path) -> list[Path]:
    """Locations checked before any paid run. Absence is unverifiable, not zero."""
    root = Path(repo_root)
    return [
        root / "runs" / "feedback_eval" / "ledger.json",
        root / "runs" / "feedback_eval" / "budget.json",
        root / "docs" / "budget_ledger.json",
    ]


def _canonical_sha256(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    """Write JSON via a temp file and os.replace so readers never see a partial."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _content_view(record: dict[str, Any]) -> str:
    """Compare identity payload, ignoring latency and later accounting fields."""
    generation = record["generation"]
    view = {
        "record_key": record["record_key"],
        "messages": record.get("messages"),
        "text": generation.get("text"),
        "error": generation.get("error"),
        "finish_reason": generation.get("finish_reason"),
        "usage": generation.get("usage"),
    }
    return json.dumps(view, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class RunStore:
    """One run directory: config fingerprint, JSONL-free per-id records, ledger."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.records_dir = self.root / "records"
        self.ledger_path = self.root / "ledger.json"
        self.config_path = self.root / "config.json"
        self.lock_path = self.root / "ledger.lock"
        self._thread_lock = threading.Lock()

    def initialize(self, config: dict[str, Any], prior: dict[str, Any]) -> None:
        """Create or resume a run directory. Config drift and broken ledgers fail."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.records_dir.mkdir(parents=True, exist_ok=True)
        fingerprint = config_fingerprint(config)
        if self.config_path.exists() or self.ledger_path.exists():
            self._reject_if_incomplete()
            stored = json.loads(self.config_path.read_text(encoding="utf-8"))
            if stored.get("config_fingerprint") != fingerprint:
                logger.error(
                    "Config fingerprint mismatch stored=%s current=%s",
                    stored.get("config_fingerprint"),
                    fingerprint,
                )
                raise ConfigConflictError(
                    "run directory fingerprint does not match the current config"
                )
            logger.info("Resuming run directory %s", self.root)
            return
        if prior.get("spent") == 0 and prior.get("status") == "unverified_missing":
            raise StoreError("refusing to treat an unverified prior ledger as zero spend")
        ledger = {
            "schema_version": STORE_SCHEMA_VERSION,
            "config_fingerprint": fingerprint,
            "prior_ledger_status": prior.get("status"),
            "prior_ledger_paths": prior.get("paths", []),
            "prior_spent": prior.get("spent"),
            "prior_balance": prior.get("balance"),
            "unit_prices": prior.get("unit_prices"),
            "spent_this_run": {"amount": None, "currency": None, "status": "unknown"},
            "pending_unverified": 0,
            "calls": {name: 0 for name in _CALL_COUNTERS},
            "incomplete": False,
        }
        public_config = sanitize_settings(dict(config))
        _atomic_write_json(
            self.config_path,
            {"config_fingerprint": fingerprint, "config": public_config},
        )
        _atomic_write_json(self.ledger_path, ledger)
        logger.info(
            "Initialized run store at %s prior_status=%s fingerprint=%s",
            self.root,
            prior.get("status"),
            fingerprint,
        )

    def load_ledger(self) -> dict[str, Any]:
        """Read the ledger JSON. Callers must not invent missing amounts."""
        return json.loads(self.ledger_path.read_text(encoding="utf-8"))

    def record_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.records_dir / f"{digest}.json"

    @contextmanager
    def budget_lock(self) -> Iterator[None]:
        """Process + thread lock so concurrent reservations cannot drop counts."""
        with self._thread_lock:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def reserve_unknown_cost(self) -> None:
        """Hold a slot for a call that may have been billed (timeouts)."""
        with self.budget_lock():
            ledger = self.load_ledger()
            ledger["pending_unverified"] = int(ledger.get("pending_unverified", 0)) + 1
            ledger["spent_this_run"]["status"] = "unknown"
            ledger["spent_this_run"]["amount"] = None
            _atomic_write_json(self.ledger_path, ledger)
            logger.info(
                "Reserved unverified cost, pending_unverified=%s",
                ledger["pending_unverified"],
            )

    def put_record(self, key: str, record: dict[str, Any]) -> None:
        """Atomically save one identity. Identical retries are no-ops."""
        with self.budget_lock():
            path = self.record_path(key)
            incoming = dict(record)
            incoming["record_key"] = key
            if "settings" in incoming:
                incoming["settings"] = sanitize_settings(incoming["settings"])
            view = _content_view(incoming)
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if _content_view(existing) != view:
                    logger.error("Resume conflict for key=%s", key)
                    raise ResumeConflictError(
                        f"record {key} already exists with different content"
                    )
                logger.info("Idempotent resume for key=%s", key)
                return
            incoming["accounting_status"] = classify_generation(incoming["generation"])
            incoming["cost"] = incoming.get(
                "cost", {"amount": None, "currency": None, "status": "unknown"}
            )
            incoming["config_fingerprint"] = json.loads(
                self.config_path.read_text(encoding="utf-8")
            )["config_fingerprint"]
            _atomic_write_json(path, incoming)
            self._apply_accounting(incoming)
            logger.info(
                "Stored key=%s status=%s",
                key,
                incoming["accounting_status"],
            )

    def _apply_accounting(self, record: dict[str, Any]) -> None:
        ledger = self.load_ledger()
        status = record["accounting_status"]
        if status not in ledger["calls"]:
            ledger["calls"][status] = 0
        ledger["calls"][status] += 1
        if status == "timeout":
            ledger["pending_unverified"] = int(ledger.get("pending_unverified", 0)) + 1
        ledger["spent_this_run"]["status"] = "unknown"
        ledger["spent_this_run"]["amount"] = None
        _atomic_write_json(self.ledger_path, ledger)

    def _reject_if_incomplete(self) -> None:
        stray = list(self.root.glob("*.tmp")) + list(self.records_dir.glob("*.tmp"))
        if stray:
            raise IncompleteLedgerError(f"stray temp files: {stray}")
        if not self.ledger_path.exists() or not self.config_path.exists():
            raise IncompleteLedgerError("missing ledger or config")
        try:
            ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise IncompleteLedgerError(f"ledger is not valid JSON: {exc}") from exc
        missing = _REQUIRED_LEDGER_KEYS - set(ledger)
        if missing or ledger.get("incomplete"):
            raise IncompleteLedgerError(f"incomplete ledger, missing={sorted(missing)}")
