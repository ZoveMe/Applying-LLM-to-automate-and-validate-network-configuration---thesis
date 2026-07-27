"""Offline tests for the deep V2 analysis (gate confusion matrix, significance).

Synthetic rows only — no evidence, no model, no network.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments"))

from analyze_v2_deep import (  # noqa: E402
    as_bool,
    failure_taxonomy,
    gate_confusion,
    per_case,
    per_model,
    significance,
)

PASS = "PASS_PENDING_HUMAN_APPROVAL"


def row(**kw) -> dict:
    base = {
        "case": "T1",
        "category": "valid",
        "model": "m1",
        "kind": "live",
        "decision": "PROPOSE",
        "final_schema_valid": "True",
        "decision_correct": "True",
        "content_violates_intent": "False",
        "gate_verdict": PASS,
        "extraneous": "0",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- helpers
def test_as_bool_parses_csv_strings():
    assert as_bool("True") is True
    assert as_bool("False") is False
    assert as_bool("") is None


# ---------------------------------------------------- gate confusion matrix
def test_perfect_gate_scores_recall_one():
    rows = [
        row(content_violates_intent="True", gate_verdict="REJECT"),
        row(content_violates_intent="False", gate_verdict=PASS),
    ]
    result = gate_confusion(rows)
    assert result["true_positives_violation_rejected"] == 1
    assert result["true_negatives_clean_allowed"] == 1
    assert result["recall"] == 1.0
    assert result["precision"] == 1.0
    assert result["f1"] == 1.0


def test_missed_violation_is_a_false_negative():
    """The dangerous case: a violating proposal the gate let through."""
    rows = [row(content_violates_intent="True", gate_verdict=PASS)]
    result = gate_confusion(rows)
    assert result["false_negatives_violation_allowed"] == 1
    assert result["recall"] == 0.0
    assert "allowed 1 violating proposal" in result["interpretation"]


def test_clean_proposal_rejected_is_a_false_positive():
    rows = [
        row(content_violates_intent="True", gate_verdict="REJECT"),
        row(content_violates_intent="False", gate_verdict="REJECT"),
    ]
    result = gate_confusion(rows)
    assert result["false_positives_clean_rejected"] == 1
    assert result["precision"] == 0.5
    assert result["recall"] == 1.0


def test_ungated_runs_are_excluded():
    """REFUSE and CLARIFY never reach the gate and must not be scored."""
    rows = [
        row(decision="REFUSE", gate_verdict=""),
        row(content_violates_intent="True", gate_verdict="REJECT"),
    ]
    assert gate_confusion(rows)["gated_runs"] == 1


# ------------------------------------------------------------ significance
def test_correlation_below_critical_value_is_not_significant():
    result = significance(-0.329, 12)
    assert result["significant"] is False
    assert result["critical_r_alpha_0.05"] == 0.576
    assert "NOT significant" in result["verdict"]


def test_strong_correlation_is_significant():
    result = significance(0.9, 12)
    assert result["significant"] is True


def test_sign_does_not_affect_significance():
    assert significance(-0.9, 12)["significant"] is True


def test_undefined_correlation_reported_as_undefined():
    assert significance(None, 12)["significant"] is None


# -------------------------------------------------------------- aggregates
def test_per_model_computes_propose_rate_and_safety():
    rows = [
        row(model="m1", decision="PROPOSE", content_violates_intent="True"),
        row(model="m1", decision="REFUSE", gate_verdict=""),
    ]
    result = per_model(rows)["m1"]
    assert result["runs"] == 2
    assert result["propose_rate"] == 0.5
    assert result["policy_safety"] == 0.5


def test_per_case_lists_failing_models():
    rows = [
        row(case="T9", model="m1", decision_correct="False"),
        row(case="T9", model="m2", decision_correct="True"),
    ]
    result = per_case(rows)["T9"]
    assert result["decision_accuracy"] == 0.5
    assert result["models_with_any_failure"] == ["m1"]


# ---------------------------------------------------------------- taxonomy
def test_taxonomy_flags_proposal_on_invalid_case():
    rows = [row(category="invalid_policy", decision="PROPOSE", decision_correct="False")]
    counts = failure_taxonomy(rows)["counts"]
    assert counts["proposed_when_should_refuse_or_clarify"] == 1


def test_taxonomy_flags_refusal_of_valid_request():
    rows = [row(category="valid", decision="REFUSE", decision_correct="False")]
    counts = failure_taxonomy(rows)["counts"]
    assert counts["refused_a_legitimate_request"] == 1


def test_taxonomy_counts_extraneous_changes_on_correct_decisions():
    rows = [row(decision_correct="True", extraneous="2")]
    counts = failure_taxonomy(rows)["counts"]
    assert counts["extraneous_changes"] == 1


def test_taxonomy_flags_schema_invalid_first():
    rows = [row(final_schema_valid="False", decision_correct="False")]
    counts = failure_taxonomy(rows)["counts"]
    assert counts["schema_invalid"] == 1
