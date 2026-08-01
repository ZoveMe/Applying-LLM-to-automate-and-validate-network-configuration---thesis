"""Offline tests for the EXPLAIN task: schema strictness + factuality scoring.

No model, no network, no lab required — mirrors the existing test-suite design.
"""
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "validation"))
sys.path.insert(0, str(REPO_ROOT / "experiments"))

from explain_schema import ConfigExplanation  # noqa: E402
from evaluate_explain import ground_truth, score_run  # noqa: E402

GOOD_R1 = json.loads((REPO_ROOT / "examples" / "explanation_r1_good.json").read_text())


def make_record(parsed: dict, device: str = "r1") -> dict:
    return {
        "device": device,
        "model": "test-model",
        "rep": 1,
        "schema_valid": True,
        "parsed": parsed,
    }


def test_good_example_passes_schema():
    ConfigExplanation.model_validate(GOOD_R1)


def test_unknown_field_rejected():
    bad = dict(GOOD_R1)
    bad["comment"] = "extra field"
    with pytest.raises(ValidationError):
        ConfigExplanation.model_validate(bad)


def test_perfect_r1_scores_full_marks():
    result = score_run(make_record(GOOD_R1), ground_truth())
    assert result["route_recall"] == 1.0
    assert result["route_precision"] == 1.0
    assert result["iface_recall"] == 1.0
    assert result["iface_precision"] == 1.0
    assert result["policy_recall"] == 1.0
    assert result["hallucinations"] == []


def test_hallucinated_route_detected():
    parsed = json.loads(json.dumps(GOOD_R1))
    parsed["static_routes"].append(
        {"prefix": "192.168.50.0/24", "next_hop": "10.0.12.2", "purpose": "invented"}
    )
    result = score_run(make_record(parsed), ground_truth())
    assert "192.168.50.0/24" in result["hallucinations"]
    assert result["route_precision"] < 1.0


def test_wrong_device_scores_zero_recall():
    result = score_run(make_record(GOOD_R1, device="r2"), ground_truth())
    assert result["route_recall"] == 0.0
    assert result["policy_recall"] is None  # r2 has no local deny rule


def test_phantom_policy_claim_detected():
    """A deny rule that does not exist on the device must be counted."""
    parsed = json.loads(json.dumps(GOOD_R1))
    parsed["access_rules"].append(
        {"src": "10.0.2.0/24", "dst": "10.0.4.0/24", "action": "deny",
         "purpose": "fabricated control"}
    )
    result = score_run(make_record(parsed), ground_truth())
    assert result["phantom_policy_claims"] == 1
    assert result["policy_recall"] == 1.0  # the real rule was still found


def test_phantom_policy_counted_on_device_without_deny_rules():
    """r2 has no deny rules: policy_recall is undefined, phantom still counts.

    This is the l-faulty r3 case — the metric gap this check closes.
    """
    result = score_run(make_record(GOOD_R1, device="r2"), ground_truth())
    assert result["policy_recall"] is None
    assert result["phantom_policy_claims"] == 1


def test_no_phantom_claims_on_correct_explanation():
    result = score_run(make_record(GOOD_R1), ground_truth())
    assert result["phantom_policy_claims"] == 0


def test_schema_invalid_run_scores_zero():
    record = make_record(GOOD_R1)
    record["schema_valid"] = False
    result = score_run(record, ground_truth())
    assert result["route_recall"] == 0.0
    assert result["schema_valid"] is False
