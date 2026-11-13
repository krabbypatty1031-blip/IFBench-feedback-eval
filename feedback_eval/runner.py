"""B0 generation and independent B1–B3 revision branches.

B0 is a single call with the raw IFBench prompt. Each revision branch sees the
same initial answer and never sees the other branches. Passing B0 still revises.
Verifier failures are recorded; they do not become invented pass/fail text.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from feedback_eval.client import classify_generation, sanitize_settings
from feedback_eval.feedback import FeedbackError, make_feedback
from feedback_eval.scoring import score
from feedback_eval.store import record_key

logger = logging.getLogger(__name__)

PROMPTS_PATH = (
    Path(__file__).resolve().parents[1] / "configs" / "feedback_eval" / "prompts.json"
)
CONDITIONS = ("B0", "B1", "B2", "B3")


def load_prompt_templates(path: Path | None = None) -> dict[str, Any]:
    """Load the frozen English revision templates."""
    template_path = Path(path) if path is not None else PROMPTS_PATH
    logger.info("Loading prompt templates from %s", template_path)
    return json.loads(template_path.read_text(encoding="utf-8"))


def synthetic_conjunction_item(key: str, n: int = 2) -> dict[str, Any]:
    """Offline stand-in item. Not part of the official test split."""
    return {
        "key": str(key),
        "prompt": (
            f"Say hello. Use at least {n} different coordinating conjunctions "
            "in the response."
        ),
        "instruction_id_list": ["count:conjunctions"],
        "kwargs": [{"small_n": n}],
    }


def build_revision_user_message(
    templates: dict[str, Any],
    condition: str,
    verifier_feedback: str | None,
    passed: bool | None,
) -> str:
    """Shared revision instructions plus the condition-specific increment."""
    parts = [templates["revision_shared"].strip()]
    if condition == "B1":
        parts.append(templates["b1_increment"].strip())
    elif condition in {"B2", "B3"}:
        if verifier_feedback:
            parts.append(verifier_feedback.strip())
        if passed:
            parts.append(templates["allow_unchanged_if_passed"].strip())
    parts.append(templates["output_rule"].strip())
    return "\n\n".join(parts)


def _generation_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = {"model": config.get("model", "mock-model")}
    settings.update(config.get("parameters") or {})
    return sanitize_settings(settings)


def _score_text(item: dict[str, Any], text: str | None) -> dict[str, Any]:
    return score(item, text or "")


def _empty_generation(error: str) -> dict[str, Any]:
    return {
        "text": None,
        "usage": None,
        "finish_reason": None,
        "latency_ms": None,
        "error": error,
    }


def _with_accounting(record: dict[str, Any]) -> dict[str, Any]:
    """Classify the generation on the in-memory record, not only after persist.

    `put_record` copies the dict before writing `accounting_status`, so
    `run_item` return values and store-less dry runs would otherwise stay
    unclassified even though `reconcile()` keys off this field.
    """
    record["accounting_status"] = classify_generation(record.get("generation") or {})
    return record


def run_item(item: dict[str, Any], client: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Run B0 once, then B1–B3 as independent one-shot revisions.

    Args:
        item: IFBench-shaped row. Not mutated.
        client: Object with `generate(messages, settings)`.
        config: Protocol, model, parameters, prompt templates, optional store.

    Returns:
        Per-condition records plus `n_client_calls` for this invocation only.
    """
    templates = config.get("prompts") or load_prompt_templates()
    protocol = config.get("protocol", "feedback-eval-dev-v1")
    model = config.get("model", "mock-model")
    repeat = int(config.get("repeat", 0))
    store = config.get("store")
    settings = _generation_settings(config)
    item_id = str(item["key"])
    n_client_calls = 0
    logger.info("run_item key=%s model=%s", item_id, model)

    def load_or_none(condition: str) -> dict[str, Any] | None:
        if store is None:
            return None
        key = record_key(protocol, model, item_id, condition, repeat)
        return store.get_record(key)

    def persist(condition: str, record: dict[str, Any]) -> None:
        if store is None:
            return
        key = record_key(protocol, model, item_id, condition, repeat)
        store.put_record(key, record)

    b0_existing = load_or_none("B0")
    if b0_existing is not None:
        b0_record = b0_existing
        logger.info("Resumed B0 key=%s", item_id)
    else:
        b0_messages = [{"role": "user", "content": item["prompt"]}]
        b0_generation = client.generate(b0_messages, settings)
        n_client_calls += 1
        b0_record = _with_accounting(
            {
                "record_key": record_key(protocol, model, item_id, "B0", repeat),
                "item_id": item_id,
                "condition": "B0",
                "repeat": repeat,
                "messages": b0_messages,
                "generation": b0_generation,
                "score": _score_text(item, b0_generation.get("text")),
                "feedback": None,
                "feedback_error": None,
                "settings": settings,
            }
        )
        persist("B0", b0_record)
        logger.info(
            "Generated B0 key=%s status=%s",
            item_id,
            classify_generation(b0_generation),
        )

    b0_text = (b0_record.get("generation") or {}).get("text")
    b0_score = b0_record.get("score") or _score_text(item, b0_text)
    b0_strict = b0_score.get("strict") or {}

    results = {"B0": b0_record}
    for condition in ("B1", "B2", "B3"):
        existing = load_or_none(condition)
        if existing is not None:
            results[condition] = existing
            logger.info("Resumed %s key=%s", condition, item_id)
            continue
        feedback_text = None
        feedback_error = None
        passed = None
        if condition in {"B2", "B3"}:
            try:
                feedback_text = make_feedback(item, b0_strict, condition)
                passed = bool(b0_strict.get("all_pass"))
            except FeedbackError as exc:
                feedback_error = str(exc)
                logger.warning(
                    "No %s feedback for key=%s: %s", condition, item_id, exc
                )
        if feedback_error:
            record = _with_accounting(
                {
                    "record_key": record_key(protocol, model, item_id, condition, repeat),
                    "item_id": item_id,
                    "condition": condition,
                    "repeat": repeat,
                    "messages": None,
                    "generation": _empty_generation(
                        f"feedback_unavailable: {feedback_error}"
                    ),
                    "score": None,
                    "feedback": None,
                    "feedback_error": feedback_error,
                    "settings": settings,
                }
            )
            persist(condition, record)
            results[condition] = record
            continue
        user_revision = build_revision_user_message(
            templates, condition, feedback_text, passed
        )
        messages = [
            {"role": "user", "content": item["prompt"]},
            {"role": "assistant", "content": b0_text or ""},
            {"role": "user", "content": user_revision},
        ]
        generation = client.generate(messages, settings)
        n_client_calls += 1
        record = _with_accounting(
            {
                "record_key": record_key(protocol, model, item_id, condition, repeat),
                "item_id": item_id,
                "condition": condition,
                "repeat": repeat,
                "messages": messages,
                "generation": generation,
                "score": _score_text(item, generation.get("text")),
                "feedback": feedback_text,
                "feedback_error": None,
                "settings": settings,
            }
        )
        persist(condition, record)
        results[condition] = record
        logger.info(
            "Generated %s key=%s status=%s",
            condition,
            item_id,
            classify_generation(generation),
        )

    logger.info("Finished item %s client_calls=%s", item_id, n_client_calls)
    return {
        "item_id": item_id,
        "B0": results["B0"],
        "B1": results["B1"],
        "B2": results["B2"],
        "B3": results["B3"],
        "n_client_calls": n_client_calls,
    }
