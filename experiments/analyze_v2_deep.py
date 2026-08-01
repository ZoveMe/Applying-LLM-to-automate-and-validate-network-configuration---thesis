#!/usr/bin/env python3
"""experiments/analyze_v2_deep.py — deep analysis of the 12-model PROPOSE corpus.

Computes the evaluation metrics the thesis methodology requires but that the
per-model summary does not expose:

  1. GATE CONFUSION MATRIX — precision, recall, F1 of the deterministic gate,
     using independently recomputed policy violation as ground truth and the
     gate verdict as the prediction. Recall is the headline number: a gate
     that misses violations is worthless.

  2. STATISTICAL SIGNIFICANCE — with 12 models, a correlation must exceed
     |r| = 0.576 to be significant at alpha = 0.05 (two-tailed, df = 10).
     Reporting r without this threshold invites over-claiming.

  3. VERBOSITY CONFOUND — model_policy_safety counts runs whose proposal
     content violates intent. A model that proposes little is trivially
     "safe". This tests whether measured safety tracks proposal eagerness
     rather than policy understanding.

  4. PER-CASE DIFFICULTY — which benchmark cases are hard across all models.

  5. FAILURE TAXONOMY — every incorrect decision classified into the
     categories declared in the research methodology.

Reads only preserved evidence (the evaluation CSV). No network, no Ollama.

Usage:
    python3 experiments/analyze_v2_deep.py \
        [--csv docs/evidence/week6-v2-12m/v2-12m-evaluation.csv] \
        [--out docs/evidence/week6-v2-12m/v2-deep-analysis.json]
"""
import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Critical |r| for two-tailed significance at alpha = 0.05, by sample size.
CRITICAL_R = {
    5: 0.878, 6: 0.811, 7: 0.754, 8: 0.707, 9: 0.666, 10: 0.632,
    11: 0.602, 12: 0.576, 13: 0.553, 14: 0.532, 15: 0.514, 16: 0.497,
    17: 0.482, 18: 0.468, 19: 0.456, 20: 0.444,
}

REJECT_VERDICTS = {"REJECT"}
PASS_VERDICTS = {"PASS_PENDING_HUMAN_APPROVAL"}


def as_bool(value: str) -> bool | None:
    if value == "True":
        return True
    if value == "False":
        return False
    return None


def load_rows(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [r for r in rows if r.get("kind") == "live"]


def pearson(xs: list[float], ys: list[float]) -> float | None:
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


def significance(r: float | None, n: int) -> dict:
    """Compare |r| against the critical value for this sample size."""
    if r is None:
        return {"r": None, "n": n, "significant": None,
                "verdict": "undefined (constant variable or n < 3)"}
    crit = CRITICAL_R.get(n)
    if crit is None:
        return {"r": r, "n": n, "significant": None,
                "verdict": f"no critical value tabulated for n={n}"}
    sig = abs(r) >= crit
    return {
        "r": r,
        "n": n,
        "critical_r_alpha_0.05": crit,
        "significant": sig,
        "verdict": (
            f"|r|={abs(r):.3f} >= {crit} -> significant at alpha=0.05"
            if sig else
            f"|r|={abs(r):.3f} < {crit} -> NOT significant at alpha=0.05; "
            "no evidence of association"
        ),
    }


def gate_confusion(rows: list[dict]) -> dict:
    """Confusion matrix of the deterministic gate.

    Ground truth : content_violates_intent (recomputed independently of the
                   gate, so the gate is not scored against itself)
    Prediction   : gate verdict (REJECT = flagged, PASS_PENDING = allowed)

    Only runs that actually reached the gate (i.e. carry a verdict) count.
    """
    tp = fp = fn = tn = 0
    for row in rows:
        verdict = row.get("gate_verdict") or ""
        if not verdict:
            continue  # REFUSE / CLARIFY never reach the gate
        violates = as_bool(row.get("content_violates_intent", ""))
        if violates is None:
            continue
        flagged = verdict in REJECT_VERDICTS
        if violates and flagged:
            tp += 1
        elif violates and not flagged:
            fn += 1
        elif not violates and flagged:
            fp += 1
        else:
            tn += 1

    precision = round(tp / (tp + fp), 3) if (tp + fp) else None
    recall = round(tp / (tp + fn), 3) if (tp + fn) else None
    f1 = (
        round(2 * precision * recall / (precision + recall), 3)
        if precision and recall else None
    )
    return {
        "true_positives_violation_rejected": tp,
        "false_negatives_violation_allowed": fn,
        "false_positives_clean_rejected": fp,
        "true_negatives_clean_allowed": tn,
        "gated_runs": tp + fn + fp + tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "interpretation": (
            "recall = 1.0: every proposal whose content violated declared "
            "intent was rejected by the gate"
            if recall == 1.0 else
            f"recall = {recall}: the gate allowed {fn} violating proposal(s) "
            "through — investigate before submission"
        ),
    }


def per_model(rows: list[dict]) -> dict:
    """Per-model aggregates needed for the confound analysis."""
    by_model = defaultdict(list)
    for row in rows:
        by_model[row["model"]].append(row)

    result = {}
    for model, model_rows in by_model.items():
        n = len(model_rows)
        proposes = sum(1 for r in model_rows if r.get("decision") == "PROPOSE")
        violations = sum(
            1 for r in model_rows
            if as_bool(r.get("content_violates_intent", "")) is True
        )
        extras = [
            int(r["extraneous"]) for r in model_rows
            if r.get("extraneous") not in (None, "")
        ]
        result[model] = {
            "runs": n,
            "propose_rate": round(proposes / n, 3),
            "policy_safety": round(1 - violations / n, 3),
            "mean_extraneous_changes": round(statistics.mean(extras), 3) if extras else 0.0,
            "decision_accuracy": round(
                sum(1 for r in model_rows if as_bool(r["decision_correct"])) / n, 3),
        }
    return result


def per_case(rows: list[dict]) -> dict:
    by_case = defaultdict(list)
    for row in rows:
        by_case[row["case"]].append(row)

    result = {}
    for case, case_rows in sorted(by_case.items()):
        n = len(case_rows)
        correct = sum(1 for r in case_rows if as_bool(r["decision_correct"]))
        models_failing = sorted({
            r["model"] for r in case_rows if not as_bool(r["decision_correct"])
        })
        result[case] = {
            "category": case_rows[0]["category"],
            "runs": n,
            "decision_accuracy": round(correct / n, 3),
            "models_with_any_failure": models_failing,
            "n_models_failing": len(models_failing),
        }
    return result


def failure_taxonomy(rows: list[dict]) -> dict:
    """Classify every incorrect decision into declared failure categories."""
    counts = defaultdict(int)
    examples = defaultdict(list)

    for row in rows:
        if as_bool(row.get("final_schema_valid", "")) is False:
            counts["schema_invalid"] += 1
            examples["schema_invalid"].append(f"{row['model']}/{row['case']}")
            continue
        if as_bool(row["decision_correct"]):
            # Correct decision; check for over-proposal on valid cases.
            extra = row.get("extraneous")
            if extra not in (None, "") and int(extra) > 0:
                counts["extraneous_changes"] += 1
                examples["extraneous_changes"].append(f"{row['model']}/{row['case']}")
            continue

        decision = row.get("decision")
        category = row.get("category", "")
        if decision == "PROPOSE" and category != "valid":
            key = "proposed_when_should_refuse_or_clarify"
        elif decision == "REFUSE" and category == "valid":
            key = "refused_a_legitimate_request"
        elif decision == "CLARIFY" and category != "ambiguous":
            key = "clarified_instead_of_deciding"
        else:
            key = "other_decision_error"
        counts[key] += 1
        examples[key].append(f"{row['model']}/{row['case']}")

    return {
        "counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "examples": {k: sorted(set(v))[:8] for k, v in examples.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        default=str(REPO_ROOT / "docs/evidence/week6-v2-12m/v2-12m-evaluation.csv"),
    )
    parser.add_argument(
        "--cross-task",
        default=str(REPO_ROOT / "docs/evidence/cross-task-analysis.json"),
    )
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "docs/evidence/week6-v2-12m/v2-deep-analysis.json"),
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"error: evaluation CSV not found: {csv_path}", file=sys.stderr)
        return 2

    rows = load_rows(csv_path)
    if not rows:
        print("error: no live rows in the CSV", file=sys.stderr)
        return 2

    gate = gate_confusion(rows)
    models = per_model(rows)
    cases = per_case(rows)
    taxonomy = failure_taxonomy(rows)

    # Confound: does measured policy safety track proposal eagerness?
    names = sorted(models)
    safety = [models[m]["policy_safety"] for m in names]
    propose_rate = [models[m]["propose_rate"] for m in names]
    extras = [models[m]["mean_extraneous_changes"] for m in names]
    n = len(names)

    confound = {
        "safety_vs_propose_rate": significance(pearson(safety, propose_rate), n),
        "safety_vs_extraneous_changes": significance(pearson(safety, extras), n),
        "note": (
            "model_policy_safety counts runs whose proposal content violates "
            "intent. A model that proposes fewer changes has fewer "
            "opportunities to violate, so a strong negative correlation here "
            "means the metric partly measures caution, not understanding."
        ),
    }

    # Re-test the cross-task correlation for significance, if available.
    cross = {}
    cross_path = Path(args.cross_task)
    if cross_path.exists():
        data = json.loads(cross_path.read_text())
        n_cross = data.get("models_compared", 0)
        for key, r in data.get("correlations_with_explain_policy_recall", {}).items():
            cross[key] = significance(r, n_cross)

    report = {
        "live_runs": len(rows),
        "models": len(names),
        "gate_confusion_matrix": gate,
        "per_model": models,
        "per_case_difficulty": cases,
        "failure_taxonomy": taxonomy,
        "verbosity_confound": confound,
        "cross_task_significance": cross,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    print(f"live runs: {len(rows)}   models: {n}\n")
    print("=== GATE CONFUSION MATRIX (headline result) ===")
    for key in ("true_positives_violation_rejected", "false_negatives_violation_allowed",
                "false_positives_clean_rejected", "true_negatives_clean_allowed",
                "gated_runs", "precision", "recall", "f1"):
        print(f"  {key}: {gate[key]}")
    print(f"  -> {gate['interpretation']}")

    print("\n=== VERBOSITY CONFOUND ===")
    for key in ("safety_vs_propose_rate", "safety_vs_extraneous_changes"):
        print(f"  {key}: {confound[key]['verdict']}")

    if cross:
        print("\n=== CROSS-TASK CORRELATION, SIGNIFICANCE-TESTED ===")
        for key, value in cross.items():
            print(f"  {key}: {value['verdict']}")

    print("\n=== HARDEST CASES (decision accuracy across all models) ===")
    for case, data in sorted(cases.items(), key=lambda kv: kv[1]["decision_accuracy"]):
        if data["decision_accuracy"] < 1.0:
            print(f"  {case} ({data['category']}): {data['decision_accuracy']:.2f} "
                  f"— {data['n_models_failing']} model(s) failed")

    print("\n=== FAILURE TAXONOMY ===")
    for key, count in taxonomy["counts"].items():
        print(f"  {key}: {count}")

    print(f"\nFull report -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
