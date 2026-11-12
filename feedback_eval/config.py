"""Restricted configuration parsing for feedback experiments."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _parse_yaml_scalar(text: str) -> Any:
    """Parse a restricted YAML scalar. Quoted text is never treated as a type."""
    if text in {"", "null", "~", "Null", "NULL"}:
        return None
    if text in {"true", "True", "TRUE"}:
        return True
    if text in {"false", "False", "FALSE"}:
        return False
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _parse_yaml_value(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[Any, int]:
    """Parse a mapping or sequence starting at `index` with the given indent."""
    if index >= len(lines):
        return None, index
    if lines[index][1].startswith("- "):
        return _parse_yaml_seq(lines, index, indent)
    return _parse_yaml_map(lines, index, indent)


def _parse_yaml_map(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ValueError(f"unexpected yaml indent at {content!r}")
        if content.startswith("- "):
            break
        if ":" not in content:
            raise ValueError(f"expected yaml key: {content!r}")
        key, _, rest = content.partition(":")
        key = key.strip()
        rest = rest.strip()
        index += 1
        if rest:
            result[key] = _parse_yaml_scalar(rest)
            continue
        if index >= len(lines) or lines[index][0] <= indent:
            result[key] = None
            continue
        child, index = _parse_yaml_value(lines, index, lines[index][0])
        result[key] = child
    return result, index


def _parse_yaml_seq(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent or not content.startswith("- "):
            break
        rest = content[2:].strip()
        # Advance past this dash line before handling empty, mapping, or scalar
        # bodies so a `- value` item cannot re-read the same index.
        index += 1
        if not rest:
            if index >= len(lines) or lines[index][0] <= indent:
                result.append(None)
                continue
            child, index = _parse_yaml_value(lines, index, lines[index][0])
            result.append(child)
            continue
        if ":" in rest and rest[0] not in {"'", '"'}:
            key, _, value = rest.partition(":")
            item = {key.strip(): _parse_yaml_scalar(value.strip())}
            if index < len(lines) and lines[index][0] > indent:
                nested, index = _parse_yaml_map(lines, index, lines[index][0])
                item.update(nested)
            result.append(item)
            continue
        result.append(_parse_yaml_scalar(rest))
        continue
    return result, index


def load_mapping_yaml(path: Path) -> dict[str, Any]:
    """Load a restricted YAML mapping used by development.yaml.

    Supports nested maps, lists of maps, scalars, and whole-line comments.
    This is not a general YAML 1.2 implementation; keep the config in that
    subset so the experiment does not add a PyYAML dependency.
    """
    path = Path(path)
    logger.info("Loading YAML mapping from %s", path)
    lines: list[tuple[int, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, raw.strip()))
    if not lines:
        raise ValueError(f"empty yaml mapping: {path}")
    value, index = _parse_yaml_value(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError(f"trailing yaml content in {path}: {lines[index][1]!r}")
    if not isinstance(value, dict):
        raise ValueError(f"yaml root must be a mapping: {path}")
    return value


