"""Split and grouping tests.

Task 2 requires whole groups to stay together, a deterministic seed, and an
explicit record when group sizes make the 40-item quota impossible. Homology
is a separate audited map; unknown pairs must not be treated as independent.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from feedback_eval.dataset import (
    SPLIT_ALGORITHM_VERSION,
    assign_dev_groups,
    audit_items,
    build_groups,
    build_split_manifest,
    file_sha256,
    load_homology,
    load_items,
    normalize_prompt,
    split_fingerprint,
    split_groups,
    validate_split,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OFFICIAL_TEST_JSONL = _REPO_ROOT / "data" / "IFBench_test.jsonl"
_OFFICIAL_SHA256 = "d2ada7da94a38cfe406351614c4e686846ed2da6d1b339db95fa5ead19554a4a"
_HOMOLOGY_PATH = _REPO_ROOT / "configs" / "feedback_eval" / "homology.json"


def _item(key: str, prompt: str, instruction_ids: list[str]) -> dict:
    return {
        "key": key,
        "prompt": prompt,
        "instruction_id_list": instruction_ids,
        "kwargs": [{} for _ in instruction_ids],
    }


def test_same_seed_yields_same_split() -> None:
    groups = [["1", "2"], ["3"], ["4", "5"], ["6"], ["7", "8", "9"]]
    first = split_groups(groups, dev_target=4, seed=42)
    second = split_groups(groups, dev_target=4, seed=42)
    assert first == second


def test_split_does_not_break_groups() -> None:
    groups = [["10", "11"], ["12"], ["13", "14", "15"], ["16"]]
    dev, test = split_groups(groups, dev_target=4, seed=42)
    assigned = {"dev": set(dev), "test": set(test)}
    for group in groups:
        homes = {name for name, bucket in assigned.items() if set(group) & bucket}
        assert homes == {"dev"} or homes == {"test"}


def test_validate_split_rejects_overlap_and_requires_full_cover() -> None:
    all_ids = {"1", "2", "3"}
    validate_split(all_ids, ["1"], ["2", "3"])
    with pytest.raises(ValueError, match="overlap"):
        validate_split(all_ids, ["1", "2"], ["2", "3"])
    with pytest.raises(ValueError, match="cover"):
        validate_split(all_ids, ["1"], ["2"])
    with pytest.raises(ValueError, match="duplicate"):
        validate_split(all_ids, ["1", "1"], ["2", "3"])


def test_inexact_quota_returns_actual_size_and_reason() -> None:
    """Size-3 groups cannot sum to 4; the leftover slot must stay empty."""
    groups = [["a", "b", "c"], ["d", "e", "f"], ["g", "h", "i"]]
    dev, test, reason = assign_dev_groups(groups, dev_target=4, seed=42)
    assert len(dev) != 4
    assert len(dev) + len(test) == 9
    assert reason is not None
    assert "4" in reason
    assert str(len(dev)) in reason


def test_build_groups_merges_normalized_duplicates_not_raw_whitespace() -> None:
    items = [
        _item("1", "Hello   world", ["count:conjunctions"]),
        _item("2", "Hello world", ["count:keywords_multiple"]),
        _item("3", "Hello world ", ["count:numbers"]),
        _item("4", "other", ["count:pronouns"]),
    ]
    assert normalize_prompt("Hello   world") == normalize_prompt("Hello world ")
    groups = build_groups(items, homology_groups=[])
    grouped = {frozenset(group) for group in groups}
    assert frozenset({"1", "2", "3"}) in grouped
    assert frozenset({"4"}) in grouped


def test_build_groups_applies_human_homology_and_leaves_unknown_as_singletons() -> None:
    items = [
        _item("1", "task A reverse letters", ["custom:character_reverse"]),
        _item("2", "task A reverse words", ["custom:word_reverse"]),
        _item("3", "unrelated", ["count:conjunctions"]),
    ]
    groups = build_groups(items, homology_groups=[["1", "2"]])
    grouped = {frozenset(group) for group in groups}
    assert frozenset({"1", "2"}) in grouped
    assert frozenset({"3"}) in grouped


def test_load_homology_requires_rationale_and_known_ids(tmp_path: Path) -> None:
    path = tmp_path / "homology.json"
    path.write_text(json.dumps({"groups": [{"ids": ["1", "2"]}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="rationale"):
        load_homology(path, known_ids={"1", "2"})
    path.write_text(
        json.dumps(
            {
                "groups": [
                    {"ids": ["1", "99"], "rationale": "missing id should fail"}
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="99"):
        load_homology(path, known_ids={"1", "2"})


def test_manifest_records_hashes_groups_and_distributions(tmp_path: Path) -> None:
    source = tmp_path / "items.jsonl"
    rows = [
        _item("1", "alpha", ["count:conjunctions"]),
        _item("2", "beta", ["count:conjunctions", "count:numbers"]),
        _item("3", "gamma", ["count:numbers"]),
        _item("4", "delta", ["format:emoji"]),
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    items = load_items(source)
    homology = tmp_path / "homology.json"
    homology.write_text(
        json.dumps(
            {
                "version": "homology-v1",
                "groups": [
                    {
                        "ids": ["1", "3"],
                        "rationale": "Fixture pair for manifest hashing.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = build_split_manifest(
        items=items,
        source_path=source,
        homology_path=homology,
        seed=42,
        dev_target=2,
    )
    assert manifest["algorithm_version"] == SPLIT_ALGORITHM_VERSION
    assert manifest["source_sha256"] == file_sha256(source)
    assert manifest["homology_sha256"] == file_sha256(homology)
    assert set(manifest["dev_ids"]) | set(manifest["test_ids"]) == {"1", "2", "3", "4"}
    validate_split({"1", "2", "3", "4"}, manifest["dev_ids"], manifest["test_ids"])
    assert "dev_constraint_counts" in manifest
    assert "test_constraint_counts" in manifest
    assert "split_sha256" in manifest
    assert manifest["split_sha256"] == split_fingerprint(manifest)
    # Group {1,3} must share a split because homology merged them.
    together = set(manifest["dev_ids"])
    assert ({"1", "3"} <= together) or ({"1", "3"}.isdisjoint(together))


def test_official_jsonl_bytes_unchanged_by_split_helpers() -> None:
    before = file_sha256(_OFFICIAL_TEST_JSONL)
    items = load_items(_OFFICIAL_TEST_JSONL)
    homology_groups = load_homology(_HOMOLOGY_PATH, known_ids={str(i["key"]) for i in items})
    build_groups(items, homology_groups)
    assert file_sha256(_OFFICIAL_TEST_JSONL) == before == _OFFICIAL_SHA256


def test_manifest_hash_matches_in_a_fresh_process() -> None:
    items = load_items(_OFFICIAL_TEST_JSONL)
    homology_groups = load_homology(
        _HOMOLOGY_PATH, known_ids={str(item["key"]) for item in items}
    )
    local = build_split_manifest(
        items=items,
        source_path=_OFFICIAL_TEST_JSONL,
        homology_path=_HOMOLOGY_PATH,
        seed=42,
        dev_target=40,
    )
    script = (
        "from pathlib import Path\n"
        "from feedback_eval.dataset import build_split_manifest, load_items\n"
        "root = Path(r'''%s''')\n"
        "manifest = build_split_manifest(\n"
        "    items=load_items(root / 'data' / 'IFBench_test.jsonl'),\n"
        "    source_path=root / 'data' / 'IFBench_test.jsonl',\n"
        "    homology_path=root / 'configs' / 'feedback_eval' / 'homology.json',\n"
        "    seed=42,\n"
        "    dev_target=40,\n"
        ")\n"
        "print(manifest['split_sha256'])\n"
        % _REPO_ROOT
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert proc.stdout.strip() == local["split_sha256"]


def test_candidate_official_split_covers_all_ids() -> None:
    items = load_items(_OFFICIAL_TEST_JSONL)
    report = audit_items(items)
    manifest = build_split_manifest(
        items=items,
        source_path=_OFFICIAL_TEST_JSONL,
        homology_path=_HOMOLOGY_PATH,
        seed=42,
        dev_target=40,
    )
    validate_split(
        {str(item["key"]) for item in items},
        manifest["dev_ids"],
        manifest["test_ids"],
    )
    assert report["n_items"] == 300
    # Exact 40 is expected because homology groups are size 2 and the rest are singletons.
    assert len(manifest["dev_ids"]) == 40
    assert len(manifest["test_ids"]) == 260
    assert manifest["quota_reason"] is None


def test_frozen_split_json_matches_rebuild() -> None:
    """The checked-in candidate split must stay a pure function of data + homology + seed."""
    frozen_path = _REPO_ROOT / "configs" / "feedback_eval" / "split.json"
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    rebuilt = build_split_manifest(
        items=load_items(_OFFICIAL_TEST_JSONL),
        source_path=_OFFICIAL_TEST_JSONL,
        homology_path=_HOMOLOGY_PATH,
        seed=42,
        dev_target=40,
    )
    assert frozen["split_sha256"] == rebuilt["split_sha256"]
    assert frozen["dev_ids"] == rebuilt["dev_ids"]
    assert frozen["test_ids"] == rebuilt["test_ids"]
