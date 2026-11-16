"""Retry the two leftover identities at max_tokens=32768.

Does not write official/records. Reuses the previous retry's messages so the
prompt and B0 baseline stay identical. Raises the HTTP timeout because the
gpt-4o 205 B1 16384 attempt ended in a gateway 504 at ~241s.
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
from feedback_eval.scoring import score
from feedback_eval.store import load_mapping_yaml

PREV = ROOT / "runs/feedback_eval/retry_remaining_20261116"
OUT = ROOT / "runs/feedback_eval/retry_two_32768_20261116"
# Same next cap that recovered gpt-4o item 200 B3 after 16384 still emptied.
MAX_TOKENS = 32768
# Client wait must exceed the previous ~241s 504, or the process dies first.
TIMEOUT_S = 600.0
TARGETS = [
    ("gpt-4.1", "168", "B2"),
    ("gpt-4o", "205", "B1"),
]


def main() -> None:
    load_dotenv_file(ROOT / ".env")
    if not any(
        os.environ.get(k)
        for k in ("API_KEY", "FEEDBACK_EVAL_API_KEY", "AZURE_OPENAI_API_KEY")
    ):
        raise SystemExit("API key is not available")
    items = {
        str(x["key"]): x
        for x in map(json.loads, (ROOT / "data/IFBench_test.jsonl").read_text().splitlines())
    }
    config = load_mapping_yaml(ROOT / "configs/feedback_eval/official.yaml")
    clients = {
        m["id"]: build_http_client(m, timeout_s=TIMEOUT_S, max_retries=0)
        for m in config["models"]
    }
    OUT.mkdir(parents=True, exist_ok=False)
    summary = []
    for model, item_id, condition in TARGETS:
        prev = json.loads((PREV / f"{model}_{item_id}_{condition}.json").read_text())
        settings = dict(prev["settings"])
        settings["max_tokens"] = MAX_TOKENS
        print(
            f"start {model} item={item_id} {condition} max_tokens={MAX_TOKENS}",
            flush=True,
        )
        gen = clients[model].generate(prev["messages"], settings)
        status = classify_generation(gen)
        text = gen.get("text") or ""
        result = {
            "model": model,
            "item_id": item_id,
            "condition": condition,
            "previous_status": prev["accounting_status"],
            "previous_max_tokens": prev["settings"]["max_tokens"],
            "accounting_status": status,
            "messages": prev["messages"],
            "settings": settings,
            "generation": gen,
            "feedback": prev.get("feedback"),
            "score": score(items[item_id], text) if status == "success" else None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "baseline_b0_record_key": prev.get("baseline_b0_record_key"),
        }
        (OUT / f"{model}_{item_id}_{condition}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        )
        row = {
            "model": model,
            "item_id": item_id,
            "condition": condition,
            "previous_status": prev["accounting_status"],
            "accounting_status": status,
            "strict_pass": None
            if result["score"] is None
            else bool(result["score"]["strict"]["all_pass"]),
            "error_head": (gen.get("error") or "")[:160].replace("\n", " "),
        }
        summary.append(row)
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(
            f"done {model} item={item_id} {condition}: {status} "
            f"strict={row['strict_pass']}",
            flush=True,
        )
        err = gen.get("error") or ""
        if any(s in err for s in ("HTTP 401", "HTTP 403", "HTTP 429")):
            print("Stopped after authentication or quota error", flush=True)
            break


if __name__ == "__main__":
    main()
