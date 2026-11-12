"""Keep feedback_eval tests from loading IFBench/.env or spending on a live API."""

from __future__ import annotations

import pytest

import httpx

_LIVE_ENV = (
    "FEEDBACK_EVAL_AUTHORIZE_SPEND",
    "API_KEY",
    "FEEDBACK_EVAL_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "MODEL_A",
    "MODEL_B",
    "API_BASE",
    "API_PROVIDER",
    "FEEDBACK_EVAL_API_PROVIDER",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_VERSION",
    "API_VERSION",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "FEEDBACK_EVAL_DEV_CALL_LIMIT",
)


@pytest.fixture(autouse=True)
def isolate_live_api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop spend credentials and refuse sockets; tests that need keys set them locally."""
    monkeypatch.setattr(
        "feedback_eval.cli.load_dotenv_file",
        lambda path, environ=None: [],
    )

    def _refuse_live_http(self: httpx.Client, *args: object, **kwargs: object) -> None:
        raise RuntimeError("tests must not open live HTTP sockets")

    monkeypatch.setattr(httpx.Client, "post", _refuse_live_http)
    monkeypatch.setattr(httpx.Client, "get", _refuse_live_http)
    for key in _LIVE_ENV:
        monkeypatch.delenv(key, raising=False)
