"""Load and audit IFBench JSONL without rewriting source bytes.

Later split hashes and resume fingerprints pin the official file SHA-256. Any
in-place normalisation of prompts or newlines would make those pins lie.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Chunk size keeps hashing large JSONL files from loading the whole body at once.
_HASH_CHUNK_BYTES = 1024 * 1024
# Bump when the grouping or packing rule changes so old split hashes do not mix.
SPLIT_ALGORITHM_VERSION = "split-v1"


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


def _id_sort_key(item_id: str) -> tuple[int, int | str]:
    """Sort numeric IFBench keys in numeric order, then any non-numeric ids."""
    return (0, int(item_id)) if item_id.isdigit() else (1, item_id)


def normalize_prompt(text: str) -> str:
    """Collapse unicode compatibility forms and whitespace for grouping only.

    The original JSONL is never rewritten; this string is only a grouping key so
    trailing-space copies of the same task stay in one split.
    """
    text = unicodedata.normalize("NFKC", text.replace("\u00a0", " "))
    return re.sub(r"\s+", " ", text).strip()


def load_homology(path: Path, known_ids: set[str]) -> list[list[str]]:
    """Load the human homology map and reject groups that cannot be audited.

    Args:
        path: JSON file with `{groups: [{ids, rationale}, ...]}`.
        known_ids: Dataset keys; unknown ids fail instead of silently dropping.

    Returns:
        Homology groups as lists of ids.

    Raises:
        ValueError: Missing rationale, unknown id, or an id in multiple groups.
    """
    path = Path(path)
    logger.info("Loading homology map from %s", path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    groups: list[list[str]] = []
    seen: set[str] = set()
    for entry in payload.get("groups", []):
        ids = [str(item_id) for item_id in entry.get("ids", [])]
        rationale = entry.get("rationale")
        if not rationale or not str(rationale).strip():
            raise ValueError("homology group missing rationale")
        if len(ids) < 2:
            raise ValueError(f"homology group must contain at least two ids: {ids}")
        for item_id in ids:
            if item_id not in known_ids:
                raise ValueError(f"homology id {item_id} is not in the dataset")
            if item_id in seen:
                raise ValueError(f"homology id {item_id} appears in multiple groups")
            seen.add(item_id)
        groups.append(ids)
        logger.info("Homology group %s (%s)", ids, str(rationale)[:80])
    logger.info("Loaded %s homology groups covering %s ids", len(groups), len(seen))
    return groups


def build_groups(
    items: list[dict[str, Any]], homology_groups: list[list[str]]
) -> list[list[str]]:
    """Union normalized-prompt duplicates with the human homology map.

    Items that match neither rule stay as singletons tagged unknown later. The
    union-find merge keeps a related pair together even if only one link exists.
    """
    ids = [str(item["key"]) for item in items]
    parent = {item_id: item_id for item_id in ids}

    def find(item_id: str) -> str:
        while parent[item_id] != item_id:
            parent[item_id] = parent[parent[item_id]]
            item_id = parent[item_id]
        return item_id

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left == root_right:
            return
        if _id_sort_key(root_left) <= _id_sort_key(root_right):
            parent[root_right] = root_left
        else:
            parent[root_left] = root_right

    buckets: dict[str, list[str]] = defaultdict(list)
    for item in items:
        buckets[normalize_prompt(item["prompt"])].append(str(item["key"]))
    n_normalized = 0
    for members in buckets.values():
        if len(members) < 2:
            continue
        n_normalized += 1
        for other in members[1:]:
            union(members[0], other)
    for group in homology_groups:
        for other in group[1:]:
            union(group[0], other)

    clustered: dict[str, list[str]] = defaultdict(list)
    for item_id in ids:
        clustered[find(item_id)].append(item_id)
    groups = [sorted(members, key=_id_sort_key) for members in clustered.values()]
    groups.sort(key=lambda group: tuple(_id_sort_key(item_id) for item_id in group))
    logger.info(
        "Built %s groups from %s items (%s normalized-duplicate buckets, %s homology groups)",
        len(groups),
        len(ids),
        n_normalized,
        len(homology_groups),
    )
    return groups


def assign_dev_groups(
    groups: list[list[str]], dev_target: int, seed: int
) -> tuple[list[str], list[str], str | None]:
    """Pack whole groups into dev with a seeded shuffle.

    First-fit in shuffled order never splits a group. If leftover slots are
    smaller than every remaining group, return the actual count and a reason
    instead of breaking a group to hit the quota.
    """
    canonical = [
        tuple(sorted((str(item_id) for item_id in group), key=_id_sort_key))
        for group in groups
    ]
    flat = [item_id for group in canonical for item_id in group]
    if len(flat) != len(set(flat)):
        raise ValueError("duplicate ids across groups")
    shuffled = sorted(canonical)
    rng = random.Random(seed)
    rng.shuffle(shuffled)
    dev_groups: list[tuple[str, ...]] = []
    n_dev = 0
    for group in shuffled:
        if n_dev + len(group) <= dev_target:
            dev_groups.append(group)
            n_dev += len(group)
    dev_group_set = set(dev_groups)
    test_groups = [group for group in shuffled if group not in dev_group_set]
    dev_ids = sorted((item_id for group in dev_groups for item_id in group), key=_id_sort_key)
    test_ids = sorted((item_id for group in test_groups for item_id in group), key=_id_sort_key)
    reason = None
    if n_dev != dev_target:
        reason = (
            f"group sizes prevent exact dev_target={dev_target}; actual_dev={n_dev}"
        )
        logger.warning("Inexact split: %s", reason)
    else:
        logger.info("Exact split: seed=%s n_dev=%s n_test=%s", seed, n_dev, len(test_ids))
    return dev_ids, test_ids, reason


def split_groups(
    groups: list[list[str]], dev_target: int, seed: int
) -> tuple[list[str], list[str]]:
    """Return dev and test ids, keeping each group on one side."""
    dev_ids, test_ids, _reason = assign_dev_groups(groups, dev_target, seed)
    return dev_ids, test_ids


def validate_split(
    all_ids: set[str], dev: list[str], test: list[str]
) -> None:
    """Reject overlap, duplicates, or incomplete coverage."""
    expected = {str(item_id) for item_id in all_ids}
    if len(dev) != len(set(dev)):
        raise ValueError("duplicate ids in dev")
    if len(test) != len(set(test)):
        raise ValueError("duplicate ids in test")
    dev_set = {str(item_id) for item_id in dev}
    test_set = {str(item_id) for item_id in test}
    overlap = dev_set & test_set
    if overlap:
        raise ValueError(f"overlap between dev and test: {sorted(overlap)}")
    covered = dev_set | test_set
    if covered != expected:
        missing = expected - covered
        extra = covered - expected
        raise ValueError(f"cover mismatch missing={sorted(missing)} extra={sorted(extra)}")
    logger.info("Validated split n_dev=%s n_test=%s", len(dev_set), len(test_set))


def split_fingerprint(manifest: dict[str, Any]) -> str:
    """Hash the assignment, not local file paths, so two processes can compare."""
    payload = {
        "algorithm_version": manifest["algorithm_version"],
        "seed": manifest["seed"],
        "dev_target": manifest["dev_target"],
        "source_sha256": manifest["source_sha256"],
        "homology_sha256": manifest["homology_sha256"],
        "quota_reason": manifest["quota_reason"],
        "dev_ids": list(manifest["dev_ids"]),
        "test_ids": list(manifest["test_ids"]),
        "groups": manifest["groups"],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _constraint_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    audit = audit_items(items)
    return {
        "n_items": audit["n_items"],
        "n_single_constraint": audit["n_single_constraint"],
        "n_double_constraint": audit["n_double_constraint"],
        "n_constraint_types": len(audit["constraint_types"]),
        "constraint_counts": audit["constraint_counts"],
    }


def build_split_manifest(
    items: list[dict[str, Any]],
    source_path: Path,
    homology_path: Path,
    seed: int,
    dev_target: int,
) -> dict[str, Any]:
    """Build an auditable split record without touching the source JSONL."""
    source_path = Path(source_path)
    homology_path = Path(homology_path)
    known_ids = {str(item["key"]) for item in items}
    homology_groups = load_homology(homology_path, known_ids)
    groups = build_groups(items, homology_groups)
    homology_ids = {item_id for group in homology_groups for item_id in group}
    norm_buckets: dict[str, list[str]] = defaultdict(list)
    for item in items:
        norm_buckets[normalize_prompt(item["prompt"])].append(str(item["key"]))
    normalized_duplicate_groups = [
        sorted(members, key=_id_sort_key)
        for members in norm_buckets.values()
        if len(members) > 1
    ]
    unknown_ids = []
    for group in groups:
        if len(group) == 1 and group[0] not in homology_ids:
            unknown_ids.append(group[0])
    dev_ids, test_ids, reason = assign_dev_groups(groups, dev_target, seed)
    validate_split(known_ids, dev_ids, test_ids)
    by_id = {str(item["key"]): item for item in items}
    manifest: dict[str, Any] = {
        "algorithm_version": SPLIT_ALGORITHM_VERSION,
        "seed": seed,
        "dev_target": dev_target,
        "source_path": source_path.as_posix(),
        "source_sha256": file_sha256(source_path),
        "homology_path": homology_path.as_posix(),
        "homology_sha256": file_sha256(homology_path),
        "quota_reason": reason,
        "n_dev": len(dev_ids),
        "n_test": len(test_ids),
        "dev_ids": dev_ids,
        "test_ids": test_ids,
        "groups": groups,
        "human_homology_groups": [sorted(group, key=_id_sort_key) for group in homology_groups],
        "normalized_duplicate_groups": normalized_duplicate_groups,
        "unknown_ids": unknown_ids,
        "dev_summary": _constraint_summary([by_id[item_id] for item_id in dev_ids]),
        "test_summary": _constraint_summary([by_id[item_id] for item_id in test_ids]),
        "dev_constraint_counts": _constraint_summary(
            [by_id[item_id] for item_id in dev_ids]
        )["constraint_counts"],
        "test_constraint_counts": _constraint_summary(
            [by_id[item_id] for item_id in test_ids]
        )["constraint_counts"],
    }
    manifest["split_sha256"] = split_fingerprint(manifest)
    logger.info(
        "Split manifest fingerprint=%s n_dev=%s n_test=%s unknown=%s",
        manifest["split_sha256"],
        manifest["n_dev"],
        manifest["n_test"],
        len(unknown_ids),
    )
    return manifest


def write_split_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write the split record as sorted JSON. Does not modify the source JSONL."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote split manifest to %s", path)
