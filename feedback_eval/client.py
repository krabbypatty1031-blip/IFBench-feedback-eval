"""Model clients for the feedback experiment.

FakeClient is the Task 4 test surface. OpenAICompatClient reads the API key from
the environment and never puts it on `repr()`. Empty text, truncation, timeouts,
and missing usage are accounting states, not refusals or score results.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SECRET_KEYS = {"api_key", "authorization", "token", "secret", "password"}
_GENERATION_FIELDS = ("text", "usage", "finish_reason", "latency_ms", "error")


def sanitize_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Copy generation settings without secrets so records can be written."""
    cleaned: dict[str, Any] = {}
    for key, value in settings.items():
        lowered = key.lower()
        if lowered in _SECRET_KEYS or lowered.endswith("_key"):
            continue
        cleaned[key] = value
    return cleaned


def normalize_generation(raw: dict[str, Any]) -> dict[str, Any]:
    """Ensure the five contract fields exist even when a provider omits them."""
    return {field: raw.get(field) for field in _GENERATION_FIELDS}


def _usage_unknown(usage: Any) -> bool:
    if not usage:
        return True
    return all(
        usage.get(field) is None
        for field in ("prompt_tokens", "completion_tokens", "total_tokens")
    )


def classify_generation(result: dict[str, Any]) -> str:
    """Map a generation record to an accounting status.

    The client never returns `refusal` or `score_fail`: scoring belongs to the
    official verifiers, and silence/truncation is not a classified refusal.
    """
    error = result.get("error")
    finish = str(result.get("finish_reason") or "")
    combined = f"{error or ''} {finish}".lower()
    if error or finish in {"timeout", "rate_limit"}:
        if "timeout" in combined:
            return "timeout"
        if "rate" in combined or "429" in combined or finish == "rate_limit":
            return "rate_limit"
        if error:
            return "error"
    if finish in {"length", "max_tokens"}:
        return "truncated"
    if not str(result.get("text") or "").strip():
        return "empty_response"
    if _usage_unknown(result.get("usage")):
        return "missing_usage"
    return "success"


class FakeClient:
    """Scripted client for offline tests. Does not open sockets."""

    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def generate(self, messages: list[dict], settings: dict) -> dict:
        """Return the next scripted generation and record the request."""
        if not self._responses:
            raise IndexError("FakeClient has no scripted responses left")
        self.calls.append(
            {"messages": list(messages), "settings": sanitize_settings(settings)}
        )
        result = normalize_generation(self._responses.pop(0))
        logger.info(
            "FakeClient generate status=%s finish_reason=%s",
            classify_generation(result),
            result.get("finish_reason"),
        )
        return result


class OpenAICompatClient:
    """OpenAI-compatible HTTP client. Tests must not call generate()."""

    def __init__(
        self,
        api_base: str,
        model: str,
        timeout_s: float = 300.0,
        max_retries: int = 1,
        api_key: str | None = None,
    ):
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        # Keep the secret off public attributes so accidental repr/log dumps omit it.
        env_key = os.environ.get("API_KEY") or os.environ.get("FEEDBACK_EVAL_API_KEY")
        self._api_key = api_key if api_key is not None else env_key

    def __repr__(self) -> str:
        return (
            f"OpenAICompatClient(api_base={self.api_base!r}, model={self.model!r}, "
            f"timeout_s={self.timeout_s!r})"
        )

    def headers(self) -> dict[str, str]:
        """Build HTTP headers. The caller must not write this dict into records."""
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def generate(self, messages: list[dict], settings: dict) -> dict:
        """POST /chat/completions. Retries rate limits only, not timeouts."""
        payload = {
            "model": settings.get("model", self.model),
            "messages": messages,
            "temperature": settings.get("temperature", 0.0),
            "max_tokens": settings.get("max_tokens", 4096),
        }
        if settings.get("seed") is not None:
            payload["seed"] = settings["seed"]
        started = time.perf_counter()
        attempts = self.max_retries + 1
        last_error: dict[str, Any] | None = None
        logger.info(
            "HTTP generate model=%s messages=%s timeout_s=%s",
            payload["model"],
            len(messages),
            self.timeout_s,
        )
        for attempt in range(attempts):
            try:
                with httpx.Client(timeout=self.timeout_s) as client:
                    response = client.post(
                        f"{self.api_base}/chat/completions",
                        headers=self.headers(),
                        json=payload,
                    )
            except httpx.TimeoutException as exc:
                latency_ms = (time.perf_counter() - started) * 1000
                logger.warning("HTTP timeout after %.1f ms", latency_ms)
                return normalize_generation(
                    {
                        "text": None,
                        "usage": None,
                        "finish_reason": "timeout",
                        "latency_ms": latency_ms,
                        "error": f"timeout: {exc}",
                    }
                )
            except httpx.HTTPError as exc:
                latency_ms = (time.perf_counter() - started) * 1000
                last_error = {
                    "text": None,
                    "usage": None,
                    "finish_reason": "error",
                    "latency_ms": latency_ms,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                break
            latency_ms = (time.perf_counter() - started) * 1000
            if response.status_code == 429 and attempt + 1 < attempts:
                logger.warning("HTTP 429, retry %s/%s", attempt + 1, attempts)
                continue
            if response.status_code == 429:
                return normalize_generation(
                    {
                        "text": None,
                        "usage": None,
                        "finish_reason": "rate_limit",
                        "latency_ms": latency_ms,
                        "error": f"429 Too Many Requests: {response.text[:200]}",
                    }
                )
            if response.status_code >= 400:
                return normalize_generation(
                    {
                        "text": None,
                        "usage": None,
                        "finish_reason": "error",
                        "latency_ms": latency_ms,
                        "error": f"HTTP {response.status_code}: {response.text[:200]}",
                    }
                )
            body = response.json()
            choice = (body.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            usage = body.get("usage")
            return normalize_generation(
                {
                    "text": message.get("content"),
                    "usage": usage,
                    "finish_reason": choice.get("finish_reason"),
                    "latency_ms": latency_ms,
                    "error": None,
                }
            )
        return normalize_generation(
            last_error
            or {
                "text": None,
                "usage": None,
                "finish_reason": "error",
                "latency_ms": (time.perf_counter() - started) * 1000,
                "error": "HTTP generate failed",
            }
        )
