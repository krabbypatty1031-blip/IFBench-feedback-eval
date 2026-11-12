"""Scheduling, resume, and quality summaries.

Live HTTP clients are never constructed here. The CLI injects FakeClient or a
pre-authorized OpenAICompatClient. Missing records stay missing; they are not
successes or zero-cost rows.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from feedback_eval.budget_guard import BudgetUnverified, allow_wallet_reservation
from feedback_eval.client import FakeClient
from feedback_eval.runner import CONDITIONS, run_item
from feedback_eval.store import record_key

logger = logging.getLogger(__name__)


class RehearsalLimit(Exception):
    """The simulated call allowance is exhausted."""


class BudgetExhausted(RuntimeError):
    """The next call's worst-case tariff would exceed the live wallet cap."""


class _LimitedFake:
    def __init__(self, client: FakeClient, allowance: int):
        if type(client) is not FakeClient:
            raise TypeError("rehearsal accepts FakeClient only")
        if type(allowance) is not int or allowance < 0:
            raise ValueError("allowance must be a non-negative integer")
        self.client, self.allowance, self.calls = client, allowance, 0

    def generate(self, messages: list[dict], settings: dict) -> dict:
        if self.calls >= self.allowance:
            raise RehearsalLimit("simulated call allowance exhausted")
        self.calls += 1
        return self.client.generate(messages, settings)


class BudgetedClient:
    """Refuse generate() when min(cap, wallet) cannot cover a conservative hold."""

    def __init__(
        self,
        inner: Any,
        store: Any,
        model: str,
        unit_price: dict[str, Any],
        cost_cap: float,
        wallet: float,
        max_tokens: int,
        input_hold_tokens: int = 8192,
    ):
        self._inner = inner
        self._store = store
        self.model = model
        self.unit_price = unit_price
        self.cost_cap = cost_cap
        self.wallet = wallet
        self.max_tokens = max_tokens
        self.input_hold_tokens = input_hold_tokens

    def _worst_case(self, settings: dict[str, Any]) -> float:
        max_tokens = int(settings.get("max_tokens") or self.max_tokens)
        return (
            self.input_hold_tokens * float(self.unit_price["input_per_million"])
            + max_tokens * float(self.unit_price["output_per_million"])
        ) / 1_000_000.0

    def generate(self, messages: list[dict], settings: dict) -> dict:
        worst = self._worst_case(settings)
        with self._store.budget_lock():
            ledger = self._store.load_ledger()
            if int(ledger.get("pending_unverified") or 0) > 0:
                raise BudgetExhausted("pending_unverified_cost")
            spent = ledger.get("spent_this_run") or {}
            if spent.get("amount") is None:
                raise BudgetUnverified("this-run spend is unknown")
            if not allow_wallet_reservation(
                self.cost_cap, self.wallet, spent["amount"], 0, worst
            ):
                logger.warning(
                    "Budget exhausted model=%s spent=%s worst=%s cap=%s wallet=%s",
                    self.model,
                    spent["amount"],
                    worst,
                    self.cost_cap,
                    self.wallet,
                )
                raise BudgetExhausted("cost_cap")
        return self._inner.generate(messages, settings)


def reconcile(items: list[dict], models: list[str], config: dict) -> dict[str, Any]:
    """Count persisted identities and explicitly classify absent records."""
    counts: Counter = Counter()
    store = config["store"]
    protocol = config.get("protocol", "feedback-eval-dev-v1")
    repeat = config.get("repeat", 0)
    for model in models:
        for item in items:
            for condition in CONDITIONS:
                key = record_key(protocol, model, str(item["key"]), condition, repeat)
                record = store.get_record(key)
                if record is None:
                    counts["missing"] += 1
                elif record.get("feedback_error"):
                    counts["feedback_unavailable"] += 1
                else:
                    counts[record.get("accounting_status", "unclassified")] += 1
    expected = len(items) * len(models) * len(CONDITIONS)
    return {"expected": expected, "statuses": dict(counts), "accounted": sum(counts.values())}


def summarize(items: list[dict], models: list[str], config: dict) -> dict[str, Any]:
    """Link official scores to stored answers and count incomplete cost rows.

    Generation accounting (success/timeout/empty) is separate from verifier
    pass/fail. Missing identities stay in `missing` and are not successes.
    """
    base = reconcile(items, models, config)
    store = config["store"]
    protocol = config.get("protocol", "feedback-eval-dev-v1")
    repeat = int(config.get("repeat", 0))
    links: list[dict[str, Any]] = []
    cost_unknown = 0
    for model in models:
        for item in items:
            item_id = str(item["key"])
            for condition in CONDITIONS:
                key = record_key(protocol, model, item_id, condition, repeat)
                record = store.get_record(key)
                if record is None:
                    cost_unknown += 1
                    links.append(
                        {
                            "item_id": item_id,
                            "model": model,
                            "condition": condition,
                            "record_key": key,
                            "accounting_status": "missing",
                            "strict_all_pass": None,
                            "loose_all_pass": None,
                            "has_text": False,
                            "cost_status": "unknown",
                        }
                    )
                    continue
                score = record.get("score") or {}
                generation = record.get("generation") or {}
                cost = record.get("cost") or {}
                if cost.get("status") != "metered":
                    cost_unknown += 1
                text = generation.get("text") or ""
                links.append(
                    {
                        "item_id": item_id,
                        "model": model,
                        "condition": condition,
                        "record_key": key,
                        "accounting_status": record.get("accounting_status")
                        or "unclassified",
                        "strict_all_pass": (score.get("strict") or {}).get("all_pass"),
                        "loose_all_pass": (score.get("loose") or {}).get("all_pass"),
                        "has_text": bool(str(text).strip()),
                        "cost_status": cost.get("status") or "unknown",
                    }
                )
    statuses = base["statuses"]
    return {
        **base,
        "success": statuses.get("success", 0),
        "truncated": statuses.get("truncated", 0),
        "empty_response": statuses.get("empty_response", 0),
        "missing": statuses.get("missing", 0),
        "generation_failures": (
            statuses.get("error", 0)
            + statuses.get("timeout", 0)
            + statuses.get("rate_limit", 0)
        ),
        "cost_unknown": cost_unknown,
        "score_links": links,
        "incomplete": statuses.get("missing", 0) > 0,
    }


def rehearse(items: list[dict], models: list[str], client: FakeClient,
             config: dict, allowance: int) -> dict[str, Any]:
    """Serial rehearsal with a per-invocation simulated call cap and resumable store."""
    if len(models) != 2 or len(set(models)) != 2 or not all(models):
        raise ValueError("exactly two distinct model identifiers required")
    if len({str(item["key"]) for item in items}) != len(items):
        raise ValueError("duplicate item ids")
    limited = _LimitedFake(client, allowance)
    stopped = None
    try:
        for model in models:
            for item in items:
                run_item(item, limited, {**config, "model": model})
    except RehearsalLimit:
        stopped = "simulated_call_limit"
    result = reconcile(items, models, config)
    return {**result, "calls_this_invocation": limited.calls, "stopped": stopped,
            "offline_only": True}


def run_schedule(
    items: list[dict],
    models: list[str],
    clients: dict[str, Any],
    config: dict[str, Any],
    on_progress: Any | None = None,
) -> dict[str, Any]:
    """Run B0–B3 for each model/item. Resume skips stored identities.

    Intermediate scores never change prompts, item order, or the model list.
    """
    if len(models) != 2 or len(set(models)) != 2 or not all(models):
        raise ValueError("exactly two distinct model identifiers required")
    if len({str(item["key"]) for item in items}) != len(items):
        raise ValueError("duplicate item ids")
    missing_clients = [model for model in models if model not in clients]
    if missing_clients:
        raise ValueError(f"missing clients for {missing_clients}")
    stopped = None
    calls = 0
    logger.info(
        "Starting schedule n_items=%s n_models=%s expected=%s",
        len(items),
        len(models),
        len(items) * len(models) * len(CONDITIONS),
    )
    try:
        for model in models:
            for item in items:
                result = run_item(item, clients[model], {**config, "model": model})
                calls += int(result.get("n_client_calls") or 0)
                if on_progress is not None:
                    on_progress(summarize(items, models, config), calls)
    except (RehearsalLimit, BudgetExhausted, BudgetUnverified) as exc:
        stopped = getattr(exc, "args", [type(exc).__name__])[0]
        if not isinstance(stopped, str):
            stopped = type(exc).__name__
        logger.warning("Schedule stopped reason=%s calls=%s", stopped, calls)
    summary = summarize(items, models, config)
    logger.info(
        "Schedule finished expected=%s accounted=%s missing=%s stopped=%s",
        summary["expected"],
        summary["accounted"],
        summary["missing"],
        stopped,
    )
    return {
        **summary,
        "calls_this_invocation": calls,
        "stopped": stopped,
    }
