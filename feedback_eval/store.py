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
import random
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from feedback_eval.client import classify_generation, sanitize_settings
from feedback_eval.config import load_mapping_yaml  # compatibility re-export

logger = logging.getLogger(__name__)

STORE_SCHEMA_VERSION = "store-v1"
FREEZE_SCHEMA_VERSION = "freeze-v1"
_OFFICIAL_DATA_SHA256 = "d2ada7da94a38cfe406351614c4e686846ed2da6d1b339db95fa5ead19554a4a"
_SOURCE_HASH_KEYS = (
    "data_sha256",
    "split_sha256",
    "prompt_sha256",
    "code_sha256",
    "homology_sha256",
    "constraint_support_sha256",
)
_REQUIRED_FREEZE_KEYS = {
    "schema_version",
    "freeze_sha256",
    "protocol",
    "code_version",
    "git_commit",
    "models",
    "parameters",
    "scoring",
    "feedback",
    "retry",
    "human_review",
    "cost_cap",
    "budget_estimate",
    "paid_trial",
    "ready_for_official_run",
    *_SOURCE_HASH_KEYS,
}
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
# inspect_prior_ledger uses these statuses with spent/balance left as None.
_UNVERIFIED_PRIOR_STATUSES = frozenset(
    {"unverified_missing", "unverified_unparsed"}
)


class StoreError(Exception):
    """Base error for resume and budget safety."""


class ResumeConflictError(StoreError):
    """Same identity already stored a different generation."""


class ConfigConflictError(StoreError):
    """Run directory fingerprint does not match the current config."""


class IncompleteLedgerError(StoreError):
    """Ledger is missing, truncated, or marked incomplete."""


class FreezeConflictError(StoreError):
    """Stored freeze hash or content does not match the current freeze."""


class FreezeMissingError(StoreError):
    """A non-empty run directory has no freeze.json, so it cannot be claimed."""


class IncompleteFreezeError(StoreError):
    """Freeze record is missing required fields."""


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


def compute_generation_cost(
    usage: dict[str, Any] | None, unit_price: dict[str, Any] | None
) -> dict[str, Any]:
    """Meter one call from usage. Cache hits are billed at the miss tariff.

    Missing usage or unverified prices stay unknown; they are not zero.
    """
    currency = (unit_price or {}).get("currency")
    unknown = {"amount": None, "currency": currency, "status": "unknown"}
    if not unit_price or unit_price.get("source") != "verified":
        return unknown
    if unit_price.get("input_per_million") is None:
        return unknown
    if unit_price.get("output_per_million") is None:
        return unknown
    if not usage:
        return unknown
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if prompt_tokens is None or completion_tokens is None:
        return unknown
    amount = (
        float(prompt_tokens) * float(unit_price["input_per_million"])
        + float(completion_tokens) * float(unit_price["output_per_million"])
    ) / 1_000_000.0
    return {"amount": amount, "currency": currency, "status": "metered"}


def _parse_verified_prior_ledger(path: Path) -> dict[str, Any] | None:
    """Return a parsed live-wallet ledger, or None if the file is not trusted."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != "prior-ledger-v1":
        return None
    if payload.get("source") != "deepseek_user_balance":
        return None
    if payload.get("status") != "ok":
        return None
    balance = payload.get("balance")
    if not isinstance(balance, (int, float)):
        return None
    spent = payload.get("spent")
    if spent is not None and not isinstance(spent, (int, float)):
        return None
    logger.info(
        "Parsed verified prior wallet %s currency=%s balance=%s spent_known=%s",
        path,
        payload.get("currency"),
        balance,
        spent is not None,
    )
    return {
        "status": "ok",
        "paths": [str(path)],
        "spent": None if spent is None else float(spent),
        "balance": float(balance),
        "currency": payload.get("currency"),
        "unit_prices": payload.get("unit_prices"),
        "fetched_at": payload.get("fetched_at"),
        "note": payload.get("note")
        or "Live wallet remaining. Historical spend is not reported.",
    }


def wallet_from_provider_payload(
    payload: dict[str, Any], preferred_currency: str = "CNY"
) -> dict[str, Any]:
    """Pick a positive wallet row. USD 0 must not mask a CNY balance."""
    infos = payload.get("balance_infos") or []
    preferred = None
    fallback = None
    for info in infos:
        try:
            amount = float(info.get("total_balance"))
        except (TypeError, ValueError):
            continue
        row = {
            "currency": info.get("currency"),
            "balance": amount,
            "granted_balance": info.get("granted_balance"),
            "topped_up_balance": info.get("topped_up_balance"),
        }
        if row["currency"] == preferred_currency:
            preferred = row
        if fallback is None or amount > float(fallback["balance"]):
            fallback = row
    chosen = preferred if preferred is not None else fallback
    if chosen is None:
        raise StoreError("provider balance payload had no parseable wallet row")
    logger.info(
        "Selected wallet currency=%s balance=%s is_available=%s",
        chosen["currency"],
        chosen["balance"],
        payload.get("is_available"),
    )
    return {
        **chosen,
        "is_available": payload.get("is_available"),
        "all_rows": [
            {
                "currency": info.get("currency"),
                "total_balance": info.get("total_balance"),
            }
            for info in infos
        ],
    }


def write_prior_balance_file(path: Path, wallet: dict[str, Any]) -> dict[str, Any]:
    """Persist a live wallet snapshot. Secrets never belong in this file."""
    from datetime import datetime, timezone

    record = {
        "schema_version": "prior-ledger-v1",
        "source": "deepseek_user_balance",
        "status": "ok",
        "currency": wallet["currency"],
        "balance": float(wallet["balance"]),
        "spent": None,
        "is_available": wallet.get("is_available"),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "all_rows": wallet.get("all_rows") or [],
        "note": (
            "Live wallet remaining. Historical spend is not reported; "
            "do not treat spent as zero."
        ),
    }
    _atomic_write_json(Path(path), record)
    logger.info("Wrote prior wallet snapshot to %s", path)
    return record


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
    for path in existing:
        parsed = _parse_verified_prior_ledger(path)
        if parsed is not None:
            return parsed
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
        root / "runs" / "feedback_eval" / "prior_balance.json",
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
        self.freeze_path = self.root / "freeze.json"
        self.lock_path = self.root / "ledger.lock"
        self._thread_lock = threading.Lock()

    def initialize(
        self,
        config: dict[str, Any],
        prior: dict[str, Any],
        *,
        unit_prices_by_model: dict[str, Any] | None = None,
        this_run_currency: str | None = None,
    ) -> None:
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
        # Missing/unparsed ledgers initialize only with unknown amounts.
        # `spent is None` is the honest inspect_prior_ledger() result; `== 0`
        # would miss forged non-zero values and would not fire on None.
        if prior.get("status") in _UNVERIFIED_PRIOR_STATUSES and (
            prior.get("spent") is not None or prior.get("balance") is not None
        ):
            raise StoreError(
                "refusing to treat an unverified prior ledger as a known spend or balance"
            )
        metered_prices = bool(unit_prices_by_model) and all(
            (price or {}).get("source") == "verified"
            and (price or {}).get("input_per_million") is not None
            and (price or {}).get("output_per_million") is not None
            for price in (unit_prices_by_model or {}).values()
        )
        # This-run spend starts at 0 only when every model tariff is verified.
        # Historical prior_spent stays None unless the wallet snapshot had it.
        spent_this_run = (
            {"amount": 0.0, "currency": this_run_currency, "status": "metered"}
            if metered_prices
            else {"amount": None, "currency": None, "status": "unknown"}
        )
        ledger = {
            "schema_version": STORE_SCHEMA_VERSION,
            "config_fingerprint": fingerprint,
            "prior_ledger_status": prior.get("status"),
            "prior_ledger_paths": prior.get("paths", []),
            "prior_spent": prior.get("spent"),
            "prior_balance": prior.get("balance"),
            "unit_prices": prior.get("unit_prices"),
            "unit_prices_by_model": unit_prices_by_model or {},
            "spent_this_run": spent_this_run,
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

    def initialize_frozen(
        self,
        config: dict[str, Any],
        prior: dict[str, Any],
        freeze: dict[str, Any],
    ) -> None:
        """Create or resume a run directory that is pinned to a freeze record.

        An empty directory receives freeze.json first. A matching freeze
        resumes. Leftover files without freeze.json are not taken over.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        if self.freeze_path.exists():
            existing = json.loads(self.freeze_path.read_text(encoding="utf-8"))
            validate_resume(existing, freeze)
            logger.info("Resuming frozen directory %s", self.root)
            self.initialize(config, prior)
            return
        leftovers = [
            path
            for path in self.root.iterdir()
            if path.name not in {".DS_Store", "ledger.lock"}
        ]
        if leftovers:
            logger.error(
                "Refusing to take over non-empty directory %s without freeze.json: %s",
                self.root,
                [path.name for path in leftovers],
            )
            raise FreezeMissingError(
                f"non-empty run directory {self.root} has no freeze.json"
            )
        _atomic_write_json(self.freeze_path, freeze)
        logger.info(
            "Wrote freeze %s ready_for_official_run=%s",
            freeze.get("freeze_sha256"),
            freeze.get("ready_for_official_run"),
        )
        self.initialize(config, prior, **_price_kwargs_from_freeze(freeze))

    def load_ledger(self) -> dict[str, Any]:
        """Read the ledger JSON. Callers must not invent missing amounts."""
        return json.loads(self.ledger_path.read_text(encoding="utf-8"))

    def record_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.records_dir / f"{digest}.json"

    def get_record(self, key: str) -> dict[str, Any] | None:
        """Return a stored record, or None if this identity has not been written."""
        path = self.record_path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

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
            ledger_for_prices = self.load_ledger()
            model_id = (incoming.get("settings") or {}).get("model")
            incoming["cost"] = compute_generation_cost(
                (incoming.get("generation") or {}).get("usage"),
                (ledger_for_prices.get("unit_prices_by_model") or {}).get(model_id),
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
        cost = record.get("cost") or {}
        if cost.get("status") == "metered" and cost.get("amount") is not None:
            current = ledger["spent_this_run"].get("amount")
            if current is None:
                current = 0.0
            ledger["spent_this_run"]["amount"] = float(current) + float(cost["amount"])
            ledger["spent_this_run"]["currency"] = cost.get("currency")
        if int(ledger.get("pending_unverified") or 0) > 0 or cost.get("status") != "metered":
            ledger["spent_this_run"]["status"] = "unknown"
        else:
            ledger["spent_this_run"]["status"] = "metered"
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


def select_review_ids(test_ids: list[str], n: int, seed: int) -> list[str]:
    """Draw `n` test ids with a fixed seed. The draw order is part of the freeze."""
    pool = [str(item_id) for item_id in test_ids]
    if n > len(pool):
        raise ValueError(f"cannot sample {n} review ids from {len(pool)} test ids")
    rng = random.Random(seed)
    rng.shuffle(pool)
    selected = pool[:n]
    logger.info("Selected %s human-review ids with seed=%s", n, seed)
    return selected


def estimate_budget(
    planning: dict[str, Any],
    prior: dict[str, Any],
    observed_usage: dict[str, Any] | None = None,
    unit_prices: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Plan official output count, retries, and cost with unverified amounts kept null.

    260 × 2 × 4 = 2,080 is the official-output planning value. Retry uplift and
    repeatability trials are added on top. A missing prior spend is not zero.
    Dollar totals are computed only from prices whose source is `verified`.
    """
    n_test = int(planning["n_test_items"])
    n_models = int(planning["n_models"])
    n_conditions = int(planning["n_conditions"])
    planned = n_test * n_models * n_conditions
    declared = planning.get("planned_outputs")
    if declared is not None and int(declared) != planned:
        raise ValueError(
            f"planned_outputs={declared} != {n_test}*{n_models}*{n_conditions}={planned}"
        )
    retry_uplift = float(planning.get("retry_uplift") or 0.0)
    retry_outputs = int(round(planned * retry_uplift))
    repeatability = int(planning.get("repeatability_outputs") or 0)
    total = planned + retry_outputs + repeatability
    prices = unit_prices if unit_prices is not None else prior.get("unit_prices")
    stop_reason = None
    if prior.get("balance") is None:
        stop_reason = "balance_unverified"
    elif not prices or prices.get("source") != "verified":
        stop_reason = "prices_unverified"
    amount = None
    cost_status = "unknown"
    currency = (prices or {}).get("currency") or "USD"
    verified_prices = (
        _unit_price_is_verified(prices)
        and observed_usage
        and observed_usage.get("n_outputs")
    )
    if verified_prices:
        n_obs = int(observed_usage["n_outputs"])
        mean_in = float(observed_usage.get("prompt_tokens") or 0) / n_obs
        mean_out = float(observed_usage.get("completion_tokens") or 0) / n_obs
        amount = (
            mean_in * total * float(prices["input_per_million"])
            + mean_out * total * float(prices["output_per_million"])
        ) / 1_000_000.0
        cost_status = "estimated_from_verified_prices"
        logger.info(
            "Budget estimate amount=%.6f %s from verified prices over %s outputs",
            amount,
            currency,
            total,
        )
    else:
        logger.warning(
            "Budget estimate cannot price %s outputs; stop_reason=%s",
            total,
            stop_reason,
        )
    return {
        "n_test_items": n_test,
        "n_models": n_models,
        "n_conditions": n_conditions,
        "planned_outputs": planned,
        "retry_uplift": retry_uplift,
        "retry_outputs": retry_outputs,
        "repeatability_outputs": repeatability,
        "total_outputs": total,
        "estimated_cost": {
            "amount": amount,
            "currency": currency,
            "status": cost_status,
        },
        "incurred_spend": prior.get("spent"),
        "incurred_status": prior.get("status"),
        "stop_reason": stop_reason,
        "observed_usage": observed_usage,
        "unit_prices": prices,
        "note": (
            "2080 is the official-output plan, not a measured cost. "
            "Unverified prior spend is not treated as zero."
        ),
    }


def decide_paid_trial(
    env: dict[str, str],
    prior: dict[str, Any],
    cost_cap: dict[str, Any],
    *,
    development: bool = False,
    call_limit: int | None = None,
) -> dict[str, Any]:
    """Gate paid API calls. Missing auth, key, balance, or cap each stop the run.

    A user-authorized *development* trial may proceed with an unverified
    historical balance only when a positive `call_limit` is set. That limit is
    not a dollar balance and does not treat prior spend as zero.
    """
    if str(env.get("FEEDBACK_EVAL_AUTHORIZE_SPEND") or "") != "1":
        reason = "authorization_missing"
    elif not (
        env.get("API_KEY")
        or env.get("FEEDBACK_EVAL_API_KEY")
        or env.get("AZURE_OPENAI_API_KEY")
    ):
        reason = "api_key_missing"
    elif development:
        # Dev trials must not fall through to official call_cap: LimitedClient
        # treats 0 as already exhausted and would block the first generate.
        if int(call_limit or 0) <= 0:
            reason = "call_limit_missing"
        else:
            logger.warning(
                "Development trial allowed with unverified balance under call_limit=%s",
                call_limit,
            )
            return {
                "allowed": True,
                "reason": None,
                "balance_status": prior.get("status"),
                "prior_spent": prior.get("spent"),
                "prior_balance": prior.get("balance"),
                "call_limit": int(call_limit),
                "note": (
                    "Historical balance unverified; development stop is call_limit. "
                    "Do not treat prior spend as zero."
                ),
            }
    elif call_cap_is_confirmed(cost_cap):
        call_cap = int(cost_cap["call_cap"])
        logger.warning(
            "Paid run allowed with unverified wallet under call_cap=%s",
            call_cap,
        )
        return {
            "allowed": True,
            "reason": None,
            "balance_status": prior.get("status"),
            "prior_spent": prior.get("spent"),
            "prior_balance": prior.get("balance"),
            "call_cap": call_cap,
            "note": (
                "Provider has no wallet endpoint; official stop is call_cap. "
                "Do not treat prior spend as zero."
            ),
        }
    elif prior.get("balance") is None:
        reason = "balance_unverified"
    elif cost_cap.get("amount") is None or cost_cap.get("status") != "confirmed":
        reason = "cost_cap_unverified"
    else:
        logger.info("Paid trial gate allowed under confirmed cap=%s", cost_cap)
        return {"allowed": True, "reason": None}
    logger.warning("Paid trial blocked: %s", reason)
    return {"allowed": False, "reason": reason}


def _freeze_body(freeze: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in freeze.items() if key != "freeze_sha256"}


def freeze_content_sha256(freeze: dict[str, Any]) -> str:
    """Hash freeze content after dropping the self-hash field."""
    return _canonical_sha256(_freeze_body(freeze))


def _require_freeze_fields(freeze: dict[str, Any]) -> None:
    missing = sorted(_REQUIRED_FREEZE_KEYS - set(freeze))
    if missing:
        raise IncompleteFreezeError(f"incomplete freeze, missing={missing}")


def call_cap_is_confirmed(cost_cap: dict[str, Any] | None) -> bool:
    """True when the official stop is a positive call count, not a wallet."""
    cap = cost_cap or {}
    try:
        call_cap = int(cap.get("call_cap"))
    except (TypeError, ValueError):
        return False
    return (
        cap.get("status") == "confirmed"
        and cap.get("unit") == "calls"
        and call_cap > 0
    )


def _unit_price_is_verified(price: dict[str, Any] | None) -> bool:
    """True only when both input and output prices are present and sourced."""
    if not price:
        return False
    return (
        price.get("source") == "verified"
        and price.get("input_per_million") is not None
        and price.get("output_per_million") is not None
    )


def conservative_unit_prices(models: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Max verified input/output across models. Mixed currencies are rejected."""
    prices = [model.get("unit_price") or {} for model in models]
    if not prices or not all(_unit_price_is_verified(price) for price in prices):
        return None
    currencies = {price.get("currency") for price in prices}
    if len(currencies) != 1:
        logger.warning("Refusing a price envelope over mixed currencies %s", currencies)
        return None
    envelope = {
        "input_per_million": max(float(price["input_per_million"]) for price in prices),
        "output_per_million": max(float(price["output_per_million"]) for price in prices),
        "currency": next(iter(currencies)),
        "source": "verified",
        "note": "Envelope is the max verified input/output across models.",
    }
    logger.info(
        "Conservative unit prices input=%s output=%s %s",
        envelope["input_per_million"],
        envelope["output_per_million"],
        envelope["currency"],
    )
    return envelope


def _price_kwargs_from_freeze(freeze: dict[str, Any]) -> dict[str, Any]:
    """Copy per-model tariffs from a freeze into RunStore initialize kwargs."""
    by_model: dict[str, Any] = {}
    for model in freeze.get("models") or []:
        model_id = model.get("id")
        if model_id:
            by_model[str(model_id)] = model.get("unit_price")
    return {
        "unit_prices_by_model": by_model or None,
        "this_run_currency": (freeze.get("cost_cap") or {}).get("currency"),
    }


def _ready_for_official_run(config: dict[str, Any], source_hashes: dict[str, Any]) -> bool:
    """Official runs need verified models plus a money cap or a call cap."""
    models = config.get("models") or []
    models_ok = bool(models) and all(
        model.get("id") and model.get("availability") == "verified" for model in models
    )
    prices_ok = bool(models) and all(
        _unit_price_is_verified(model.get("unit_price")) for model in models
    )
    cap = config.get("cost_cap") or {}
    money_ok = (
        prices_ok
        and cap.get("status") == "confirmed"
        and cap.get("amount") is not None
    )
    quota_ok = call_cap_is_confirmed(cap)
    paid = config.get("paid_trial") or {}
    hashes_ok = all(source_hashes.get(key) for key in _SOURCE_HASH_KEYS)
    return bool(
        models_ok
        and hashes_ok
        and (money_ok or quota_ok)
        and paid.get("allowed") is True
    )


def build_freeze(config: dict[str, Any], source_hashes: dict[str, Any]) -> dict[str, Any]:
    """Assemble a freeze record and pin it with a canonical content hash.

    Args:
        config: Protocol, models, parameters, scoring, feedback, retry,
            human-review draw, cost cap, budget estimate, and paid-trial gate.
        source_hashes: SHA-256 pins for data, split, prompts, code, homology,
            and constraint-support files.

    Returns:
        Freeze mapping including `freeze_sha256` over the remaining fields.
    """
    missing_hashes = [key for key in _SOURCE_HASH_KEYS if key not in source_hashes]
    if missing_hashes:
        raise IncompleteFreezeError(f"source hashes missing {missing_hashes}")
    freeze = {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "protocol": config["protocol"],
        "code_version": config["code_version"],
        "git_commit": config.get("git_commit"),
        "data_sha256": source_hashes["data_sha256"],
        "split_sha256": source_hashes["split_sha256"],
        "prompt_sha256": source_hashes["prompt_sha256"],
        "code_sha256": source_hashes["code_sha256"],
        "homology_sha256": source_hashes["homology_sha256"],
        "constraint_support_sha256": source_hashes["constraint_support_sha256"],
        "models": config["models"],
        "parameters": config["parameters"],
        "scoring": config["scoring"],
        "feedback": config["feedback"],
        "retry": config["retry"],
        "human_review": config["human_review"],
        "cost_cap": config["cost_cap"],
        "budget_estimate": config["budget_estimate"],
        "paid_trial": config["paid_trial"],
        "ready_for_official_run": _ready_for_official_run(config, source_hashes),
    }
    freeze["freeze_sha256"] = freeze_content_sha256(freeze)
    logger.info(
        "Built freeze sha256=%s ready_for_official_run=%s",
        freeze["freeze_sha256"],
        freeze["ready_for_official_run"],
    )
    return freeze


def validate_resume(existing: dict[str, Any], current: dict[str, Any]) -> None:
    """Refuse resume unless both freeze records hash-check then match.

    Integrity is checked first so a rewritten `freeze_sha256` cannot hide a
    field edit. Matching hashes then mean the pinned content is identical.
    """
    _require_freeze_fields(existing)
    _require_freeze_fields(current)
    existing_hash = freeze_content_sha256(existing)
    current_hash = freeze_content_sha256(current)
    if existing.get("freeze_sha256") != existing_hash:
        logger.error("Existing freeze hash does not match its content")
        raise FreezeConflictError("existing freeze hash does not match content")
    if current.get("freeze_sha256") != current_hash:
        logger.error("Current freeze hash does not match its content")
        raise FreezeConflictError("current freeze hash does not match content")
    if existing_hash != current_hash:
        logger.error(
            "Freeze mismatch existing=%s current=%s",
            existing.get("freeze_sha256"),
            current.get("freeze_sha256"),
        )
        raise FreezeConflictError("freeze content mismatch; refuse resume")
    logger.info("Freeze resume validated sha256=%s", existing_hash)
