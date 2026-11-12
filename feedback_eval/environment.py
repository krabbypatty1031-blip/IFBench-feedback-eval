"""Environment loading and model configuration overlay."""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_dotenv_file(path: Path, environ: dict[str, str] | None = None) -> list[str]:
    """Load KEY=VALUE into environ if the key is unset. Returns key names only."""
    env = os.environ if environ is None else environ
    path = Path(path)
    if not path.is_file():
        logger.info("No dotenv file at %s", path)
        return []
    loaded: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and env.get(key) in (None, ""):
            env[key] = value
            loaded.append(key)
    logger.info("Loaded dotenv keys from %s: %s", path, loaded)
    return loaded


def apply_env_models(development: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Fill null model ids/api_base from the process environment. No secrets."""
    overlay = copy.deepcopy(development)
    api_base = env.get("API_BASE") or env.get("OPENAI_API_BASE") or env.get("OPENAI_BASE_URL")
    if api_base:
        api_base = api_base.rstrip("/")
    ids = [
        env.get("FEEDBACK_EVAL_MODEL_A") or env.get("MODEL_A") or env.get("MODEL"),
        env.get("FEEDBACK_EVAL_MODEL_B") or env.get("MODEL_B"),
    ]
    for model, model_id in zip(overlay.get("models") or [], ids):
        if model_id and not model.get("id"):
            model["id"] = model_id
        if api_base and not model.get("api_base"):
            model["api_base"] = api_base
    return overlay


