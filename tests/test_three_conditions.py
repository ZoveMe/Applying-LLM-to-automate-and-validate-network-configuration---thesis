"""Offline tests for the three-condition comparison."""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments"))

from analyze_three_conditions import (  # noqa: E402
    freeform_condition,
    guarded_condition,
    rate,
    structured_condition,
)


def test_rate_formats_percentage():
    assert rate(6, 80) == "6/80 (8%)"
    assert rate(0, 30) == "0/30 (0%)"


def test_rate_handles_missing_data():
    assert rate(None, 10) == "n/a"
    assert rate(5, 0) == "n/a"


def test_missing_evidence_returns_none(tmp_path):
    assert guarded_condition(tmp_path / "absent.csv") is None
    assert structured_condition(tmp_path / "absent.json") is None
    assert freeform_condition(tmp_path / "absent.json") is None


def test_guarded_counts_only_accepted_unsafe(tmp_path):
    path = tmp_path / "eval.csv"
    path.write_text(
        "kind,outcome,content_violates_intent\n"
        "live,ACCEPTED,False\n"
        "live,REJECTED_GATE,True\n"       # unsafe but never deployed
        "live,REFUSED,False\n"
        "mock,ACCEPTED,True\n"            # mock rows must be ignored
    )
    result = guarded_condition(path)
    assert result["runs"] == 3
    assert result["changes_that_reached_deployment"] == 1
    assert result["unsafe_changes_deployed"] == 0
    assert result["policy_breaches"] == 0


def test_structured_reads_summary(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({
        "proposals_replayed": 80,
        "left_network_not_matching_intent": 32,
        "security_policy_breached": 6,
        "deny_rule_removed": 8,
        "connectivity_broken": 24,
    }))
    result = structured_condition(path)
    assert result["runs"] == 80
    assert result["policy_breaches"] == 6


def test_freeform_flags_partial_campaign(tmp_path):
    path = tmp_path / "f.json"
    path.write_text(json.dumps({
        "runs_with_outcome": 30,
        "left_network_not_matching_intent": 10,
        "security_policy_breached": 9,
    }))
    result = freeform_condition(path)
    assert result["runs"] == 30
    assert result["complete"] is False


def test_freeform_marks_complete_campaign(tmp_path):
    path = tmp_path / "f.json"
    path.write_text(json.dumps({
        "runs_with_outcome": 120,
        "left_network_not_matching_intent": 40,
        "security_policy_breached": 30,
    }))
    assert freeform_condition(path)["complete"] is True
