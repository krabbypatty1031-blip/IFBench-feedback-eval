"""Configuration parser remains compatible with the original store interface."""

import pytest

from feedback_eval.config import load_mapping_yaml
from feedback_eval.store import load_mapping_yaml as legacy_load


def test_nested_config_and_legacy_interface(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('models:\n  - id: first\n    enabled: true\n  - id: second\n    enabled: false\nquoted: "42"\n', encoding="utf-8")
    expected = {"models": [{"id": "first", "enabled": True},
                            {"id": "second", "enabled": False}], "quoted": "42"}
    assert load_mapping_yaml(path) == legacy_load(path) == expected


def test_invalid_indentation_is_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("root: 1\n  invalid: 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="indent"):
        load_mapping_yaml(path)
