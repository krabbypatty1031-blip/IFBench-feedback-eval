"""Wrap official IFBench strict/loose scoring without copying the rules.

The official strict scorer mutates kwargs in place (drops nulls) before loose
runs in `run_eval.py`. Callers here always receive a copy so experiment records
keep the original JSONL fields. Verifier exceptions stay errors, not fails.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable

import evaluation_lib

logger = logging.getLogger(__name__)


def _filtered_kwargs(kwargs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop null values the way `test_instruction_following_strict` does."""
    return [
        {key: value for key, value in row.items() if value is not None}
        for row in kwargs
    ]


def item_to_input_example(item: dict[str, Any]) -> evaluation_lib.InputExample:
    """Build an official InputExample from a JSONL row without sharing kwargs."""
    return evaluation_lib.InputExample(
        key=item["key"],
        instruction_id_list=list(item["instruction_id_list"]),
        prompt=item["prompt"],
        kwargs=_filtered_kwargs(copy.deepcopy(item["kwargs"])),
    )


def _run_official(
    fn: Callable,
    item: dict[str, Any],
    prompt_to_response: dict[str, str],
) -> dict[str, Any]:
    inp = item_to_input_example(item)
    try:
        output = fn(inp, prompt_to_response)
    except Exception as exc:
        logger.exception(
            "Verifier error key=%s scorer=%s", item.get("key"), fn.__name__
        )
        return {
            "all_pass": None,
            "per_constraint": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    logger.info(
        "Scored key=%s scorer=%s all_pass=%s per_constraint=%s",
        item.get("key"),
        fn.__name__,
        output.follow_all_instructions,
        output.follow_instruction_list,
    )
    return {
        "all_pass": bool(output.follow_all_instructions),
        "per_constraint": list(output.follow_instruction_list),
        "error": None,
    }


def score(item: dict[str, Any], response: str) -> dict[str, Any]:
    """Return strict and loose results for one item/response pair.

    Args:
        item: IFBench JSONL object. Not mutated.
        response: Model text to score. Empty text is a failure, not an error.

    Returns:
        Dict with `strict` and `loose` records. Each record has `all_pass`,
        `per_constraint`, and `error`. `error` is set only when a verifier
        raises; `all_pass` is then None so callers cannot treat it as fail.
    """
    prompt_to_response = {item["prompt"]: response}
    logger.info(
        "Scoring key=%s n_constraints=%s response_chars=%s",
        item.get("key"),
        len(item["instruction_id_list"]),
        len(response or ""),
    )
    return {
        "instruction_id_list": list(item["instruction_id_list"]),
        "strict": _run_official(
            evaluation_lib.test_instruction_following_strict,
            item,
            prompt_to_response,
        ),
        "loose": _run_official(
            evaluation_lib.test_instruction_following_loose,
            item,
            prompt_to_response,
        ),
    }
