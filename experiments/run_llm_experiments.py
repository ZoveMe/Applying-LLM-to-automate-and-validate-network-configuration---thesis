#!/usr/bin/env python3
"""experiments/run_llm_experiments.py — repeatable LLM evaluation.

Runs every case in llm_test_cases.yaml against one or more local models,
N times each. Saves one evidence JSON per run into docs/evidence/week4/,
plus summary.json and summary.csv with the aggregate metrics used in the
thesis evaluation chapter:

  - schema validity rate (first attempt)
  - outcome distribution (ACCEPTED / NOOP / REJECTED_GATE / REJECTED_SCHEMA)
  - safe rate (outcome within the case's acceptable list)
  - task-success rate (required route/rule content is present on valid cases)
  - pass@1 on valid cases (task-correct result on the first run)
  - mean latency per case

Usage:
    python3 experiments/run_llm_experiments.py --n 3
    python3 experiments/run_llm_experiments.py --models qwen3:4b-instruct --n 5
"""
import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "llm"))
from ollama_client import DEFAULT_MODEL, run_pipeline  # noqa: E402

DEFAULT_MODELS = [DEFAULT_MODEL, "qwen3:4b-instruct"]


def sanitize(name: str) -> str:
    return name.replace(":", "_").replace("/", "_")


def contains_required(proposal: dict | None, case: dict) -> bool:
    """Return True only when every labeled required change is proposed.

    Gate acceptance alone proves safety/topology validity, not that the LLM
    answered the requested task. This semantic subset check keeps those two
    measurements separate.
    """
    if proposal is None:
        return False
    proposed_routes = {tuple(sorted(item.items())) for item in proposal["static_routes"]}
    proposed_rules = {tuple(sorted(item.items())) for item in proposal["access_policy"]}
    required_routes = {
        tuple(sorted(item.items())) for item in case.get("expected_static_routes", [])
    }
    required_rules = {
        tuple(sorted(item.items())) for item in case.get("expected_access_policy", [])
    }
    return required_routes <= proposed_routes and required_rules <= proposed_rules


def task_correct(ev: dict, case: dict) -> bool:
    safe = ev["outcome"] in case["acceptable"]
    if case["category"] != "valid":
        return safe
    return safe and ev["outcome"] == "ACCEPTED" and contains_required(ev["proposal"], case)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the Week 4 LLM experiment matrix")
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--n", type=int, default=3, help="repetitions per case per model")
    ap.add_argument("--cases", default=str(REPO_ROOT / "experiments" / "llm_test_cases.yaml"))
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "docs" / "evidence" / "week4"))
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()

    if args.n < 1:
        ap.error("--n must be at least 1")

    cases = yaml.safe_load(Path(args.cases).read_text())
    if not cases:
        ap.error("the case file must contain at least one test case")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for model in args.models:
        for case in cases:
            for r in range(1, args.n + 1):
                ev = run_pipeline(case["requirement"], model=model, retries=args.retries)
                if ev["outcome"] == "ERROR_OLLAMA_UNREACHABLE":
                    print("FATAL: Ollama is not reachable. Start it (or set OLLAMA_URL) and rerun.")
                    return 4
                fname = f"{case['id']}_{sanitize(model)}_run{r}.json"
                (out_dir / fname).write_text(json.dumps(ev, indent=2))
                safe = ev["outcome"] in case["acceptable"]
                correct = task_correct(ev, case)
                first_ok = bool(ev["attempts"]) and ev["attempts"][0]["schema_valid"]
                rows.append(
                    {
                        "model": model,
                        "case": case["id"],
                        "category": case["category"],
                        "run": r,
                        "outcome": ev["outcome"],
                        "safe": safe,
                        "task_correct": correct,
                        "first_attempt_schema_valid": first_ok,
                        "attempts": len(ev["attempts"]),
                        "latency_s": ev["total_latency_s"],
                    }
                )
                print(f"{'SAFE  ' if safe else 'UNSAFE'} | "
                      f"{'CORRECT' if correct else 'WRONG  '} | {model} | "
                      f"{case['id']} run{r} | outcome={ev['outcome']} | "
                      f"{ev['total_latency_s']}s")

    # ---------- aggregate ----------
    per_case = []
    for model in args.models:
        for case in cases:
            sel = [x for x in rows if x["model"] == model and x["case"] == case["id"]]
            per_case.append(
                {
                    "model": model,
                    "case": case["id"],
                    "category": case["category"],
                    "n": len(sel),
                    "outcomes": dict(Counter(x["outcome"] for x in sel)),
                    "safe_rate": round(sum(x["safe"] for x in sel) / len(sel), 3),
                    "task_success_rate": round(
                        sum(x["task_correct"] for x in sel) / len(sel), 3),
                    "schema_first_attempt_rate": round(
                        sum(x["first_attempt_schema_valid"] for x in sel) / len(sel), 3),
                    "mean_latency_s": round(statistics.mean(x["latency_s"] for x in sel), 2),
                }
            )

    per_model = []
    valid_ids = [c["id"] for c in cases if c["category"] == "valid"]
    for model in args.models:
        sel = [x for x in rows if x["model"] == model]
        vsel = [x for x in sel if x["case"] in valid_ids]
        first_valid = [x for x in vsel if x["run"] == 1]
        per_model.append(
            {
                "model": model,
                "runs": len(sel),
                "overall_safe_rate": round(sum(x["safe"] for x in sel) / len(sel), 3),
                "valid_task_success_rate": round(
                    sum(x["task_correct"] for x in vsel) / len(vsel), 3
                ) if vsel else None,
                "pass_at_1_valid_cases": round(
                    sum(x["task_correct"] for x in first_valid) / len(first_valid), 3
                ) if first_valid else None,
                "schema_first_attempt_rate": round(
                    sum(x["first_attempt_schema_valid"] for x in sel) / len(sel), 3),
                "mean_latency_s": round(statistics.mean(x["latency_s"] for x in sel), 2),
            }
        )

    summary = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "models": args.models,
        "repetitions_per_case": args.n,
        "per_case": per_case,
        "per_model": per_model,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    with open(out_dir / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print("\n================ SUMMARY ================")
    for pm in per_model:
        print(f"{pm['model']}: safe={pm['overall_safe_rate']}, "
              f"valid_task_success={pm['valid_task_success_rate']}, "
              f"pass@1(valid)={pm['pass_at_1_valid_cases']}, "
              f"schema_first_try={pm['schema_first_attempt_rate']}, "
              f"mean_latency={pm['mean_latency_s']}s")
    print(f"Evidence + summary.json + summary.csv written to {out_dir}")
    unsafe = [x for x in rows if not x["safe"]]
    wrong = [x for x in rows if not x["task_correct"]]
    if unsafe:
        print(f"NOTE: {len(unsafe)} UNSAFE run(s) — these are the interesting ones. "
              f"Inspect their JSON files and classify them for the failure taxonomy.")
    if wrong:
        print(f"NOTE: {len(wrong)} task-incorrect run(s). A safe answer can still be "
              f"irrelevant or incomplete; inspect these separately from safety failures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
