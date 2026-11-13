"""Freeze, resume, and budget-estimate tests.

Tampering data/prompts/code/config or dropping freeze fields must refuse
resume. A non-empty run directory without freeze.json cannot be taken over.
Paid probes stay blocked unless authorization, API key, and a verified
balance are all present. Tests never open sockets or spend.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from feedback_eval.cli import apply_env_models, load_dotenv_file, main
from feedback_eval.client import OpenAICompatClient
from feedback_eval.store import (
    FreezeConflictError,
    FreezeMissingError,
    IncompleteFreezeError,
    RunStore,
    build_freeze,
    decide_paid_trial,
    estimate_budget,
    inspect_prior_ledger,
    load_mapping_yaml,
    select_review_ids,
    validate_resume,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEV_YAML = _REPO_ROOT / "configs" / "feedback_eval" / "development.yaml"
_SPLIT_PATH = _REPO_ROOT / "configs" / "feedback_eval" / "split.json"
_OFFICIAL_SHA256 = "d2ada7da94a38cfe406351614c4e686846ed2da6d1b339db95fa5ead19554a4a"
_SPLIT_SHA256 = "41ec3bf4e6a83079a2a7d824f0892e4f52df425e598896cd03900239e5421241"


def _hashes(**overrides: object) -> dict:
    payload = {
        "data_sha256": _OFFICIAL_SHA256,
        "split_sha256": _SPLIT_SHA256,
        "prompt_sha256": "p" * 64,
        "code_sha256": "c" * 64,
        "homology_sha256": "h" * 64,
        "constraint_support_sha256": "s" * 64,
    }
    payload.update(overrides)
    return payload


def _config(**overrides: object) -> dict:
    payload = {
        "protocol": "feedback-eval-dev-v1",
        "code_version": "store-v1",
        "git_commit": "2ac6b1561db3a566704424dac69e54d977decf51",
        "models": [
            {
                "slot": "model_a",
                "id": None,
                "api_base": None,
                "availability": "unverified",
                "unit_price": {
                    "input_per_million": None,
                    "output_per_million": None,
                    "currency": "USD",
                    "source": "unverified",
                },
            },
            {
                "slot": "model_b",
                "id": None,
                "api_base": None,
                "availability": "unverified",
                "unit_price": {
                    "input_per_million": None,
                    "output_per_million": None,
                    "currency": "USD",
                    "source": "unverified",
                },
            },
        ],
        "parameters": {"temperature": 0.0, "max_tokens": 4096, "seed": 42},
        "scoring": {
            "primary_metric": "strict_prompt_level",
            "official_strict": "evaluation_lib.test_instruction_following_strict",
            "official_loose": "evaluation_lib.test_instruction_following_loose",
            "loose_is_not_human_judgment": True,
        },
        "feedback": {
            "b2": "overall_strict_status",
            "b3": "per_constraint_strict_plus_prompt_text",
            "verifier_error": "skip_revision_record_feedback_unavailable",
            "mapping_error": "skip_revision_record_feedback_unavailable",
        },
        "retry": {
            "rate_limit_retries": 1,
            "timeout_retries": 0,
            "missing_data_rule": "report_missing_do_not_impute",
        },
        "human_review": {
            "n_items": 40,
            "seed": 42,
            "split": "test",
            "item_ids": [str(i) for i in range(40)],
            "blinding": "hide_model_and_condition",
        },
        "cost_cap": {"amount": None, "currency": "USD", "status": "unverified"},
        "budget_estimate": {
            "planned_outputs": 2080,
            "retry_outputs": 208,
            "repeatability_outputs": 0,
            "total_outputs": 2288,
            "estimated_cost": {"amount": None, "currency": "USD", "status": "unknown"},
            "incurred_spend": None,
            "incurred_status": "unverified_missing",
            "stop_reason": "balance_unverified",
        },
        "paid_trial": {
            "allowed": False,
            "reason": "authorization_missing",
        },
    }
    payload.update(overrides)
    return payload


def _verified_unit_price() -> dict:
    return {
        "input_per_million": 1.0,
        "output_per_million": 2.0,
        "currency": "USD",
        "source": "verified",
    }


def _ready_config() -> dict:
    """A freeze payload that should be eligible for an official run."""
    return _config(
        models=[
            {
                "slot": "model_a",
                "id": "vendor/model-a",
                "api_base": "https://example.invalid/v1",
                "availability": "verified",
                "unit_price": _verified_unit_price(),
            },
            {
                "slot": "model_b",
                "id": "vendor/model-b",
                "api_base": "https://example.invalid/v1",
                "availability": "verified",
                "unit_price": _verified_unit_price(),
            },
        ],
        cost_cap={"amount": 50.0, "currency": "USD", "status": "confirmed"},
        paid_trial={"allowed": True, "reason": None},
    )


def _fingerprint_config() -> dict:
    return {
        "protocol": "feedback-eval-dev-v1",
        "data_sha256": _OFFICIAL_SHA256,
        "model": "model_a,model_b",
        "prompt_sha256": "p" * 64,
        "code_version": "store-v1",
        "parameters": {"temperature": 0.0, "max_tokens": 4096, "seed": 42},
    }


def test_yaml_scalar_sequence_advances_index(tmp_path: Path) -> None:
    """Dash-plus-text items must consume the line; otherwise parsing hangs."""
    path = tmp_path / "seq.yaml"
    path.write_text(
        "items:\n"
        "  - alpha\n"
        "  - bravo\n"
        "  - 'quoted: colon'\n"
        "nested:\n"
        "  - slot: model_a\n"
        "    id: null\n",
        encoding="utf-8",
    )
    raw = load_mapping_yaml(path)
    assert raw["items"] == ["alpha", "bravo", "quoted: colon"]
    assert raw["nested"] == [{"slot": "model_a", "id": None}]


def test_development_yaml_declares_two_unverified_candidates() -> None:
    raw = load_mapping_yaml(_DEV_YAML)
    assert raw["budget_planning"]["planned_outputs"] == 2080
    assert raw["budget_planning"]["n_test_items"] == 260
    assert raw["budget_planning"]["n_models"] == 2
    assert raw["budget_planning"]["n_conditions"] == 4
    assert len(raw["models"]) == 2
    for model in raw["models"]:
        assert model["id"] is None
        assert model["availability"] == "unverified"
        assert model["unit_price"]["source"] == "unverified"
        assert model["unit_price"]["input_per_million"] is None
    assert raw["cost_cap"]["amount"] is None
    assert raw["cost_cap"]["status"] == "unverified"
    assert raw["parameters"]["thinking"] == {"type": "disabled"}


def test_official_yaml_declares_hkbu_call_cap() -> None:
    raw = load_mapping_yaml(_REPO_ROOT / "configs" / "feedback_eval" / "official.yaml")
    assert raw["protocol"] == "feedback-eval-official-hkbu-v1"
    assert raw["budget_planning"]["planned_outputs"] == 2080
    assert raw["cost_cap"]["unit"] == "calls"
    assert raw["cost_cap"]["status"] == "confirmed"
    assert raw["cost_cap"]["call_cap"] == 2288
    assert raw["cost_cap"]["amount"] is None
    assert raw["parameters"]["thinking"] == {"type": "disabled"}
    ids = {model["id"] for model in raw["models"]}
    assert ids == {"gpt-4.1", "gpt-4o"}
    for model in raw["models"]:
        assert model["availability"] == "verified"
        assert model["provider"] == "azure"
        assert model["api_base"] == "https://genai.hkbu.edu.hk/general/rest"
        assert model["unit_price"]["source"] == "unverified"


def test_build_freeze_pins_hashes_and_is_stable() -> None:
    first = build_freeze(_config(), _hashes())
    second = build_freeze(_config(), _hashes())
    required = {
        "schema_version",
        "freeze_sha256",
        "protocol",
        "code_version",
        "git_commit",
        "code_sha256",
        "data_sha256",
        "split_sha256",
        "prompt_sha256",
        "homology_sha256",
        "constraint_support_sha256",
        "models",
        "parameters",
        "scoring",
        "feedback",
        "retry",
        "human_review",
        "cost_cap",
        "budget_estimate",
        "paid_trial",
        "ready_for_official_run",
    }
    assert required <= set(first)
    assert first["freeze_sha256"] == second["freeze_sha256"]
    assert first["data_sha256"] == _OFFICIAL_SHA256
    assert first["split_sha256"] == _SPLIT_SHA256
    assert first["ready_for_official_run"] is False
    assert first["models"][0]["id"] is None
    assert len(first["models"]) == 2


def test_ready_for_official_run_requires_input_and_output_prices() -> None:
    """Official readiness must match estimate_budget: both unit prices present."""
    hashes = _hashes()
    ready = build_freeze(_ready_config(), hashes)
    assert ready["ready_for_official_run"] is True

    missing_output = _ready_config()
    missing_output["models"] = copy.deepcopy(missing_output["models"])
    missing_output["models"][1]["unit_price"]["output_per_million"] = None
    assert build_freeze(missing_output, hashes)["ready_for_official_run"] is False

    missing_input = _ready_config()
    missing_input["models"] = copy.deepcopy(missing_input["models"])
    missing_input["models"][0]["unit_price"]["input_per_million"] = None
    assert build_freeze(missing_input, hashes)["ready_for_official_run"] is False


def test_ready_for_official_run_accepts_confirmed_call_cap_without_prices() -> None:
    """HKBU GenAI has no published CNY tariff; a confirmed call cap is the stop."""
    hashes = _hashes()
    config = _config(
        models=[
            {
                "slot": "model_a",
                "id": "gpt-4.1",
                "api_base": "https://genai.hkbu.edu.hk/general/rest",
                "provider": "azure",
                "availability": "verified",
                "unit_price": {
                    "input_per_million": None,
                    "output_per_million": None,
                    "currency": None,
                    "source": "unverified",
                },
            },
            {
                "slot": "model_b",
                "id": "gpt-4o",
                "api_base": "https://genai.hkbu.edu.hk/general/rest",
                "provider": "azure",
                "availability": "verified",
                "unit_price": {
                    "input_per_million": None,
                    "output_per_million": None,
                    "currency": None,
                    "source": "unverified",
                },
            },
        ],
        cost_cap={
            "amount": None,
            "currency": None,
            "unit": "calls",
            "call_cap": 2288,
            "status": "confirmed",
        },
        paid_trial={"allowed": True, "reason": None},
    )
    freeze = build_freeze(config, hashes)
    assert freeze["ready_for_official_run"] is True
    config["cost_cap"]["call_cap"] = 0
    assert build_freeze(config, hashes)["ready_for_official_run"] is False


def test_validate_resume_accepts_identical_freeze() -> None:
    freeze = build_freeze(_config(), _hashes())
    validate_resume(freeze, build_freeze(_config(), _hashes()))


def test_tampered_config_rejects_resume() -> None:
    existing = build_freeze(_config(), _hashes())
    current = build_freeze(
        _config(parameters={"temperature": 0.7, "max_tokens": 4096, "seed": 42}),
        _hashes(),
    )
    with pytest.raises(FreezeConflictError):
        validate_resume(existing, current)


def test_tampered_data_hash_rejects_resume() -> None:
    existing = build_freeze(_config(), _hashes())
    current = build_freeze(_config(), _hashes(data_sha256="0" * 64))
    with pytest.raises(FreezeConflictError):
        validate_resume(existing, current)


def test_tampered_prompt_hash_rejects_resume() -> None:
    existing = build_freeze(_config(), _hashes())
    current = build_freeze(_config(), _hashes(prompt_sha256="1" * 64))
    with pytest.raises(FreezeConflictError):
        validate_resume(existing, current)


def test_tampered_code_hash_rejects_resume() -> None:
    existing = build_freeze(_config(), _hashes())
    current = build_freeze(_config(), _hashes(code_sha256="2" * 64))
    with pytest.raises(FreezeConflictError):
        validate_resume(existing, current)


def test_missing_freeze_fields_reject_resume() -> None:
    current = build_freeze(_config(), _hashes())
    with pytest.raises(IncompleteFreezeError):
        validate_resume({"protocol": "feedback-eval-dev-v1"}, current)
    broken = dict(current)
    broken.pop("prompt_sha256")
    with pytest.raises(IncompleteFreezeError):
        validate_resume(broken, current)


def test_tampered_stored_hash_fails_integrity_before_content_compare() -> None:
    existing = build_freeze(_config(), _hashes())
    tampered = dict(existing)
    tampered["freeze_sha256"] = "0" * 64
    with pytest.raises(FreezeConflictError):
        validate_resume(tampered, existing)


def test_nonempty_directory_without_freeze_cannot_be_taken_over(tmp_path: Path) -> None:
    (tmp_path / "orphan.json").write_text("{}\n", encoding="utf-8")
    freeze = build_freeze(_config(), _hashes())
    with pytest.raises(FreezeMissingError):
        RunStore(tmp_path).initialize_frozen(
            _fingerprint_config(), inspect_prior_ledger([]), freeze
        )


def test_initialize_frozen_writes_and_resumes(tmp_path: Path) -> None:
    freeze = build_freeze(_config(), _hashes())
    first = RunStore(tmp_path)
    first.initialize_frozen(_fingerprint_config(), inspect_prior_ledger([]), freeze)
    assert first.freeze_path.is_file()
    RunStore(tmp_path).initialize_frozen(
        _fingerprint_config(), inspect_prior_ledger([]), freeze
    )


def test_initialize_frozen_rejects_changed_prompts(tmp_path: Path) -> None:
    freeze = build_freeze(_config(), _hashes())
    RunStore(tmp_path).initialize_frozen(
        _fingerprint_config(), inspect_prior_ledger([]), freeze
    )
    changed = build_freeze(_config(), _hashes(prompt_sha256="9" * 64))
    with pytest.raises(FreezeConflictError):
        RunStore(tmp_path).initialize_frozen(
            _fingerprint_config(), inspect_prior_ledger([]), changed
        )


def test_budget_estimate_includes_2080_retries_and_unverified_spend() -> None:
    prior = inspect_prior_ledger(
        [_REPO_ROOT / "runs" / "feedback_eval" / "ledger.json"]
    )
    estimate = estimate_budget(
        {
            "n_test_items": 260,
            "n_models": 2,
            "n_conditions": 4,
            "planned_outputs": 2080,
            "retry_uplift": 0.1,
            "repeatability_outputs": 0,
        },
        prior,
    )
    assert estimate["planned_outputs"] == 2080
    assert estimate["retry_outputs"] == 208
    assert estimate["repeatability_outputs"] == 0
    assert estimate["total_outputs"] == 2288
    assert estimate["estimated_cost"]["amount"] is None
    assert estimate["estimated_cost"]["status"] == "unknown"
    assert estimate["incurred_spend"] is None
    assert estimate["incurred_spend"] != 0
    assert estimate["incurred_status"] == "unverified_missing"
    assert estimate["stop_reason"] == "balance_unverified"


def test_budget_estimate_does_not_invent_cost_from_unverified_prices() -> None:
    estimate = estimate_budget(
        {
            "n_test_items": 260,
            "n_models": 2,
            "n_conditions": 4,
            "planned_outputs": 2080,
            "retry_uplift": 0.1,
            "repeatability_outputs": 0,
        },
        {"status": "unverified_missing", "spent": None, "balance": None, "unit_prices": None},
        observed_usage={
            "prompt_tokens": 1600,
            "completion_tokens": 3200,
            "n_outputs": 16,
        },
        unit_prices={
            "input_per_million": 1.0,
            "output_per_million": 2.0,
            "currency": "USD",
            "source": "unverified",
        },
    )
    assert estimate["estimated_cost"]["amount"] is None
    assert estimate["observed_usage"]["n_outputs"] == 16


def test_paid_trial_blocked_without_authorization_key_or_balance() -> None:
    prior = {"status": "unverified_missing", "spent": None, "balance": None}
    cap = {"amount": None, "currency": "USD", "status": "unverified"}
    blocked_auth = decide_paid_trial({}, prior, cap)
    assert blocked_auth["allowed"] is False
    assert blocked_auth["reason"] == "authorization_missing"
    blocked_key = decide_paid_trial(
        {"FEEDBACK_EVAL_AUTHORIZE_SPEND": "1"}, prior, cap
    )
    assert blocked_key["allowed"] is False
    assert blocked_key["reason"] == "api_key_missing"
    blocked_balance = decide_paid_trial(
        {
            "FEEDBACK_EVAL_AUTHORIZE_SPEND": "1",
            "API_KEY": "secret-key",
        },
        prior,
        cap,
    )
    assert blocked_balance["allowed"] is False
    assert blocked_balance["reason"] == "balance_unverified"
    still_blocked = decide_paid_trial(
        {
            "FEEDBACK_EVAL_AUTHORIZE_SPEND": "1",
            "API_KEY": "secret-key",
        },
        {"status": "ok", "spent": 1.0, "balance": 10.0},
        cap,
    )
    assert still_blocked["allowed"] is False
    assert still_blocked["reason"] == "cost_cap_unverified"
    # Chat-authorized development trial may run under a call cap without
    # inventing a historical balance.
    allowed_dev = decide_paid_trial(
        {
            "FEEDBACK_EVAL_AUTHORIZE_SPEND": "1",
            "API_KEY": "secret-key",
        },
        prior,
        cap,
        development=True,
        call_limit=16,
    )
    assert allowed_dev["allowed"] is True
    assert allowed_dev["prior_spent"] is None
    assert allowed_dev["call_limit"] == 16
    blocked_dev_limit = decide_paid_trial(
        {
            "FEEDBACK_EVAL_AUTHORIZE_SPEND": "1",
            "API_KEY": "secret-key",
        },
        prior,
        {
            "amount": None,
            "currency": None,
            "unit": "calls",
            "call_cap": 2288,
            "status": "confirmed",
        },
        development=True,
        call_limit=0,
    )
    assert blocked_dev_limit["allowed"] is False
    assert blocked_dev_limit["reason"] == "call_limit_missing"
    allowed_calls = decide_paid_trial(
        {
            "FEEDBACK_EVAL_AUTHORIZE_SPEND": "1",
            "API_KEY": "secret-key",
        },
        prior,
        {
            "amount": None,
            "currency": None,
            "unit": "calls",
            "call_cap": 2288,
            "status": "confirmed",
        },
    )
    assert allowed_calls["allowed"] is True
    assert allowed_calls["call_cap"] == 2288
    assert allowed_calls["prior_spent"] is None


def test_paid_trial_cli_stops_without_network(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["trial-dev", "--paid", "--store-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code != 0
    assert "authorization_missing" in captured.out or "authorization_missing" in captured.err
    blocker = tmp_path / "paid_trial.json"
    assert blocker.is_file()
    payload = json.loads(blocker.read_text(encoding="utf-8"))
    assert payload["allowed"] is False
    assert payload["reason"] == "authorization_missing"
    assert not list(tmp_path.glob("records/*.json"))


def test_review_ids_are_deterministic_and_from_test_split() -> None:
    split = json.loads(_SPLIT_PATH.read_text(encoding="utf-8"))
    first = select_review_ids(split["test_ids"], n=40, seed=42)
    second = select_review_ids(split["test_ids"], n=40, seed=42)
    assert first == second
    assert len(first) == 40
    assert len(set(first)) == 40
    assert set(first).isdisjoint(split["dev_ids"])
    assert set(first) <= set(split["test_ids"])


def test_freeze_cli_writes_record(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["freeze", "--store-dir", str(tmp_path)])
    capsys.readouterr()
    assert code == 0
    freeze_path = tmp_path / "freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    assert freeze["data_sha256"] == _OFFICIAL_SHA256
    assert freeze["split_sha256"] == _SPLIT_SHA256
    assert freeze["budget_estimate"]["planned_outputs"] == 2080
    assert freeze["ready_for_official_run"] is False
    assert freeze["paid_trial"]["allowed"] is False
    assert len(freeze["human_review"]["item_ids"]) == 40
    # Resume with the same sources must succeed.
    assert main(["freeze", "--store-dir", str(tmp_path)]) == 0


def test_dotenv_loads_missing_keys_without_exposing_values(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("API_KEY=secret-key\nAPI_BASE=http://127.0.0.1:9/v1\n", encoding="utf-8")
    env: dict[str, str] = {}
    loaded = load_dotenv_file(path, env)
    assert loaded == ["API_KEY", "API_BASE"]
    assert env["API_KEY"] == "secret-key"
    overlay = apply_env_models(
        load_mapping_yaml(_DEV_YAML),
        {"API_BASE": "http://127.0.0.1:9/v1", "MODEL_A": "cand-a", "MODEL_B": "cand-b"},
    )
    blob = json.dumps(overlay)
    assert "secret-key" not in blob
    assert overlay["models"][0]["id"] == "cand-a"
    assert overlay["models"][1]["id"] == "cand-b"


def test_paid_trial_cli_uses_live_client_when_authorized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FEEDBACK_EVAL_AUTHORIZE_SPEND", "1")
    monkeypatch.setenv("API_KEY", "secret-key")
    monkeypatch.setenv("API_BASE", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("MODEL_A", "cand-a")
    monkeypatch.setenv("MODEL_B", "cand-b")
    monkeypatch.setenv("FEEDBACK_EVAL_DEV_CALL_LIMIT", "16")

    def fake_generate(self: OpenAICompatClient, messages: list[dict], settings: dict) -> dict:
        assert "secret-key" not in json.dumps(messages)
        assert settings.get("thinking") == {"type": "disabled"}
        return {
            "text": "ping and but so",
            "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            "finish_reason": "stop",
            "latency_ms": 2.0,
            "error": None,
        }

    monkeypatch.setattr(OpenAICompatClient, "generate", fake_generate)
    code = main(
        ["trial-dev", "--paid", "--n-items", "1", "--store-dir", str(tmp_path)]
    )
    captured = capsys.readouterr().out
    assert code == 0
    assert "secret-key" not in captured
    summary = json.loads((tmp_path / "trial_summary.json").read_text(encoding="utf-8"))
    assert summary["client"] == "OpenAICompatClient"
    assert summary["paid"] is True
    assert summary["actual_calls"] >= 2
    dumped = "".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.json"))
    assert "secret-key" not in dumped


def test_offline_trial_cli_uses_fake_client(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["trial-dev", "--offline", "--n-items", "1", "--store-dir", str(tmp_path)]
    )
    captured = capsys.readouterr().out
    assert code == 0
    assert "planned_calls=8" in captured
    summary = json.loads((tmp_path / "trial_summary.json").read_text(encoding="utf-8"))
    assert summary["client"] == "FakeClient"
    assert summary["n_models"] == 2
    assert summary["actual_calls"] == 8
    assert summary["paid"] is False
