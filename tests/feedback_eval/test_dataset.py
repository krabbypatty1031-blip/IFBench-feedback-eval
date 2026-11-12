"""Dataset loader and audit tests.

These tests pin Task 1 behaviour before `feedback_eval.dataset` exists: duplicate
IDs/prompts must surface, single vs double constraints must be counted from the
raw `instruction_id_list`, and source bytes must not be rewritten.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from feedback_eval.dataset import audit_items, file_sha256, load_items

# Frozen official test set; do not silently accept the packaged copy under ifbench/data/.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_OFFICIAL_TEST_JSONL = _REPO_ROOT / "data" / "IFBench_test.jsonl"
_OFFICIAL_SHA256 = "d2ada7da94a38cfe406351614c4e686846ed2da6d1b339db95fa5ead19554a4a"


def _item(key: str, prompt: str, instruction_ids: list[str]) -> dict:
    """Build a minimal IFBench-shaped row for fixtures."""
    return {
        "key": key,
        "prompt": prompt,
        "instruction_id_list": instruction_ids,
        "kwargs": [{} for _ in instruction_ids],
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    """Write JSONL without rewriting prompt text."""
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_load_items_fails_when_file_is_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.jsonl"
    with pytest.raises(FileNotFoundError):
        load_items(missing)


def test_load_items_fails_on_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_items(path)


def test_load_items_keeps_trailing_prompt_whitespace(tmp_path: Path) -> None:
    """Protocol forbids prompt normalisation; trailing spaces are identity, not noise."""
    path = tmp_path / "items.jsonl"
    prompt = "Keep trailing spaces.  "
    _write_jsonl(path, [_item("0", prompt, ["count:conjunctions"])])
    items = load_items(path)
    assert len(items) == 1
    assert items[0]["prompt"] == prompt
    assert items[0]["key"] == "0"


def test_audit_items_reports_duplicate_ids() -> None:
    items = [
        _item("1", "prompt-a", ["count:conjunctions"]),
        _item("1", "prompt-b", ["count:keywords_multiple"]),
        _item("2", "prompt-c", ["count:conjunctions"]),
    ]
    report = audit_items(items)
    assert report["n_items"] == 3
    assert report["duplicate_ids"] == ["1"]


def test_audit_items_reports_duplicate_prompts() -> None:
    items = [
        _item("1", "same prompt", ["count:conjunctions"]),
        _item("2", "same prompt", ["count:keywords_multiple"]),
        _item("3", "other prompt", ["count:conjunctions"]),
    ]
    report = audit_items(items)
    assert report["duplicate_prompts"] == ["same prompt"]


def test_audit_items_does_not_treat_whitespace_variants_as_duplicates() -> None:
    """Exact prompt bytes differ; collapsing spaces would hide the two official copies."""
    items = [
        _item("1", "hello", ["count:conjunctions"]),
        _item("2", "hello ", ["count:conjunctions"]),
    ]
    report = audit_items(items)
    assert report["duplicate_prompts"] == []


def test_audit_items_counts_single_and_double_constraints() -> None:
    items = [
        _item("1", "p1", ["count:conjunctions"]),
        _item("2", "p2", ["count:keywords_multiple", "length:words"]),
        _item("3", "p3", ["count:conjunctions"]),
        _item("4", "p4", ["length:words", "count:conjunctions"]),
    ]
    report = audit_items(items)
    assert report["n_single_constraint"] == 2
    assert report["n_double_constraint"] == 2
    assert report["constraint_types"] == {
        "count:conjunctions",
        "count:keywords_multiple",
        "length:words",
    }
    assert report["constraint_counts"] == {
        "count:conjunctions": 3,
        "count:keywords_multiple": 1,
        "length:words": 2,
    }


def test_file_sha256_hashes_raw_bytes_without_rewriting(tmp_path: Path) -> None:
    path = tmp_path / "raw.jsonl"
    payload = b'{"key": "0", "prompt": "hi \\n"}\n'
    path.write_bytes(payload)
    assert file_sha256(path) == hashlib.sha256(payload).hexdigest()


def test_official_test_set_matches_frozen_inventory() -> None:
    """Guard the protocol's frozen file: 300 items, unique keys, 58 constraint types."""
    items = load_items(_OFFICIAL_TEST_JSONL)
    report = audit_items(items)
    assert file_sha256(_OFFICIAL_TEST_JSONL) == _OFFICIAL_SHA256
    assert report["n_items"] == 300
    assert report["duplicate_ids"] == []
    assert len(report["constraint_types"]) == 58
