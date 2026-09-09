"""Tests for the apply-time proof: state snapshot diffing.

The diff is what turns "the command returned 0" into "the router actually
changed" — the distinction the thesis makes between automation success and
network correctness.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments"))
sys.path.insert(0, str(REPO_ROOT))

from live_llm_dashboard import state_diff  # noqa: E402


def test_added_route_detected():
    before = ["route 10.0.2.0/24 [1/0] via"]
    after = before + ["route 10.0.99.0/24 [1/0] via"]
    result = state_diff(before, after)
    assert result["added"] == ["route 10.0.99.0/24 [1/0] via"]
    assert result["removed"] == []


def test_removed_rule_detected():
    before = ["rule -A FORWARD -s 10.0.1.0/24 -d 10.0.99.0/24 -j DROP"]
    result = state_diff(before, [])
    assert len(result["removed"]) == 1
    assert result["added"] == []


def test_no_change_reports_nothing():
    same = ["route A", "rule B"]
    result = state_diff(same, list(same))
    assert result["added"] == []
    assert result["removed"] == []


def test_simultaneous_add_and_remove():
    result = state_diff(["route A", "rule X"], ["route A", "route B"])
    assert result["added"] == ["route B"]
    assert result["removed"] == ["rule X"]


def test_duplicate_lines_counted_not_deduplicated_away():
    """Four identical DROP rules is a real state; adding a fifth is a change."""
    before = ["rule DROP"] * 4
    after = ["rule DROP"] * 5
    assert state_diff(before, after)["added"] == ["rule DROP"]


def test_output_is_deduplicated_for_display():
    before = []
    after = ["rule DROP", "rule DROP", "rule DROP"]
    # three added, but shown once
    assert state_diff(before, after)["added"] == ["rule DROP"]


def test_dashboard_exposes_analysis_and_proof_helpers():
    import live_llm_dashboard as dash
    assert hasattr(dash, "device_snapshot")
    assert hasattr(dash, "state_diff")
    assert hasattr(dash.Handler, "handle_analyze")
    for token in ("applyLogHTML", "analyzeTopology", "/api/analyze",
                  "потврдена промена на состојбата"):
        assert token in dash.PAGE, token
