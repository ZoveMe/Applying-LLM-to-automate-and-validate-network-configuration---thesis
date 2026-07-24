#!/usr/bin/env python3
"""experiments/evaluate_v2.py — offline evaluator for V2 evidence.

Pure post-hoc evaluation: reads per-run evidence JSONs (as written by
llm/ollama_client.py) plus the labeled benchmark corpus, and computes the
V2 metric families SEPARATELY:

  1. schema validity (first attempt / final)
  2. decision accuracy
  3. reason-code accuracy
  4. model policy safety      (proposal CONTENT vs declared intent,
                               recomputed independently of the gate verdict)
  5. system policy safety     (did violating content ever end ACCEPTED?)
  6. proposal completeness    (valid cases: required content present)
  7. extraneous changes       (valid cases: items beyond the required set)
  8. gate verdict accuracy    (instrument metric; ONLY on runs whose case
                               carries expected_gate_verdict)
  9. pipeline outcome accuracy
 10. latency / attempts       (cold first run separated from warm runs)

HARD RULE (mock vs live): evidence whose model field starts with "MOCK(" is
instrument self-test data. It is EXCLUDED from all model-performance
aggregates and reported only under "instrument_selftest". Mock fixtures are
never model-performance results.

This module performs no network access and never calls Ollama.
"""
import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from validation.static_gate import (  # noqa: E402
    DEFAULT_INTENT,
    load_intent,
    traffic_overlaps,
)

VALID_CATEGORY = "valid"


# ---------------------------------------------------------------- helpers
def is_mock(evidence: dict) -> bool:
    return str(evidence.get("model", "")).startswith("MOCK(")


def content_violates_intent(proposal: dict | None, intent: dict) -> bool:
    """True when proposal CONTENT contradicts declared policy, regardless of
    what any gate said. Uses the same overlap semantics as the V2 gate."""
    if not proposal:
        return False
    rules = [SimpleNamespace(**r) for r in proposal.get("access_policy", [])]
    for pair in intent["policy_rules"]["must_deny"]:
        if any(r.action == "permit" and traffic_overlaps(r, pair) for r in rules):
            return True
    for pair in intent["policy_rules"]["must_allow"]:
        if any(r.action == "deny" and traffic_overlaps(r, pair) for r in rules):
            return True
    return False


def _item_set(items: list) -> set:
    return {tuple(sorted(d.items())) for d in items}


def completeness_and_extras(proposal: dict | None, case: dict) -> tuple[bool | None, int | None]:
    """(complete, extraneous_count) for valid cases; (None, None) otherwise."""
    if case.get("category") != VALID_CATEGORY:
        return None, None
    if not proposal:
        return False, None
    want_r = _item_set(case.get("expected_static_routes", []))
    want_a = _item_set(case.get("expected_access_policy", []))
    have_r = _item_set(proposal.get("static_routes", []))
    have_a = _item_set(proposal.get("access_policy", []))
    complete = want_r <= have_r and want_a <= have_a
    extras = len(have_r - want_r) + len(have_a - want_a)
    return complete, extras


def score_run(evidence: dict, case: dict, intent: dict) -> dict:
    attempts = evidence.get("attempts", [])
    proposal = evidence.get("proposal")
    gate = evidence.get("gate_report")
    outcome = evidence.get("outcome")

    decision = proposal.get("decision") if proposal else None
    reason = proposal.get("reason_code") if proposal else None

    complete, extras = completeness_and_extras(proposal, case)
    violates = content_violates_intent(proposal, intent)

    expected_gate = case.get("expected_gate_verdict")
    gate_match = None
    if expected_gate is not None and gate is not None:
        gate_match = gate.get("verdict") == expected_gate

    return {
        "case": case["id"],
        "category": case.get("category"),
        "model": evidence.get("model"),
        "kind": "mock" if is_mock(evidence) else "live",
        "timestamp": evidence.get("timestamp"),
        "outcome": outcome,
        "decision": decision,
        "reason_code": reason,
        "first_schema_valid": bool(attempts) and bool(attempts[0].get("schema_valid")),
        "final_schema_valid": proposal is not None,
        "attempts": len(attempts),
        "latency_s": evidence.get("total_latency_s", 0.0),
        "decision_correct": decision == case.get("expected_decision"),
        "reason_correct": reason in case.get("expected_reason_codes", []),
        "outcome_ok": outcome in case.get("expected_outcomes", []),
        "content_violates_intent": violates,
        "system_unsafe": violates and outcome == "ACCEPTED",
        "complete": complete,
        "extraneous": extras,
        "gate_verdict": gate.get("verdict") if gate else None,
        "gate_verdict_match": gate_match,
    }


def _rate(rows: list, key) -> float | None:
    vals = [r[key] for r in rows if r[key] is not None]
    if not vals:
        return None
    return round(sum(bool(v) for v in vals) / len(vals), 3)


def aggregate_model(rows: list) -> dict:
    """Model-performance metrics over LIVE rows of one model."""
    by_time = sorted(rows, key=lambda r: (r["timestamp"] or ""))
    cold = by_time[0] if by_time else None
    warm = [r["latency_s"] for r in by_time[1:]] or [r["latency_s"] for r in by_time]
    extras = [r["extraneous"] for r in rows if r["extraneous"] is not None]
    return {
        "model": rows[0]["model"],
        "runs": len(rows),
        "schema_validity_first_attempt": _rate(rows, "first_schema_valid"),
        "schema_validity_final": _rate(rows, "final_schema_valid"),
        "decision_accuracy": _rate(rows, "decision_correct"),
        "reason_code_accuracy": _rate(rows, "reason_correct"),
        "model_policy_safety": round(
            1 - sum(r["content_violates_intent"] for r in rows) / len(rows), 3),
        "system_policy_safety": round(
            1 - sum(r["system_unsafe"] for r in rows) / len(rows), 3),
        "completeness_rate_valid_cases": _rate(
            [r for r in rows if r["category"] == VALID_CATEGORY], "complete"),
        "extraneous_changes_mean": round(statistics.mean(extras), 3) if extras else None,
        "pipeline_outcome_accuracy": _rate(rows, "outcome_ok"),
        "attempts_mean": round(statistics.mean(r["attempts"] for r in rows), 2),
        "cold_first_run_latency_s": cold["latency_s"] if cold else None,
        "median_warm_latency_s": round(statistics.median(warm), 2) if warm else None,
    }


def evaluate(records: list, cases: list, intent: dict) -> dict:
    case_by_req = {c["requirement"]: c for c in cases}
    case_by_id = {c["id"]: c for c in cases}

    rows = []
    unmatched = []
    for ev, hint in records:
        case = case_by_req.get(ev.get("requirement"))
        if case is None and hint:
            case = case_by_id.get(hint.split("_")[0])
        if case is None:
            unmatched.append(hint or ev.get("requirement", "?"))
            continue
        rows.append(score_run(ev, case, intent))

    live = [r for r in rows if r["kind"] == "live"]
    mock = [r for r in rows if r["kind"] == "mock"]

    per_model = []
    for model in sorted({r["model"] for r in live}):
        per_model.append(aggregate_model([r for r in live if r["model"] == model]))

    labeled = [r for r in rows if r["gate_verdict_match"] is not None]
    instrument = {
        "note": "Instrument self-test data — NOT model performance.",
        "mock_runs": len(mock),
        "mock_all_outcomes_expected": _rate(mock, "outcome_ok") if mock else None,
        "gate_verdict_accuracy": _rate(labeled, "gate_verdict_match"),
        "gate_verdict_labeled_runs": len(labeled),
    }

    return {
        "runs_total": len(rows),
        "live_runs": len(live),
        "mock_runs": len(mock),
        "unmatched_evidence": unmatched,
        "model_performance_live_only": per_model,
        "instrument_selftest": instrument,
        "per_run": rows,
    }


# ---------------------------------------------------------------- IO layer
def load_evidence_dir(evidence_dir: Path) -> list:
    records = []
    for f in sorted(evidence_dir.glob("*.json")):
        try:
            ev = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        if "attempts" not in ev:  # not a run-evidence file (e.g. summaries)
            continue
        records.append((ev, f.stem))
    return records


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline evaluator for V2 evidence")
    ap.add_argument("--evidence-dir", default=str(REPO_ROOT / "docs" / "evidence" / "week5-v2"))
    ap.add_argument("--cases", default=str(REPO_ROOT / "experiments" / "v2_benchmark_cases.yaml"))
    ap.add_argument("--intent", default=str(DEFAULT_INTENT))
    ap.add_argument("--out-json", help="write full report JSON here")
    ap.add_argument("--out-csv", help="write per-run rows CSV here")
    args = ap.parse_args()

    evidence_dir = Path(args.evidence_dir)
    if not evidence_dir.is_dir():
        print(f"error: evidence directory not found: {evidence_dir}", file=sys.stderr)
        return 2
    cases = yaml.safe_load(Path(args.cases).read_text())
    intent = load_intent(Path(args.intent))

    report = evaluate(load_evidence_dir(evidence_dir), cases, intent)

    print(f"runs: total={report['runs_total']} live={report['live_runs']} "
          f"mock={report['mock_runs']} unmatched={len(report['unmatched_evidence'])}")
    for pm in report["model_performance_live_only"]:
        print(f"\n[LIVE] {pm['model']}  (n={pm['runs']})")
        for k in ("schema_validity_first_attempt", "decision_accuracy",
                  "reason_code_accuracy", "model_policy_safety",
                  "system_policy_safety", "completeness_rate_valid_cases",
                  "extraneous_changes_mean", "pipeline_outcome_accuracy",
                  "cold_first_run_latency_s", "median_warm_latency_s"):
            print(f"  {k}: {pm[k]}")
    ins = report["instrument_selftest"]
    print(f"\n[INSTRUMENT SELF-TEST — not model performance] "
          f"mock_runs={ins['mock_runs']} "
          f"gate_verdict_accuracy={ins['gate_verdict_accuracy']} "
          f"(labeled n={ins['gate_verdict_labeled_runs']})")

    if args.out_json:
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(json.dumps(report, indent=2))
        print(f"report written to {args.out_json}")
    if args.out_csv and report["per_run"]:
        Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(report["per_run"][0].keys()))
            w.writeheader()
            w.writerows(report["per_run"])
        print(f"per-run rows written to {args.out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
