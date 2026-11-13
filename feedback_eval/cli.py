"""CLI for audit, dry-run, freeze, budget estimate, and gated development trials.

`dry-run` and `trial-dev --offline` use FakeClient only. `trial-dev --paid`
constructs an HTTP client only after authorization, an API key, and a
positive development call_limit. Historical spend stays unverified, not zero.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from feedback_eval.client import (
    FakeClient,
    build_http_client,
    classify_generation,
    fetch_user_balance,
    normalize_provider,
    provider_has_wallet,
)
from feedback_eval.dataset import file_sha256, load_items, split_fingerprint
from feedback_eval.environment import apply_env_models, load_dotenv_file
from feedback_eval.execution import (
    BudgetedClient,
    CallCappedClient,
    run_schedule,
    summarize,
)
from feedback_eval.store import (
    STORE_SCHEMA_VERSION,
    RunStore,
    StoreError,
    _OFFICIAL_DATA_SHA256,
    _canonical_sha256,
    build_freeze,
    call_cap_is_confirmed,
    conservative_unit_prices,
    decide_paid_trial,
    default_prior_ledger_candidates,
    estimate_budget,
    inspect_prior_ledger,
    load_mapping_yaml,
    select_review_ids,
    wallet_from_provider_payload,
    write_prior_balance_file,
)
from feedback_eval.analysis import (
    bootstrap_paired_diffs,
    build_review_pack,
    hydrate_overlay_record,
    load_records,
    overlay_records,
    summarize as summarize_metrics,
    write_json as write_analysis_json,
)
from feedback_eval.runner import PROMPTS_PATH, load_prompt_templates, run_item, synthetic_conjunction_item

logger = logging.getLogger(__name__)

_OK_USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
_DEFAULT_CONFIG = Path("configs") / "feedback_eval" / "development.yaml"


def _http_client_label(development: dict[str, Any]) -> str:
    """Name the HTTP client class implied by the first model row."""
    first = (development.get("models") or [{}])[0]
    if normalize_provider(first.get("provider"), first.get("api_base")) == "azure":
        return "AzureOpenAIClient"
    return "OpenAICompatClient"


def _env_api_key() -> str | None:
    """Read the live key. Never log or persist the value."""
    return (
        os.environ.get("API_KEY")
        or os.environ.get("FEEDBACK_EVAL_API_KEY")
        or os.environ.get("AZURE_OPENAI_API_KEY")
    )


def _refuse_secret_in_text(text: str) -> None:
    """Abort if any known env secret leaked into a record or summary."""
    for key in ("API_KEY", "FEEDBACK_EVAL_API_KEY", "AZURE_OPENAI_API_KEY"):
        secret = os.environ.get(key)
        if secret and secret in text:
            raise StoreError("refusing to write a summary that contains the API key")


_OFFICIAL_CONFIG = Path("configs") / "feedback_eval" / "official.yaml"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_split_items(
    repo_root: Path, development: dict[str, Any], which: str
) -> list[dict[str, Any]]:
    """Load official JSONL rows for the frozen dev or test id list."""
    split_path = repo_root / development["paths"]["split"]
    data_path = repo_root / development["paths"]["data"]
    split = json.loads(split_path.read_text(encoding="utf-8"))
    wanted = [str(item_id) for item_id in split["dev_ids" if which == "dev" else "test_ids"]]
    by_id = {str(item["key"]): item for item in load_items(data_path)}
    items = [by_id[item_id] for item_id in wanted if item_id in by_id]
    logger.info("Loaded %s %s-split items", len(items), which)
    return items


class CallLimitError(RuntimeError):
    """Development trial hit the authorized generate cap."""


class LimitedClient:
    """Wrap a live client and refuse generates after `call_limit`."""

    def __init__(self, inner: Any, call_limit: int, counter: dict[str, int]):
        self._inner = inner
        self._call_limit = call_limit
        self._counter = counter

    def generate(self, messages: list[dict], settings: dict) -> dict:
        if self._counter["n"] >= self._call_limit:
            raise CallLimitError(
                f"development call_limit={self._call_limit} reached"
            )
        self._counter["n"] += 1
        logger.info(
            "LimitedClient generate %s/%s model=%s",
            self._counter["n"],
            self._call_limit,
            settings.get("model"),
        )
        return self._inner.generate(messages, settings)


def _fake_ok(text: str) -> dict[str, Any]:
    return {
        "text": text,
        "usage": dict(_OK_USAGE),
        "finish_reason": "stop",
        "latency_ms": 1.0,
        "error": None,
    }


def _fingerprint_config(model: str) -> dict[str, Any]:
    return {
        "protocol": "feedback-eval-dev-v1",
        "data_sha256": "dry-run-synthetic",
        "model": model,
        "prompt_sha256": file_sha256(PROMPTS_PATH),
        "code_version": STORE_SCHEMA_VERSION,
        "parameters": {"temperature": 0.0, "max_tokens": 128, "seed": 42},
    }


def dry_run(
    n_items: int,
    store_dir: Path,
    client: FakeClient | None = None,
) -> dict[str, Any]:
    """Run n synthetic items through B0–B3 with a fake client. No network."""
    planned_calls = n_items * 4
    model = "fake-dry-run"
    fingerprint = _fingerprint_config(model)
    store = RunStore(store_dir)
    store.initialize(fingerprint, inspect_prior_ledger([]))
    templates = load_prompt_templates()
    config = {**fingerprint, "store": store, "prompts": templates}
    if client is None:
        script = [
            _fake_ok(f"item{item_index} cond{cond} hello and but so")
            for item_index in range(n_items)
            for cond in range(4)
        ]
        client = FakeClient(script)
    actual_before = len(client.calls)
    items = [synthetic_conjunction_item(str(index)) for index in range(n_items)]
    for item in items:
        run_item(item, client, config)
    actual_calls = len(client.calls) - actual_before
    logger.info(
        "dry-run n_items=%s planned_calls=%s actual_calls=%s store=%s",
        n_items,
        planned_calls,
        actual_calls,
        store_dir,
    )
    print(f"planned_calls={planned_calls}")
    print(f"actual_calls={actual_calls}")
    print(f"store_dir={store_dir}")
    return {
        "planned_calls": planned_calls,
        "actual_calls": actual_calls,
        "n_items": n_items,
        "store_dir": str(store_dir),
    }


def _resolve_config_path(config: Path | None, repo_root: Path) -> Path:
    if config is None:
        return repo_root / _DEFAULT_CONFIG
    path = Path(config)
    return path if path.is_absolute() else repo_root / path


def _git_head(repo_root: Path) -> str | None:
    """Record HEAD when git is available. Missing git is not a zero hash."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        logger.warning("git rev-parse failed: %s", exc)
        return None
    if completed.returncode != 0:
        logger.warning("git rev-parse exit=%s", completed.returncode)
        return None
    return completed.stdout.strip() or None


def _code_sha256(repo_root: Path) -> str:
    """Pin student package files by name so the hash is path-independent."""
    files = sorted(
        (repo_root / "feedback_eval").glob("*.py"),
        key=lambda path: path.name,
    )
    payload = {
        "files": [
            {"path": path.name, "sha256": file_sha256(path)} for path in files
        ]
    }
    digest = _canonical_sha256(payload)
    logger.info("Hashed %s student modules -> %s", len(files), digest)
    return digest


def collect_source_hashes(repo_root: Path, development: dict[str, Any]) -> dict[str, str]:
    """Hash the official JSONL, split, prompts, homology, support catalog, and code."""
    paths = development["paths"]

    def resolve(relative: str) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else repo_root / path

    data_path = resolve(paths["data"])
    split_path = resolve(paths["split"])
    data_hash = file_sha256(data_path)
    if data_hash != _OFFICIAL_DATA_SHA256:
        raise ValueError(
            f"data sha256 {data_hash} is not the official IFBench file {_OFFICIAL_DATA_SHA256}"
        )
    split = json.loads(split_path.read_text(encoding="utf-8"))
    recomputed = split_fingerprint(split)
    if recomputed != split["split_sha256"]:
        raise ValueError("split.json split_sha256 does not match split_fingerprint()")
    if split["source_sha256"] != data_hash:
        raise ValueError("split.source_sha256 does not match the data file")
    hashes = {
        "data_sha256": data_hash,
        "split_sha256": split["split_sha256"],
        "prompt_sha256": file_sha256(resolve(paths["prompts"])),
        "homology_sha256": file_sha256(resolve(paths["homology"])),
        "constraint_support_sha256": file_sha256(resolve(paths["constraint_support"])),
        "code_sha256": _code_sha256(repo_root),
    }
    logger.info("Collected source hashes data=%s split=%s", data_hash, hashes["split_sha256"])
    return hashes


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_experiment_freeze(
    repo_root: Path,
    development: dict[str, Any],
    env: dict[str, str],
    paid_kwargs: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the freeze record and the RunStore fingerprint from development.yaml."""
    hashes = collect_source_hashes(repo_root, development)
    split_path = repo_root / development["paths"]["split"]
    split = json.loads(split_path.read_text(encoding="utf-8"))
    review = dict(development["human_review"])
    review["item_ids"] = select_review_ids(
        split["test_ids"], int(review["n_items"]), int(review["seed"])
    )
    prior = inspect_prior_ledger(default_prior_ledger_candidates(repo_root))
    estimate = estimate_budget(
        development["budget_planning"],
        prior,
        unit_prices=conservative_unit_prices(development.get("models") or []),
    )
    paid = decide_paid_trial(
        env, prior, development["cost_cap"], **(paid_kwargs or {})
    )
    freeze_config = {
        "protocol": development["protocol"],
        "code_version": development["code_version"],
        "git_commit": _git_head(repo_root),
        "models": development["models"],
        "parameters": development["parameters"],
        "scoring": development["scoring"],
        "feedback": development["feedback"],
        "retry": development["retry"],
        "human_review": review,
        "cost_cap": development["cost_cap"],
        "budget_estimate": estimate,
        "paid_trial": paid,
    }
    freeze = build_freeze(freeze_config, hashes)
    fingerprint = {
        "protocol": development["protocol"],
        "data_sha256": hashes["data_sha256"],
        "model": ",".join(
            str(model.get("id") or model["slot"]) for model in development["models"]
        ),
        "prompt_sha256": hashes["prompt_sha256"],
        "code_version": development["code_version"],
        "parameters": development["parameters"],
    }
    return freeze, fingerprint


def cmd_freeze(store_dir: Path, config_path: Path | None = None) -> int:
    """Write or resume a freeze.json. Does not call models."""
    repo_root = _repo_root()
    load_dotenv_file(repo_root / ".env")
    development = apply_env_models(
        load_mapping_yaml(_resolve_config_path(config_path, repo_root)),
        dict(os.environ),
    )
    freeze, fingerprint = build_experiment_freeze(
        repo_root, development, dict(os.environ)
    )
    prior = inspect_prior_ledger(default_prior_ledger_candidates(repo_root))
    try:
        RunStore(store_dir).initialize_frozen(fingerprint, prior, freeze)
    except StoreError as exc:
        logger.error("Freeze refused: %s", exc)
        print(f"freeze_refused={type(exc).__name__}")
        print(f"reason={exc}")
        return 1
    print(f"freeze_sha256={freeze['freeze_sha256']}")
    print(f"ready_for_official_run={str(freeze['ready_for_official_run']).lower()}")
    print(f"planned_outputs={freeze['budget_estimate']['planned_outputs']}")
    print(f"paid_trial_allowed={str(freeze['paid_trial']['allowed']).lower()}")
    print(f"paid_trial_reason={freeze['paid_trial']['reason']}")
    return 0


def cmd_estimate_budget(config_path: Path | None = None) -> dict[str, Any]:
    """Print the 2,080-output plan. Unknown prices stay unknown."""
    repo_root = _repo_root()
    development = load_mapping_yaml(_resolve_config_path(config_path, repo_root))
    prior = inspect_prior_ledger(default_prior_ledger_candidates(repo_root))
    estimate = estimate_budget(
        development["budget_planning"],
        prior,
        unit_prices=conservative_unit_prices(development.get("models") or []),
    )
    print(json.dumps(estimate, indent=2, ensure_ascii=False, sort_keys=True))
    return estimate


def cmd_trial_dev_paid(
    store_dir: Path,
    n_items: int | None = None,
    config_path: Path | None = None,
) -> int:
    """Probe two candidates and run a call-capped development subset. No secrets written."""
    repo_root = _repo_root()
    load_dotenv_file(repo_root / ".env")
    development = apply_env_models(
        load_mapping_yaml(_resolve_config_path(config_path, repo_root)),
        dict(os.environ),
    )
    trial_cfg = development.get("trial") or {}
    call_limit = int(
        os.environ.get("FEEDBACK_EVAL_DEV_CALL_LIMIT")
        or trial_cfg.get("call_limit")
        or 0
    )
    prior = inspect_prior_ledger(default_prior_ledger_candidates(repo_root))
    decision = decide_paid_trial(
        dict(os.environ),
        prior,
        development["cost_cap"],
        development=True,
        call_limit=call_limit,
    )
    if decision["allowed"] and any(
        not model.get("id") or not model.get("api_base") for model in development["models"]
    ):
        decision = {
            **decision,
            "allowed": False,
            "reason": "model_id_unspecified",
        }
    store_dir.mkdir(parents=True, exist_ok=True)
    if not decision["allowed"]:
        _write_json(store_dir / "paid_trial.json", decision)
        print(f"paid_trial_blocked reason={decision['reason']}")
        logger.warning("Paid trial stopped: %s", decision["reason"])
        return 3

    n_items = int(
        n_items
        if n_items is not None
        else trial_cfg.get("paid_n_dev_items") or 1
    )
    templates = load_prompt_templates()
    freeze, fingerprint = build_experiment_freeze(
        repo_root,
        development,
        dict(os.environ),
        paid_kwargs={"development": True, "call_limit": call_limit},
    )
    store = RunStore(store_dir)
    store.initialize_frozen(fingerprint, prior, freeze)
    _write_json(store_dir / "paid_trial.json", decision)
    counter = {"n": 0}
    timeout_s = float((development.get("retry") or {}).get("timeout_s") or 300.0)
    max_retries = int((development.get("retry") or {}).get("rate_limit_retries") or 1)
    probes: list[dict[str, Any]] = []
    stopped_reason = None
    client_name = _http_client_label(development)
    try:
        for model in development["models"]:
            inner = build_http_client(
                model, timeout_s=timeout_s, max_retries=max_retries
            )
            client = LimitedClient(inner, call_limit, counter)
            ping_settings = {
                "model": model["id"],
                "temperature": 0.0,
                "max_tokens": 8,
                "seed": 42,
            }
            if (development.get("parameters") or {}).get("thinking") is not None:
                ping_settings["thinking"] = development["parameters"]["thinking"]
            ping = client.generate(
                [{"role": "user", "content": "Reply with the single word ping."}],
                ping_settings,
            )
            status = classify_generation(ping)
            reachable = status in {"success", "truncated", "missing_usage", "empty_response"}
            model["availability"] = "probe_ok" if reachable else "probe_failed"
            probes.append(
                {
                    "slot": model["slot"],
                    "id": model["id"],
                    "api_base": model["api_base"],
                    "availability": model["availability"],
                    "status": status,
                    "finish_reason": ping.get("finish_reason"),
                    "error": ping.get("error"),
                    "usage": ping.get("usage"),
                    "latency_ms": ping.get("latency_ms"),
                    "text_chars": len(str(ping.get("text") or "")),
                }
            )
            logger.info(
                "Probe slot=%s id=%s status=%s reachable=%s",
                model["slot"],
                model["id"],
                status,
                reachable,
            )

        items = load_split_items(repo_root, development, "dev")[:n_items]
        for model in development["models"]:
            if model.get("availability") != "probe_ok":
                logger.warning("Skipping items for unreachable %s", model.get("id"))
                continue
            inner = build_http_client(
                model, timeout_s=timeout_s, max_retries=max_retries
            )
            client = LimitedClient(inner, call_limit, counter)
            item_config = {
                **fingerprint,
                "model": str(model["id"]),
                "store": store,
                "prompts": templates,
                "parameters": development["parameters"],
            }
            for item in items:
                run_item(item, client, item_config)
    except CallLimitError as exc:
        stopped_reason = str(exc)
        logger.warning("%s", exc)

    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "n_outputs": 0}
    for probe in probes:
        usage = probe.get("usage") or {}
        if usage.get("prompt_tokens") is not None:
            usage_totals["prompt_tokens"] += int(usage["prompt_tokens"])
            usage_totals["n_outputs"] += 1
        if usage.get("completion_tokens") is not None:
            usage_totals["completion_tokens"] += int(usage["completion_tokens"])
    estimate = estimate_budget(
        development["budget_planning"],
        prior,
        observed_usage=usage_totals if usage_totals["n_outputs"] else None,
    )
    summary = {
        "client": client_name,
        "paid": True,
        "n_items": n_items,
        "n_models": len(development["models"]),
        "models": [
            {"slot": m["slot"], "id": m.get("id"), "availability": m.get("availability")}
            for m in development["models"]
        ],
        "probes": probes,
        "call_limit": call_limit,
        "actual_calls": counter["n"],
        "ledger_calls": store.load_ledger().get("calls"),
        "stopped_reason": stopped_reason,
        "budget_estimate": estimate,
        "ready_for_official_run": False,
        "note": (
            "Development trial only. Prior spend remains unverified. "
            "Unit prices stay unverified unless recorded separately."
        ),
    }
    _write_json(store_dir / "trial_summary.json", summary)
    dumped = json.dumps(summary)
    _refuse_secret_in_text(dumped)
    print(f"planned_calls={n_items * len(development['models']) * 4 + len(development['models'])}")
    print(f"actual_calls={counter['n']}")
    print(f"client={client_name}")
    print(f"paid=true")
    print(f"call_limit={call_limit}")
    if stopped_reason:
        print(f"stopped_reason={stopped_reason}")
    for probe in probes:
        print(
            f"probe slot={probe['slot']} id={probe['id']} "
            f"availability={probe['availability']} status={probe['status']}"
        )
    return 0 if any(p["availability"] == "probe_ok" for p in probes) else 4


def cmd_trial_dev_offline(
    store_dir: Path,
    n_items: int | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """End-to-end FakeClient trial over two candidate slots. No network."""
    repo_root = _repo_root()
    development = load_mapping_yaml(_resolve_config_path(config_path, repo_root))
    freeze, fingerprint = build_experiment_freeze(
        repo_root, development, dict(os.environ)
    )
    prior = inspect_prior_ledger(default_prior_ledger_candidates(repo_root))
    store = RunStore(store_dir)
    store.initialize_frozen(fingerprint, prior, freeze)
    n_items = int(n_items if n_items is not None else development["trial"]["n_dev_items"])
    models = [str(model.get("id") or model["slot"]) for model in development["models"]]
    planned_calls = n_items * len(models) * 4
    script = [
        _fake_ok(f"{model} item{item_index} cond{cond} hello and but so")
        for model in models
        for item_index in range(n_items)
        for cond in range(4)
    ]
    client = FakeClient(script)
    templates = load_prompt_templates()
    items = [synthetic_conjunction_item(str(index)) for index in range(n_items)]
    actual_before = len(client.calls)
    for model in models:
        item_config = {
            **fingerprint,
            "model": model,
            "store": store,
            "prompts": templates,
        }
        for item in items:
            run_item(item, client, item_config)
    actual_calls = len(client.calls) - actual_before
    summary = {
        "client": "FakeClient",
        "paid": False,
        "n_items": n_items,
        "n_models": len(models),
        "models": models,
        "planned_calls": planned_calls,
        "actual_calls": actual_calls,
        "ledger_calls": store.load_ledger().get("calls"),
        "ready_for_official_run": freeze["ready_for_official_run"],
        "stop_reason": freeze["budget_estimate"]["stop_reason"],
    }
    _write_json(store_dir / "trial_summary.json", summary)
    logger.info(
        "offline trial planned_calls=%s actual_calls=%s",
        planned_calls,
        actual_calls,
    )
    print(f"planned_calls={planned_calls}")
    print(f"actual_calls={actual_calls}")
    print(f"client={summary['client']}")
    print(f"paid={str(summary['paid']).lower()}")
    return summary


def _refresh_prior_wallet(repo_root: Path, development: dict[str, Any]) -> dict[str, Any]:
    """Fetch the live wallet and write prior_balance.json. Never logs the key."""
    api_key = _env_api_key()
    if not api_key:
        raise StoreError("api_key_missing")
    api_base = None
    for model in development.get("models") or []:
        if model.get("api_base"):
            api_base = str(model["api_base"])
            break
    api_base = api_base or os.environ.get("API_BASE") or "https://api.deepseek.com/v1"
    first = (development.get("models") or [{}])[0]
    provider = normalize_provider(first.get("provider"), api_base)
    if not provider_has_wallet(provider, api_base):
        # HKBU/Azure has no account-balance endpoint. Do not probe DeepSeek
        # /user/balance or invent a zero wallet.
        raise StoreError("provider_has_no_wallet")
    payload = fetch_user_balance(api_base, api_key)
    preferred = str((development.get("cost_cap") or {}).get("currency") or "CNY")
    wallet = wallet_from_provider_payload(payload, preferred_currency=preferred)
    path = repo_root / "runs" / "feedback_eval" / "prior_balance.json"
    write_prior_balance_file(path, wallet)
    return inspect_prior_ledger(default_prior_ledger_candidates(repo_root))


def _write_execution_summary(
    store_dir: Path, summary: dict[str, Any], extra: dict[str, Any]
) -> dict[str, Any]:
    payload = {**summary, **extra}
    links = payload.get("score_links") or []
    # Keep the on-disk summary bounded; full links stay in per-record JSON.
    payload["score_links_n"] = len(links)
    payload["score_links_preview"] = links[:12]
    if len(links) > 12:
        payload.pop("score_links", None)
    _write_json(store_dir / "execution_summary.json", payload)
    _refuse_secret_in_text(json.dumps(payload))
    return payload


def cmd_run_official_offline(
    store_dir: Path,
    n_items: int | None = None,
    config_path: Path | None = None,
) -> int:
    """FakeClient official-path rehearsal. Never opens sockets."""
    repo_root = _repo_root()
    config_file = config_path or _OFFICIAL_CONFIG
    development = load_mapping_yaml(_resolve_config_path(config_file, repo_root))
    freeze, fingerprint = build_experiment_freeze(
        repo_root, development, dict(os.environ)
    )
    prior = inspect_prior_ledger(default_prior_ledger_candidates(repo_root))
    store = RunStore(store_dir)
    store.initialize_frozen(fingerprint, prior, freeze)
    n_items = int(n_items if n_items is not None else 2)
    models = [str(model.get("id") or model["slot"]) for model in development["models"]]
    items = [synthetic_conjunction_item(str(index)) for index in range(n_items)]
    script = [
        _fake_ok(f"{model} item{item_index} cond{cond} hello and but so")
        for model in models
        for item_index in range(n_items)
        for cond in range(4)
    ]
    client = FakeClient(script)
    templates = load_prompt_templates()
    item_config = {**fingerprint, "store": store, "prompts": templates}
    summary = run_schedule(
        items, models, {model: client for model in models}, item_config
    )
    dumped = _write_execution_summary(
        store_dir,
        summary,
        {
            "client": "FakeClient",
            "paid": False,
            "ready_for_official_run": freeze["ready_for_official_run"],
            "n_items": n_items,
            "n_models": len(models),
        },
    )
    print(f"expected={dumped['expected']}")
    print(f"accounted={dumped['accounted']}")
    print(f"missing={dumped['missing']}")
    print(f"actual_calls={dumped['calls_this_invocation']}")
    print("client=FakeClient")
    print(f"paid=false")
    print(f"ready_for_official_run={str(freeze['ready_for_official_run']).lower()}")
    return 0


def cmd_run_official_paid(
    store_dir: Path,
    n_items: int | None = None,
    config_path: Path | None = None,
) -> int:
    """Paid official test split. Wallet providers need a live balance; Azure uses call_cap."""
    repo_root = _repo_root()
    load_dotenv_file(repo_root / ".env")
    config_file = config_path or _OFFICIAL_CONFIG
    development = apply_env_models(
        load_mapping_yaml(_resolve_config_path(config_file, repo_root)),
        dict(os.environ),
    )
    first = (development.get("models") or [{}])[0]
    api_base = str(first.get("api_base") or os.environ.get("API_BASE") or "")
    provider = normalize_provider(first.get("provider"), api_base)
    if provider_has_wallet(provider, api_base):
        try:
            prior = _refresh_prior_wallet(repo_root, development)
        except Exception as exc:
            logger.error("Official wallet refresh failed: %s", exc)
            print("official_run_blocked reason=balance_unverified")
            return 3
    else:
        # HKBU/Azure has no /user/balance. Do not write a forged zero wallet.
        prior = inspect_prior_ledger(default_prior_ledger_candidates(repo_root))
        logger.warning(
            "Skipping wallet refresh provider=%s prior_status=%s",
            provider,
            prior.get("status"),
        )
    decision = decide_paid_trial(
        dict(os.environ),
        prior,
        development["cost_cap"],
        development=False,
    )
    freeze, fingerprint = build_experiment_freeze(
        repo_root, development, dict(os.environ)
    )
    if not decision["allowed"]:
        print(f"official_run_blocked reason={decision['reason']}")
        logger.warning("Official run blocked: %s", decision["reason"])
        return 3
    if not freeze["ready_for_official_run"]:
        print("official_run_blocked reason=freeze_not_ready")
        logger.warning("Official run blocked: freeze_not_ready")
        return 3
    cap = development.get("cost_cap") or {}
    cap_currency = str(cap.get("currency") or "")
    if (
        provider_has_wallet(provider, api_base)
        and prior.get("currency")
        and cap_currency
        and prior.get("currency") != cap_currency
    ):
        print("official_run_blocked reason=currency_mismatch")
        logger.error(
            "Wallet currency %s != cap currency %s",
            prior.get("currency"),
            cap_currency,
        )
        return 3
    templates = load_prompt_templates()
    store = RunStore(store_dir)
    try:
        store.initialize_frozen(fingerprint, prior, freeze)
    except StoreError as exc:
        logger.error("Official freeze refused: %s", exc)
        print(f"official_run_blocked reason={type(exc).__name__}")
        return 1
    items = load_split_items(repo_root, development, "test")
    if n_items is not None:
        items = items[: int(n_items)]
    timeout_s = float((development.get("retry") or {}).get("timeout_s") or 300.0)
    max_retries = int((development.get("retry") or {}).get("rate_limit_retries") or 1)
    max_tokens = int((development.get("parameters") or {}).get("max_tokens") or 4096)
    clients: dict[str, Any] = {}
    client_name = _http_client_label(development)
    call_counter = {"n": 0}
    for model in development["models"]:
        model_id = str(model["id"])
        inner = build_http_client(
            model, timeout_s=timeout_s, max_retries=max_retries
        )
        if call_cap_is_confirmed(cap):
            clients[model_id] = CallCappedClient(
                inner, int(cap["call_cap"]), call_counter
            )
        else:
            clients[model_id] = BudgetedClient(
                inner,
                store,
                model_id,
                model["unit_price"],
                float(cap["amount"]),
                float(prior["balance"]),
                max_tokens,
            )
    models = [str(model["id"]) for model in development["models"]]
    item_config = {
        **fingerprint,
        "store": store,
        "prompts": templates,
        "parameters": development["parameters"],
        "protocol": development["protocol"],
    }

    def on_progress(progress: dict[str, Any], calls: int) -> None:
        _write_execution_summary(
            store_dir,
            {**progress, "calls_this_invocation": calls, "stopped": None},
            {
                "client": client_name,
                "paid": True,
                "ready_for_official_run": True,
                "n_items": len(items),
                "n_models": len(models),
                "cost_cap": development["cost_cap"],
                "prior_balance": prior.get("balance"),
                "prior_currency": prior.get("currency"),
                "ledger_calls": store.load_ledger().get("calls"),
                "spent_this_run": store.load_ledger().get("spent_this_run"),
                "in_progress": True,
            },
        )

    summary = run_schedule(
        items, models, clients, item_config, on_progress=on_progress
    )
    dumped = _write_execution_summary(
        store_dir,
        summary,
        {
            "client": client_name,
            "paid": True,
            "ready_for_official_run": True,
            "n_items": len(items),
            "n_models": len(models),
            "cost_cap": development["cost_cap"],
            "prior_balance": prior.get("balance"),
            "prior_currency": prior.get("currency"),
            "ledger_calls": store.load_ledger().get("calls"),
            "spent_this_run": store.load_ledger().get("spent_this_run"),
        },
    )
    print(f"expected={dumped['expected']}")
    print(f"accounted={dumped['accounted']}")
    print(f"success={dumped['success']}")
    print(f"missing={dumped['missing']}")
    print(f"truncated={dumped['truncated']}")
    print(f"empty_response={dumped['empty_response']}")
    print(f"generation_failures={dumped['generation_failures']}")
    print(f"cost_unknown={dumped['cost_unknown']}")
    print(f"actual_calls={dumped['calls_this_invocation']}")
    print(f"stopped={dumped.get('stopped')}")
    print(f"client={client_name}")
    print("paid=true")
    return 0 if dumped["missing"] == 0 and not dumped.get("stopped") else 4


def _format_rate(block: dict[str, Any] | None) -> str:
    """Render n/d and the ratio, or null when the denominator is zero."""
    block = block or {}
    n = block.get("n", 0)
    d = block.get("d", 0)
    value = block.get("value")
    if value is None:
        return f"{n}/{d} null"
    return f"{n}/{d} {float(value):.4f}"


def _default_official_overlays() -> list[Path]:
    """Recovered generations used when official tables ignore the token cap."""
    root = _repo_root()
    candidates = [
        root / "runs" / "feedback_eval" / "sensitivity_empty_b0_16384",
        root / "runs" / "feedback_eval" / "probe_b3_32768",
    ]
    return [path for path in candidates if (path / "records").is_dir()]


def _items_by_id() -> dict[str, dict[str, Any]]:
    """Official JSONL keyed by item id, used to score hydrated overlay probes."""
    data_path = _repo_root() / "data" / "IFBench_test.jsonl"
    return {str(item["key"]): item for item in load_items(data_path)}


def _prepare_analysis_records(
    store_dir: Path,
    freeze: dict[str, Any],
    overlay_dirs: list[Path] | None,
    no_overlay: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Load store records and optionally overlay recovered higher-cap answers."""
    records = load_records(store_dir)
    if no_overlay:
        logger.info("Overlay disabled; reporting raw store identities")
        return records, [], []
    paths = list(overlay_dirs) if overlay_dirs else []
    if not paths and freeze.get("protocol") == "feedback-eval-official-hkbu-v1":
        paths = _default_official_overlays()
    if not paths:
        return records, [], []
    items = _items_by_id()
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    source_names: list[str] = []
    for path in paths:
        rows = [
            hydrate_overlay_record(row, items_by_id=items) for row in load_records(path)
        ]
        groups.append((str(path), rows))
        source_names.append(str(path))
        logger.info("Prepared overlay n=%s from %s", len(rows), path)
    merged, replaced = overlay_records(records, groups)
    return merged, replaced, source_names


def _load_homology_groups(path: Path | None) -> list[list[str]]:
    """Load homology clusters for bootstrap; missing file means item-level units."""
    if path is None:
        candidate = _repo_root() / "configs" / "feedback_eval" / "homology.json"
        path = candidate if candidate.is_file() else None
    if path is None:
        logger.info("No homology file; bootstrap clusters by item id")
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    groups = [[str(item_id) for item_id in (group.get("ids") or [])] for group in payload.get("groups") or []]
    logger.info("Loaded %s homology groups from %s", len(groups), path)
    return groups


def cmd_analyze(
    store_dir: Path,
    homology_path: Path | None = None,
    n_resamples: int = 1000,
    seed: int = 42,
    overlay_dirs: list[Path] | None = None,
    no_overlay: bool = False,
) -> int:
    """Rebuild paired tables. Official protocol overlays recovered generations."""
    store_dir = Path(store_dir)
    freeze_path = store_dir / "freeze.json"
    freeze: dict[str, Any] = {}
    if freeze_path.is_file():
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        logger.info("Loaded freeze protocol=%s", freeze.get("protocol"))
    else:
        logger.warning("No freeze.json under %s; analysis still runs on raw records", store_dir)
    records, replaced, overlay_sources = _prepare_analysis_records(
        store_dir, freeze, overlay_dirs, no_overlay
    )
    # Keep the 4096-only tables if this is the first overlaid official rebuild.
    raw_archive = store_dir / "analysis_4096_only.json"
    current_analysis = store_dir / "analysis.json"
    if (
        freeze.get("protocol") == "feedback-eval-official-hkbu-v1"
        and replaced
        and current_analysis.is_file()
        and not raw_archive.is_file()
    ):
        raw_archive.write_text(current_analysis.read_text(encoding="utf-8"), encoding="utf-8")
        logger.info("Archived 4096-only analysis to %s", raw_archive)
    groups = _load_homology_groups(homology_path)
    summary = summarize_metrics(records)
    diffs = bootstrap_paired_diffs(
        records, n_resamples=int(n_resamples), seed=int(seed), groups=groups
    )
    appendix_path = store_dir / "appendix_b3_32768.json"
    appendix = None
    if appendix_path.is_file():
        appendix = json.loads(appendix_path.read_text(encoding="utf-8"))
        logger.info("Attached sensitivity appendix %s", appendix_path)
    overlay_block = {
        "enabled": bool(overlay_sources),
        "policy": "prefer_better_accounting_keep_base_success",
        "sources": overlay_sources,
        "n_replaced": len(replaced),
        "replaced": replaced,
        "note": (
            "Reported official tables treat max_tokens as an implementation "
            "limit. Recovered text replaces empty/error identities; raw 4096 "
            "JSON in records/ is unchanged."
        ),
    }
    payload = {
        "store_dir": str(store_dir),
        "protocol": freeze.get("protocol"),
        "freeze_sha256": freeze.get("freeze_sha256"),
        "parameters": freeze.get("parameters"),
        "n_records": len(records),
        "missing_data_rule": "report_missing_do_not_impute",
        "homology_groups": groups,
        "summary": summary,
        "bootstrap": diffs,
        "overlay": overlay_block,
        "cost": {
            "amount": None,
            "currency": None,
            "status": "unknown",
            "reason": "HKBU wallet spend is not reported; unit prices stay unverified",
        },
        "sensitivity_appendix": appendix,
        "note": (
            "Official reported tables overlay recovered generations when "
            "present. Token cap is not a model-quality factor."
            if overlay_sources
            else "Tables are rebuilt from this store only."
        ),
    }
    out_path = store_dir / "analysis.json"
    write_analysis_json(out_path, payload)
    write_analysis_json(store_dir / "overlay_manifest.json", overlay_block)
    logger.info("Wrote analysis n_records=%s path=%s", len(records), out_path)
    print(f"wrote={out_path}")
    print(f"protocol={freeze.get('protocol')}")
    print(f"n_records={len(records)}")
    acc = summary.get("accounting") or {}
    print(
        "accounting "
        + " ".join(
            f"{key}={acc.get(key, 0)}"
            for key in (
                "success",
                "empty_response",
                "error",
                "missing",
                "truncated",
                "rate_limit",
                "timeout",
            )
        )
    )
    for model, block in (summary.get("by_model") or {}).items():
        print(f"model={model} usable_ssss={block.get('usable_ssss')} n_items={block.get('n_items')}")
        for condition in ("B0", "B1", "B2", "B3"):
            print(
                f"  strict_{condition}={_format_rate((block.get('strict_prompt') or {}).get(condition))}"
            )
        for condition in ("B1", "B2", "B3"):
            print(f"  repair_{condition}={_format_rate((block.get('repair') or {}).get(condition))}")
            print(f"  regress_{condition}={_format_rate((block.get('regress') or {}).get(condition))}")
            diff = ((diffs.get(model) or {}).get(condition) or {}).get("strict_prompt_diff") or {}
            point = diff.get("point")
            print(
                f"  bootstrap_{condition} point={point} "
                f"[{diff.get('low')}, {diff.get('high')}] n_pairs={diff.get('n_pairs')}"
            )
    print(f"overlay_replaced={len(replaced)}")
    print("cost_status=unknown")
    return 0


def cmd_review_pack(
    store_dir: Path,
    out_dir: Path | None = None,
    overlay_dirs: list[Path] | None = None,
    no_overlay: bool = False,
) -> int:
    """Write a blinded content-review pack from freeze human_review ids."""
    store_dir = Path(store_dir)
    freeze_path = store_dir / "freeze.json"
    if not freeze_path.is_file():
        print("review-pack requires freeze.json with human_review.item_ids")
        return 2
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    review = freeze.get("human_review") or {}
    item_ids = [str(item_id) for item_id in review.get("item_ids") or []]
    if not item_ids:
        print("freeze.json has no human_review.item_ids")
        return 2
    models = [str(model["id"]) for model in freeze.get("models") or [] if model.get("id")]
    if not models:
        print("freeze.json has no models")
        return 2
    records, replaced, overlay_sources = _prepare_analysis_records(
        store_dir, freeze, overlay_dirs, no_overlay
    )
    logger.info(
        "Review pack overlay sources=%s replaced=%s",
        overlay_sources,
        len(replaced),
    )
    pack = build_review_pack(
        records, item_ids, int(review.get("seed") or 42), models
    )
    out_dir = Path(out_dir) if out_dir else store_dir / "review_pack"
    out_dir.mkdir(parents=True, exist_ok=True)
    blinded = {key: value for key, value in pack.items() if key != "key"}
    write_analysis_json(out_dir / "blinded.json", blinded)
    write_analysis_json(
        out_dir / "key.json",
        {"seed": pack["seed"], "n_entries": pack["n_entries"], "key": pack["key"]},
    )
    csv_path = out_dir / "judgments.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["review_id", "label", "notes"])
        for row in pack["entries"]:
            writer.writerow([row["review_id"], "", ""])
    logger.info(
        "Wrote review pack n_entries=%s out_dir=%s single_rater=%s",
        pack["n_entries"],
        out_dir,
        pack.get("single_rater"),
    )
    print(f"wrote={out_dir}")
    print(f"n_items={pack['n_items']}")
    print(f"n_entries={pack['n_entries']}")
    print(f"seed={pack['seed']}")
    print("single_rater=true")
    print("labels=complete,partial,incomplete,cannot_judge")
    print(f"overlay_replaced={len(replaced)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Paid generate is gated; dry-run and offline trial never network."""
    parser = argparse.ArgumentParser(prog="feedback_eval")
    sub = parser.add_subparsers(dest="cmd", required=True)
    dry = sub.add_parser("dry-run", help="Offline FakeClient B0–B3 over synthetic items")
    dry.add_argument("--n-items", type=int, default=4)
    dry.add_argument("--store-dir", type=Path, required=True)
    freeze_p = sub.add_parser("freeze", help="Write freeze.json from development.yaml")
    freeze_p.add_argument("--store-dir", type=Path, required=True)
    freeze_p.add_argument("--config", type=Path, default=None)
    estimate_p = sub.add_parser(
        "estimate-budget", help="Print 2080-output plan; unknown prices stay unknown"
    )
    estimate_p.add_argument("--config", type=Path, default=None)
    trial = sub.add_parser(
        "trial-dev", help="Offline FakeClient trial, or a gated paid probe"
    )
    trial.add_argument("--store-dir", type=Path, required=True)
    trial.add_argument("--offline", action="store_true")
    trial.add_argument("--paid", action="store_true")
    trial.add_argument("--n-items", type=int, default=None)
    trial.add_argument("--config", type=Path, default=None)
    official = sub.add_parser("run-official", help="Offline rehearsal or gated official run")
    official.add_argument("--store-dir", type=Path, required=True)
    official.add_argument("--offline", action="store_true")
    official.add_argument("--paid", action="store_true")
    official.add_argument("--n-items", type=int, default=None)
    official.add_argument("--config", type=Path, default=None)
    analyze_p = sub.add_parser(
        "analyze", help="Rebuild paired metrics from a frozen store; missing stays missing"
    )
    analyze_p.add_argument("--store-dir", type=Path, required=True)
    analyze_p.add_argument("--homology", type=Path, default=None)
    analyze_p.add_argument("--n-resamples", type=int, default=1000)
    analyze_p.add_argument("--seed", type=int, default=42)
    analyze_p.add_argument("--overlay", action="append", type=Path, default=None)
    analyze_p.add_argument("--no-overlay", action="store_true")
    review_p = sub.add_parser(
        "review-pack", help="Write a blinded 40-item content-review pack"
    )
    review_p.add_argument("--store-dir", type=Path, required=True)
    review_p.add_argument("--out-dir", type=Path, default=None)
    review_p.add_argument("--overlay", action="append", type=Path, default=None)
    review_p.add_argument("--no-overlay", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.cmd == "dry-run":
        dry_run(n_items=args.n_items, store_dir=args.store_dir)
        return 0
    if args.cmd == "freeze":
        return cmd_freeze(store_dir=args.store_dir, config_path=args.config)
    if args.cmd == "estimate-budget":
        cmd_estimate_budget(config_path=args.config)
        return 0
    if args.cmd == "trial-dev":
        if args.paid:
            return cmd_trial_dev_paid(
                store_dir=args.store_dir,
                n_items=args.n_items,
                config_path=args.config,
            )
        cmd_trial_dev_offline(
            store_dir=args.store_dir,
            n_items=args.n_items,
            config_path=args.config,
        )
        return 0
    if args.cmd == "run-official":
        if args.paid and args.offline:
            parser.error("run-official cannot be both --paid and --offline")
        if args.paid:
            return cmd_run_official_paid(
                store_dir=args.store_dir,
                n_items=args.n_items,
                config_path=args.config,
            )
        if args.offline:
            return cmd_run_official_offline(
                store_dir=args.store_dir,
                n_items=args.n_items,
                config_path=args.config,
            )
        parser.error("run-official requires --offline or --paid")
    if args.cmd == "analyze":
        return cmd_analyze(
            store_dir=args.store_dir,
            homology_path=args.homology,
            n_resamples=args.n_resamples,
            seed=args.seed,
            overlay_dirs=args.overlay,
            no_overlay=args.no_overlay,
        )
    if args.cmd == "review-pack":
        return cmd_review_pack(
            store_dir=args.store_dir,
            out_dir=args.out_dir,
            overlay_dirs=args.overlay,
            no_overlay=args.no_overlay,
        )
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
