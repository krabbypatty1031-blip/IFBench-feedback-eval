"""Map official strict results onto B2/B3 feedback text.

Requirements come from the prompt surface, not from dumping verifier kwargs.
If a constraint cannot be located in the prompt, raise instead of guessing.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ifbench import instructions_registry

logger = logging.getLogger(__name__)

# Classic IFEval prefixes are not in the 58 OOD types this experiment uses.
_CLASSIC_PREFIXES = (
    "keywords:",
    "language:",
    "length_constraints:",
    "detectable_content:",
    "detectable_format:",
    "combination:",
    "startend:",
    "change_case:",
    "punctuation:",
)

# Extra packages beyond NLTK, recorded for the 58-type support catalog.
_EXTRA_RESOURCES = {
    "format:emoji": ["emoji"],
    "words:odd_even_syllables": ["syllapy"],
    "custom:csv_city": ["csv"],
    "custom:csv_quotes": ["csv"],
    "custom:csv_special_character": ["csv"],
}

# Prompt phrasing that differs from Instruction.build_description but is still
# the same user-facing requirement. Each pair was checked on the official JSONL.
_PHRASE_VARIANTS = (
    (
        "Ensure that the ratio of sentence types (declarative, interrogative, exclamatory) is balanced.",
        "Ensure that the ratio of sentence types (declarative, interrogative, exclamatory) in your response is balanced.",
    ),
    (
        "Each sentence must contain exactly",
        "Each sentence in your response must contain exactly",
    ),
    (
        "Each word must start with the next letter of the alphabet",
        "Each word in your response must start with the next letter of the alphabet",
    ),
    (
        "The last word of each sentence must become the first word of the next sentence.",
        "In your response, the last word of each sentence must become the first word of the next sentence.",
    ),
    (
        "The last word of each sentence must become the first word of the next sentence.",
        "In your response the last word of each sentence must become the first word of the next sentence.",
    ),
    (
        "Each paragraph must end with the same word it started with, separate paragraphs with a newline.",
        "Each paragraph of your response must end with the same word it started with, separate paragraphs with a newline.",
    ),
    (
        "Maintain a 2:1 ratio of declarative to interrogative sentences.",
        "Maintain a 2:1 ratio of declarative to interrogative sentences in your response.",
    ),
)


class FeedbackError(Exception):
    """Feedback cannot be produced without inventing content."""


class ConstraintMappingError(FeedbackError):
    """A constraint has no unique natural-language span in the prompt."""


class VerifierFeedbackError(FeedbackError):
    """Verifier error must not be rewritten as pass or fail feedback."""


def _ordinal_hyphen(number: int) -> str:
    """Match prompt ordinals such as 22-nd and 3-rd, not verifier 22-th."""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}-{suffix}"


def _surface_forms(description: str) -> list[str]:
    """Enumerate audited prompt spellings of a verifier description."""
    forms = {description}
    pending = [description]
    while pending:
        text = pending.pop()
        variants = {
            re.sub(r"(\d+)\.0\b", r"\1", text),
            re.sub(r"(\d+)\.0th\b", r"\1th", text),
            re.sub(
                r"(\d+)(?:\.0)?-th",
                lambda match: _ordinal_hyphen(int(match.group(1))),
                text,
            ),
            re.sub(r'keyword "([^"]+)"', r"keyword \1", text),
            text.replace(
                "'20th century art history'", '"20th century art history"'
            ),
        }
        for source, target in _PHRASE_VARIANTS:
            if source in text:
                variants.add(text.replace(source, target))
        explanation = " Do not give any explanation."
        if text.endswith(explanation):
            variants.add(text[: -len(explanation)])
        if " Request: " in text:
            variants.add(text.split(" Request: ", 1)[0])
        for variant in variants:
            if variant and variant not in forms:
                forms.add(variant)
                pending.append(variant)
    return sorted(forms, key=len, reverse=True)


def _description_for_constraint(item: dict[str, Any], index: int) -> str:
    instruction_id = item["instruction_id_list"][index]
    if instruction_id not in instructions_registry.INSTRUCTION_DICT:
        raise ConstraintMappingError(f"unknown instruction id {instruction_id}")
    kwargs = {
        key: value
        for key, value in item["kwargs"][index].items()
        if value is not None
    }
    checker = instructions_registry.INSTRUCTION_DICT[instruction_id](instruction_id)
    return checker.build_description(**kwargs)


def extract_requirement(item: dict[str, Any], index: int) -> str:
    """Return the prompt substring that states constraint `index`.

    Raises:
        ConstraintMappingError: If no audited surface form appears in the prompt.
    """
    instruction_id = item["instruction_id_list"][index]
    description = _description_for_constraint(item, index)
    prompt = item["prompt"]
    matches = [form for form in _surface_forms(description) if form in prompt]
    if not matches:
        logger.error(
            "Unmapped constraint key=%s id=%s description=%s",
            item.get("key"),
            instruction_id,
            description,
        )
        raise ConstraintMappingError(
            f"cannot map {instruction_id} for item {item.get('key')} to prompt text"
        )
    requirement = max(matches, key=len)
    logger.info(
        "Mapped key=%s id=%s requirement=%s",
        item.get("key"),
        instruction_id,
        requirement[:80],
    )
    return requirement


def extract_requirements(item: dict[str, Any]) -> list[str]:
    """Map every constraint of an item, preserving instruction_id_list order."""
    return [
        extract_requirement(item, index)
        for index in range(len(item["instruction_id_list"]))
    ]


def make_feedback(item: dict[str, Any], strict: dict[str, Any], mode: str) -> str:
    """Build B2/B3 incremental feedback from a strict score record.

    Args:
        item: Original IFBench row; used only for prompt-side requirements.
        strict: `score()['strict']` record.
        mode: `B2` for overall status, `B3` for per-constraint status.

    Raises:
        VerifierFeedbackError: If the verifier did not produce a boolean result.
        ConstraintMappingError: If B3 cannot quote prompt text.
        ValueError: If `mode` is not B2 or B3.
    """
    if strict.get("error") or strict.get("all_pass") is None:
        raise VerifierFeedbackError(
            f"refusing to invent pass/fail feedback: {strict.get('error')}"
        )
    if mode == "B2":
        if strict["all_pass"]:
            text = "Verifier (strict): PASSED all output constraints."
        else:
            text = (
                "Verifier (strict): FAILED. At least one output constraint "
                "was not satisfied."
            )
        logger.info("B2 feedback key=%s all_pass=%s", item.get("key"), strict["all_pass"])
        return text
    if mode == "B3":
        requirements = extract_requirements(item)
        flags = list(strict["per_constraint"])
        if len(requirements) != len(flags):
            raise ConstraintMappingError(
                "constraint count does not match strict per_constraint length"
            )
        lines = [
            "Verifier (strict), one line per constraint, in listed order:"
        ]
        for index, (requirement, passed) in enumerate(zip(requirements, flags), start=1):
            status = "PASS" if passed else "FAIL"
            lines.append(f"{index}. {status}: {requirement}")
        text = "\n".join(lines)
        logger.info("B3 feedback key=%s n=%s", item.get("key"), len(flags))
        return text
    raise ValueError(f"unsupported feedback mode {mode}")


def _ood_instruction_ids() -> list[str]:
    return [
        instruction_id
        for instruction_id in instructions_registry.INSTRUCTION_DICT
        if not instruction_id.startswith(_CLASSIC_PREFIXES)
    ]


def audit_constraint_support(
    items: list[dict[str, Any]], dev_ids: set[str]
) -> dict[str, Any]:
    """Record mapping status and resources for all 58 OOD constraint types.

    Uses prompts only. Test-set items may be inspected structurally; their
    model outputs are not used to drop or keep a type.
    """
    ood_ids = _ood_instruction_ids()
    types: dict[str, dict[str, Any]] = {}
    for instruction_id in ood_ids:
        resources = ["nltk", *_EXTRA_RESOURCES.get(instruction_id, [])]
        types[instruction_id] = {
            "n_items": 0,
            "n_dev_items": 0,
            "mapping": "supported",
            "unmapped_keys": [],
            "resources": resources,
        }
    for item in items:
        item_id = str(item["key"])
        in_dev = item_id in dev_ids
        for index, instruction_id in enumerate(item["instruction_id_list"]):
            if instruction_id not in types:
                continue
            types[instruction_id]["n_items"] += 1
            if in_dev:
                types[instruction_id]["n_dev_items"] += 1
            try:
                extract_requirement(item, index)
            except ConstraintMappingError:
                types[instruction_id]["mapping"] = "unmapped"
                types[instruction_id]["unmapped_keys"].append(item_id)
                logger.error(
                    "Support audit unmapped key=%s id=%s", item_id, instruction_id
                )
    report = {
        "n_instruction_types": len(ood_ids),
        "mapping_policy": (
            "Locate an audited surface form of Instruction.build_description "
            "inside the prompt. Never emit raw kwargs as the requirement."
        ),
        "types": types,
    }
    n_supported = sum(row["mapping"] == "supported" for row in types.values())
    logger.info(
        "Constraint support: %s/%s types mapped", n_supported, len(ood_ids)
    )
    return report


def write_constraint_support(path: Path, report: dict[str, Any]) -> None:
    """Write the 58-type catalog as sorted JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote constraint support catalog to %s", path)
