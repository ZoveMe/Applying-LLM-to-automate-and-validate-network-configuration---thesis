#!/usr/bin/env python3
"""experiments/analyze_cross_task.py — cross-task consistency analysis.

The thesis measures each model on two independent assistive roles:

    EXPLAIN  — read an existing configuration, describe it faithfully
    PROPOSE  — read a natural-language requirement, emit a structured change

This script joins the two campaigns per model and asks the question neither
campaign answers alone:

    Does a model that fails to SEE the access policy when explaining a
    configuration also fail to RESPECT the access policy when proposing one?

Two outcomes, both meaningful:
  * correlated   -> policy-blindness is a stable model property; the cheap
                    EXPLAIN benchmark predicts risk in the expensive task.
  * independent  -> the two failures are unrelated; neither task can be used
                    as a proxy for the other, and both must be measured.

And one invariant that must hold regardless: system_policy_safety = 1.0 for
every model. The pipeline's safety comes from the deterministic gate, not
from model quality. A single unsafe ACCEPTED outcome would falsify that.

Reads only preserved evidence. No network access, no Ollama, no deployment.

Usage:
    python3 experiments/analyze_cross_task.py \
        [--explain-root docs/evidence/week6-explain-13m] \
        [--v2-eval docs/evidence/week6-v2-12m/v2-12m-evaluation.json] \
        [--out docs/evidence/cross-task-analysis.json]
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPLAIN_SUITES = ("small", "l-clean", "l-faulty")


def load_explain(explain_root: Path) -> dict:
    """Mean EXPLAIN policy recall per model, averaged over available suites."""
    per_model: dict[str, list[float]] = {}
    for suite in EXPLAIN_SUITES:
        path = explain_root / f"{suite}-evaluation.json"
        if not path.exists():
            print(f"warning: missing {path}", file=sys.stderr)
            continue
        aggregate = json.loads(path.read_text())["aggregate"]
        for model, metrics in aggregate.items():
            recall = metrics.get("mean_policy_recall")
            if recall is not None:
                per_model.setdefault(model, []).append(recall)
    return {m: round(statistics.mean(v), 3) for m, v in per_model.items() if v}


def load_v2(v2_eval: Path) -> dict:
    """Per-model PROPOSE metrics from the V2 evaluation report."""
    report = json.loads(v2_eval.read_text())
    return {
        entry["model"]: {
            "model_policy_safety": entry.get("model_policy_safety"),
            "system_policy_safety": entry.get("system_policy_safety"),
            "decision_accuracy": entry.get("decision_accuracy"),
            "schema_validity_first_attempt": entry.get("schema_validity_first_attempt"),
            "runs": entry.get("runs"),
        }
        for entry in report.get("model_performance_live_only", [])
    }


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson r. None when undefined (n < 3 or a variable is constant)."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs)
    dy = sum((y - my) ** 2 for y in ys)
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy) ** 0.5, 3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--explain-root",
        default=str(REPO_ROOT / "docs/evidence/week6-explain-13m"),
    )
    parser.add_argument(
        "--v2-eval",
        default=str(REPO_ROOT / "docs/evidence/week6-v2-12m/v2-12m-evaluation.json"),
    )
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "docs/evidence/cross-task-analysis.json"),
    )
    args = parser.parse_args()

    explain = load_explain(Path(args.explain_root))
    v2_path = Path(args.v2_eval)
    if not v2_path.exists():
        print(
            f"error: V2 evaluation not found: {v2_path}\n"
            "Run the PROPOSE campaign first:\n"
            "    bash benchmarks/run_v2_benchmark.sh",
            file=sys.stderr,
        )
        return 2
    v2 = load_v2(v2_path)

    shared = sorted(set(explain) & set(v2))
    if not shared:
        print("error: no models present in both campaigns", file=sys.stderr)
        return 2

    rows = []
    for model in shared:
        rows.append(
            {
                "model": model,
                "explain_policy_recall": explain[model],
                "propose_model_policy_safety": v2[model]["model_policy_safety"],
                "propose_system_policy_safety": v2[model]["system_policy_safety"],
                "propose_decision_accuracy": v2[model]["decision_accuracy"],
                "propose_schema_validity": v2[model]["schema_validity_first_attempt"],
                "propose_runs": v2[model]["runs"],
            }
        )

    xs = [r["explain_policy_recall"] for r in rows]

    correlations = {}
    for key in (
        "propose_model_policy_safety",
        "propose_decision_accuracy",
        "propose_schema_validity",
    ):
        ys = [r[key] for r in rows]
        if any(y is None for y in ys):
            correlations[key] = None
            continue
        correlations[key] = pearson(xs, ys)

    # The invariant: the gate must hold for every model.
    unsafe = [
        r["model"]
        for r in rows
        if r["propose_system_policy_safety"] is not None
        and r["propose_system_policy_safety"] < 1.0
    ]

    # Group comparison: policy-blind (EXPLAIN recall 0) vs policy-aware.
    blind = [r for r in rows if r["explain_policy_recall"] == 0.0]
    aware = [r for r in rows if r["explain_policy_recall"] > 0.0]

    def group_mean(group: list, key: str) -> float | None:
        vals = [r[key] for r in group if r[key] is not None]
        return round(statistics.mean(vals), 3) if vals else None

    report = {
        "models_compared": len(rows),
        "per_model": rows,
        "correlations_with_explain_policy_recall": correlations,
        "group_comparison": {
            "policy_blind_models": [r["model"] for r in blind],
            "policy_aware_models": [r["model"] for r in aware],
            "blind_mean_propose_model_policy_safety": group_mean(
                blind, "propose_model_policy_safety"),
            "aware_mean_propose_model_policy_safety": group_mean(
                aware, "propose_model_policy_safety"),
            "blind_mean_decision_accuracy": group_mean(
                blind, "propose_decision_accuracy"),
            "aware_mean_decision_accuracy": group_mean(
                aware, "propose_decision_accuracy"),
        },
        "system_safety_invariant_holds": not unsafe,
        "models_violating_system_safety": unsafe,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    print(f"models compared: {len(rows)}\n")
    print(f"{'model':<44}{'EXPL pol':>9}{'PROP safe':>10}{'SYS safe':>9}{'decision':>9}")
    for r in rows:
        print(
            f"{r['model']:<44}"
            f"{r['explain_policy_recall']:>9.2f}"
            f"{(r['propose_model_policy_safety'] if r['propose_model_policy_safety'] is not None else -1):>10.2f}"
            f"{(r['propose_system_policy_safety'] if r['propose_system_policy_safety'] is not None else -1):>9.2f}"
            f"{(r['propose_decision_accuracy'] if r['propose_decision_accuracy'] is not None else -1):>9.2f}"
        )

    print("\ncorrelations with EXPLAIN policy recall:")
    for key, value in correlations.items():
        print(f"  {key}: r = {value if value is not None else 'undefined (constant or n<3)'}")

    group = report["group_comparison"]
    print(
        f"\npolicy-blind models (n={len(blind)}): "
        f"mean PROPOSE policy safety = {group['blind_mean_propose_model_policy_safety']}, "
        f"decision accuracy = {group['blind_mean_decision_accuracy']}"
    )
    print(
        f"policy-aware models (n={len(aware)}): "
        f"mean PROPOSE policy safety = {group['aware_mean_propose_model_policy_safety']}, "
        f"decision accuracy = {group['aware_mean_decision_accuracy']}"
    )

    if unsafe:
        print(f"\n*** SYSTEM SAFETY VIOLATED by: {', '.join(unsafe)} ***")
    else:
        print("\nSystem safety invariant HOLDS: no unsafe proposal was ACCEPTED "
              "for any model.")

    print(f"\nFull report -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
