"""Retry IFBench item 147 (Azure content-filter cascade) without rewriting official records.

B0 was filtered for both models; B1–B3 then sent an empty assistant turn and
got HTTP 400. Revisions only run after that model's sidecar B0 has non-empty
text. The prompt is unchanged: modifying it would leave the IFBench item.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from feedback_eval.client import build_http_client, classify_generation
from feedback_eval.environment import load_dotenv_file
from feedback_eval.feedback import make_feedback
from feedback_eval.runner import build_revision_user_message, load_prompt_templates
from feedback_eval.scoring import score
from feedback_eval.store import load_mapping_yaml

ITEM_ID = "147"
MODELS = ("gpt-4.1", "gpt-4o")
CONDITIONS = ("B0", "B1", "B2", "B3")
# Content filter is not a token-cap failure; 16384 still protects against
# empty-B0 if Azure lets the screenplay through.
MAX_TOKENS = 16384
TIMEOUT_S = 600.0
OUT = ROOT / "runs/feedback_eval/retry_item147_20261116"


def _write_json(path: Path, payload: dict | list) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _row(result: dict) -> dict:
    gen = result.get("generation") or {}
    sc = result.get("score")
    return {
        "model": result["model"],
        "item_id": result["item_id"],
        "condition": result["condition"],
        "previous_status": result.get("previous_status"),
        "accounting_status": result["accounting_status"],
        "strict_pass": None
        if sc is None
        else bool(sc["strict"]["all_pass"]),
        "error_head": (gen.get("error") or "")[:200].replace("\n", " "),
    }


def main() -> None:
    load_dotenv_file(ROOT / ".env")
    if not any(
        os.environ.get(k)
        for k in ("API_KEY", "FEEDBACK_EVAL_API_KEY", "AZURE_OPENAI_API_KEY")
    ):
        raise SystemExit("API key is not available")

    items = {
        str(x["key"]): x
        for x in map(
            json.loads, (ROOT / "data/IFBench_test.jsonl").read_text().splitlines()
        )
    }
    item = items[ITEM_ID]
    config = load_mapping_yaml(ROOT / "configs/feedback_eval/official.yaml")
    clients = {
        m["id"]: build_http_client(m, timeout_s=TIMEOUT_S, max_retries=0)
        for m in config["models"]
    }
    templates = load_prompt_templates()
    OUT.mkdir(parents=True, exist_ok=False)
    summary: list[dict] = []

    for model in MODELS:
        settings = {
            "max_tokens": MAX_TOKENS,
            "model": model,
            "seed": 42,
            "temperature": 0.0,
            "thinking": {"type": "disabled"},
        }
        b0_text = ""
        b0_score = None
        stop = False
        for condition in CONDITIONS:
            if condition != "B0" and not b0_text:
                # Empty assistant turns reproduce the original HTTP 400 cascade.
                result = {
                    "model": model,
                    "item_id": ITEM_ID,
                    "condition": condition,
                    "previous_status": "error",
                    "accounting_status": "skipped_empty_b0",
                    "messages": None,
                    "settings": settings,
                    "generation": {
                        "text": None,
                        "usage": None,
                        "finish_reason": None,
                        "latency_ms": None,
                        "error": "skipped: B0 still has no text after content-filter retry",
                    },
                    "feedback": None,
                    "score": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                _write_json(OUT / f"{model}_{ITEM_ID}_{condition}.json", result)
                summary.append(_row(result))
                _write_json(OUT / "summary.json", summary)
                print(
                    f"skip {model} item={ITEM_ID} {condition}: skipped_empty_b0",
                    flush=True,
                )
                continue

            if condition == "B0":
                messages = [{"role": "user", "content": item["prompt"]}]
                feedback = None
            else:
                passed = bool((b0_score or {}).get("strict", {}).get("all_pass"))
                feedback = (
                    make_feedback(item, b0_score["strict"], condition)
                    if condition in ("B2", "B3")
                    else None
                )
                messages = [
                    {"role": "user", "content": item["prompt"]},
                    {"role": "assistant", "content": b0_text},
                    {
                        "role": "user",
                        "content": build_revision_user_message(
                            templates,
                            condition,
                            feedback,
                            passed if condition != "B1" else None,
                        ),
                    },
                ]

            print(
                f"start {model} item={ITEM_ID} {condition} max_tokens={MAX_TOKENS}",
                flush=True,
            )
            gen = clients[model].generate(messages, settings)
            status = classify_generation(gen)
            text = gen.get("text") or ""
            result = {
                "model": model,
                "item_id": ITEM_ID,
                "condition": condition,
                "previous_status": "error",
                "accounting_status": status,
                "messages": messages,
                "settings": settings,
                "generation": gen,
                "feedback": feedback,
                "score": score(item, text) if status == "success" else None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            if condition == "B0" and status == "success":
                b0_text = text
                b0_score = result["score"]
            _write_json(OUT / f"{model}_{ITEM_ID}_{condition}.json", result)
            summary.append(_row(result))
            _write_json(OUT / "summary.json", summary)
            print(
                f"done {model} item={ITEM_ID} {condition}: {status} "
                f"strict={summary[-1]['strict_pass']}",
                flush=True,
            )
            err = gen.get("error") or ""
            if any(s in err for s in ("HTTP 401", "HTTP 403", "HTTP 429")):
                print("Stopped after authentication or quota error", flush=True)
                stop = True
                break
        if stop:
            break


if __name__ == "__main__":
    main()
