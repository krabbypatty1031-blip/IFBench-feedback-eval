"""Model clients for the feedback experiment.

FakeClient is the Task 4 test surface. HTTP clients read the API key from the
environment and never put it on `repr()`. Empty text, truncation, timeouts,
and missing usage are accounting states, not refusals or score results.

OpenAICompatClient talks to Bearer `/v1/chat/completions` hosts (DeepSeek).
AzureOpenAIClient talks to Azure-style `/deployments/{id}/chat/completions`
hosts (HKBU GenAI): `api-key` header, deployment in the path, no vendor wallet.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any
from urllib.parse import quote, urlencode

import httpx

logger = logging.getLogger(__name__)

_SECRET_KEYS = {"api_key", "api-key", "authorization", "token", "secret", "password"}
_GENERATION_FIELDS = ("text", "usage", "finish_reason", "latency_ms", "error")
_AZURE_ALIASES = {"azure", "azure_openai", "hkbu", "hkbu_genai"}
_OPENAI_ALIASES = {"openai", "openai_compat", "deepseek"}
# HKBU GenAI rejects max_output_tokens below 16 (HTTP 400).
_AZURE_MIN_MAX_TOKENS = 16


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


def normalize_provider(provider: str | None, api_base: str | None = None) -> str:
    """Map config/env aliases onto `openai` or `azure`.

    HKBU GenAI is Azure-shaped (`api-key` + `/deployments/{id}/...`), so its
    host selects azure even when `provider` is left blank.
    """
    raw = (provider or "").strip().lower().replace("-", "_")
    if raw in _AZURE_ALIASES:
        return "azure"
    if raw in _OPENAI_ALIASES:
        return "openai"
    host = (api_base or "").lower()
    if "genai.hkbu.edu.hk" in host:
        return "azure"
    return "openai"


def provider_has_wallet(provider: str | None, api_base: str | None = None) -> bool:
    """HKBU/Azure has no `/user/balance`; do not treat a missing wallet as 0."""
    return normalize_provider(provider, api_base) == "openai"


def completion_payload(
    messages: list[dict],
    settings: dict,
    default_model: str,
    provider: str | None = None,
    api_base: str | None = None,
) -> dict[str, Any]:
    """Build a chat-completions body. Secrets never belong here.

    DeepSeek thinking is billed and can leak chain-of-thought into IFBench
    answers. Callers that need it off must pass `thinking: {type: disabled}`.
    Azure deployments already identify the model in the URL, so `model` and
    DeepSeek-only `thinking` stay out of that body.
    """
    kind = normalize_provider(provider, api_base)
    max_tokens = settings.get("max_tokens", 4096)
    if kind == "azure":
        try:
            max_tokens = max(int(max_tokens), _AZURE_MIN_MAX_TOKENS)
        except (TypeError, ValueError):
            max_tokens = _AZURE_MIN_MAX_TOKENS
    payload: dict[str, Any] = {
        "messages": messages,
        "temperature": settings.get("temperature", 0.0),
        "max_tokens": max_tokens,
    }
    if kind != "azure":
        payload["model"] = settings.get("model", default_model)
    if settings.get("seed") is not None:
        payload["seed"] = settings["seed"]
    if kind != "azure" and settings.get("thinking") is not None:
        payload["thinking"] = settings["thinking"]
    return payload


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
    """OpenAI-compatible HTTP client. Tests must not call live generate()."""

    def __init__(
        self,
        api_base: str,
        model: str,
        timeout_s: float = 300.0,
        max_retries: int = 1,
        api_key: str | None = None,
        provider: str | None = None,
        api_version: str | None = None,
    ):
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.provider = normalize_provider(provider, self.api_base)
        self.api_version = api_version
        # Keep the secret off public attributes so accidental repr/log dumps omit it.
        env_key = (
            os.environ.get("API_KEY")
            or os.environ.get("FEEDBACK_EVAL_API_KEY")
            or os.environ.get("AZURE_OPENAI_API_KEY")
        )
        self._api_key = api_key if api_key is not None else env_key

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(api_base={self.api_base!r}, model={self.model!r}, "
            f"provider={self.provider!r}, timeout_s={self.timeout_s!r})"
        )

    def chat_completions_url(self) -> str:
        """Return the POST URL. Azure puts the deployment name in the path."""
        base = self.api_base.rstrip("/")
        if self.provider == "azure":
            deployment = quote(str(self.model), safe=".-")
            url = f"{base}/deployments/{deployment}/chat/completions"
            if self.api_version:
                url = f"{url}?{urlencode({'api-version': self.api_version})}"
            return url
        return f"{base}/chat/completions"

    def headers(self) -> dict[str, str]:
        """Build HTTP headers. The caller must not write this dict into records."""
        headers = {"Content-Type": "application/json"}
        if not self._api_key:
            return headers
        if self.provider == "azure":
            headers["api-key"] = self._api_key
        else:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def generate(self, messages: list[dict], settings: dict) -> dict:
        """POST chat completions. Retries rate limits only, not timeouts."""
        payload = completion_payload(
            messages,
            settings,
            self.model,
            provider=self.provider,
            api_base=self.api_base,
        )
        started = time.perf_counter()
        attempts = self.max_retries + 1
        last_error: dict[str, Any] | None = None
        logger.info(
            "HTTP generate model=%s provider=%s messages=%s timeout_s=%s",
            self.model,
            self.provider,
            len(messages),
            self.timeout_s,
        )
        for attempt in range(attempts):
            try:
                with httpx.Client(timeout=self.timeout_s) as client:
                    response = client.post(
                        self.chat_completions_url(),
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


class AzureOpenAIClient(OpenAICompatClient):
    """Azure OpenAI / HKBU GenAI: `api-key` header and deployment-scoped URL."""

    def __init__(
        self,
        api_base: str,
        model: str,
        timeout_s: float = 300.0,
        max_retries: int = 1,
        api_key: str | None = None,
        api_version: str | None = None,
        provider: str | None = "azure",
    ):
        super().__init__(
            api_base=api_base,
            model=model,
            timeout_s=timeout_s,
            max_retries=max_retries,
            api_key=api_key,
            provider=provider or "azure",
            api_version=api_version,
        )


def build_http_client(
    model: dict[str, Any],
    timeout_s: float = 300.0,
    max_retries: int = 1,
    api_key: str | None = None,
) -> OpenAICompatClient:
    """Construct the HTTP client implied by a model row. No network."""
    api_base = str(model["api_base"])
    provider = normalize_provider(model.get("provider"), api_base)
    kwargs = {
        "api_base": api_base,
        "model": str(model["id"]),
        "timeout_s": timeout_s,
        "max_retries": max_retries,
        "api_key": api_key,
        "api_version": model.get("api_version"),
        "provider": provider,
    }
    if provider == "azure":
        return AzureOpenAIClient(**kwargs)
    return OpenAICompatClient(**kwargs)


def provider_root(api_base: str) -> str:
    """Strip a trailing /v1 so account endpoints share the vendor origin."""
    root = api_base.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root.rstrip("/")


def fetch_user_balance(
    api_base: str, api_key: str, timeout_s: float = 30.0
) -> dict[str, Any]:
    """GET /user/balance. The key is only used as a header and is never logged."""
    url = f"{provider_root(api_base)}/user/balance"
    logger.info("Fetching provider wallet from %s", url)
    with httpx.Client(timeout=timeout_s) as client:
        response = client.get(url, headers={"Authorization": f"Bearer {api_key}"})
    response.raise_for_status()
    payload = response.json()
    if api_key and api_key in json.dumps(payload):
        raise RuntimeError("provider balance payload contained the API key")
    return payload
