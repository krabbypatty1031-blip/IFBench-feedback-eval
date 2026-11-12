"""Environment configuration is isolated and keeps existing overrides."""

from feedback_eval import cli
from feedback_eval.environment import apply_env_models, load_dotenv_file


def test_dotenv_preserves_existing_values(tmp_path):
    path = tmp_path / "example.env"
    path.write_text("MODEL_A=example-a\nAPI_KEY=example-key\n", encoding="utf-8")
    environment = {"MODEL_A": "existing"}
    assert load_dotenv_file(path, environment) == ["API_KEY"]
    assert environment == {"MODEL_A": "existing", "API_KEY": "example-key"}


def test_model_overlay_does_not_mutate_config():
    config = {"models": [{"id": None}, {"id": "fixed"}]}
    updated = apply_env_models(config, {"MODEL_A": "example-a", "MODEL_B": "other",
                                        "API_BASE": "https://example.invalid/v1/"})
    assert updated["models"] == [{"id": "example-a", "api_base": "https://example.invalid/v1"},
                                  {"id": "fixed", "api_base": "https://example.invalid/v1"}]
    assert config == {"models": [{"id": None}, {"id": "fixed"}]}
    assert cli.apply_env_models is apply_env_models
