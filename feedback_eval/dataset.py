"""Load and audit IFBench JSONL without rewriting source bytes.

Later split hashes and resume fingerprints pin the official file SHA-256. Any
in-place normalisation of prompts or newlines would make those pins lie.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Chunk size keeps hashing large JSONL files from loading the whole body at once.
_HASH_CHUNK_BYTES = 1024 * 1024


def file_sha256(path: Path) -> str:
    """Return the SHA-256 of the file as stored on disk.

    Args:
        path: JSONL (or any) file to hash.

    Returns:
        Lowercase hex digest of the raw bytes.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    hexdigest = digest.hexdigest()
    logger.info("Hashed %s -> %s", path, hexdigest)
    return hexdigest


def load_items(path: Path) -> list[dict[str, Any]]:
    """Parse JSONL items in file order.

    Blank lines are treated as record separators, not items. Invalid JSON and a
    missing file raise immediately so callers cannot continue on a partial set.

    Args:
        path: Path to an IFBench-style JSONL file.

    Returns:
        Parsed objects in the same order as the file.

    Raises:
        FileNotFoundError: If `path` does not exist.
        json.JSONDecodeError: If a non-empty line is not valid JSON.
    """
    path = Path(path)
    logger.info("Loading items from %s", path)
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                logger.error("Invalid JSON in %s at line %s", path, line_number)
                raise
            items.append(item)
    logger.info("Loaded %s items from %s", len(items), path)
    return items


def audit_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise inventory, exact-text duplicates, and constraint mix.

    Duplicate detection uses the raw `key` and `prompt` fields. Whitespace
    variants stay distinct so the two official JSONL copies cannot be confused.

    Args:
        items: Parsed IFBench rows.

    Returns:
        Audit dictionary with item counts, duplicate IDs/prompts, single/double
        constraint counts, unique constraint types, and per-type frequencies.
    """
    logger.info("Auditing %s items", len(items))
    id_counts: Counter[str] = Counter(str(item["key"]) for item in items)
    prompt_counts: Counter[str] = Counter(item["prompt"] for item in items)
    constraint_counts: Counter[str] = Counter()
    n_single = 0
    n_double = 0
    for item in items:
        instructions = list(item["instruction_id_list"])
        n_constraints = len(instructions)
        if n_constraints == 1:
            n_single += 1
        elif n_constraints == 2:
            n_double += 1
        constraint_counts.update(instructions)

    report: dict[str, Any] = {
        "n_items": len(items),
        "duplicate_ids": sorted(
            item_id for item_id, count in id_counts.items() if count > 1
        ),
        "duplicate_prompts": sorted(
            prompt for prompt, count in prompt_counts.items() if count > 1
        ),
        "n_single_constraint": n_single,
        "n_double_constraint": n_double,
        "constraint_types": set(constraint_counts),
        "constraint_counts": dict(constraint_counts),
    }
    logger.info(
        "Audit: n_items=%s duplicate_ids=%s n_single=%s n_double=%s n_types=%s",
        report["n_items"],
        len(report["duplicate_ids"]),
        n_single,
        n_double,
        len(report["constraint_types"]),
    )
    return report
