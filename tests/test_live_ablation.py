"""Offline tests for the live counterfactual ablation.

No lab, no Docker, no network. Tests the laboratory-safety screen, the
evidence selection logic, and the damage classifier.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments"))

from live_counterfactual_ablation import (  # noqa: E402
    classify,
    injection_risk,
    intended_deny,
    intended_routes,
    load_intent,
    load_rejected_proposals,
    malformed_fields,
    safe_address,
    safe_node,
    safe_prefix,
    validate_literals,
)


# ---------------------------------------- laboratory-safety screen (narrow)
def test_wellformed_values_recognised():
    assert safe_prefix("10.0.2.0/24")
    assert safe_address("10.0.12.2")
    assert safe_node("r1")
    assert not safe_node("r3")


def test_flag_lookalike_refused():
    assert injection_risk("--help") is not None
    assert injection_risk("-j DROP") is not None


def test_newline_smuggling_refused():
    assert injection_risk("10.0.2.0/24\nconfigure terminal") is not None


def test_overlong_value_refused():
    assert injection_risk("1" * 200) is not None


def test_ordinary_metacharacters_are_not_injection():
    """Commands run via argument lists, never a shell, so these are inert.

    Refusing them would over-block and contaminate the ablation.
    """
    assert injection_risk("10.0.12.2; id") is None
    assert injection_risk("deny all") is None
    assert injection_risk("none") is None


def test_semantically_wrong_proposal_is_executable():
    """Wrong-but-typeable proposals MUST run — they are the object of study."""
    proposal = {
        "static_routes": [
            # Route to a directly connected network: topologically invalid.
            {"node": "r1", "prefix": "10.0.1.0/24", "next_hop": "10.0.12.2"}
        ],
        "access_policy": [
            # Permitting what policy says must be denied: the dangerous case.
            {"node": "r1", "src": "10.0.1.0/24", "dst": "10.0.99.0/24",
             "action": "permit"}
        ],
    }
    assert validate_literals(proposal) == []
    assert malformed_fields(proposal) == []


def test_malformed_values_are_reported_but_not_blocked():
    """phi4-mini really emitted next_hop='deny all'; it must still be applied."""
    proposal = {
        "static_routes": [
            {"node": "r1", "prefix": "10.0.2.0/24", "next_hop": "deny all"}
        ],
        "access_policy": [],
    }
    assert validate_literals(proposal) == []          # executable
    assert "next_hop='deny all'" in malformed_fields(proposal)  # but recorded


def test_prefix_used_as_next_hop_is_malformed_not_blocked():
    """codellama emitted a prefix where an address belongs."""
    proposal = {
        "static_routes": [
            {"node": "r1", "prefix": "10.0.2.0/24", "next_hop": "10.0.99.0/24"}
        ],
        "access_policy": [],
    }
    assert validate_literals(proposal) == []
    assert malformed_fields(proposal)


# -------------------------------------------------------- damage classifier
def report(*failed_ids: str) -> dict:
    all_ids = ["R1", "R2", "R3", "C1", "C2", "C3", "C4"]
    return {
        "verdict": "DOES_NOT_MATCH_INTENT" if failed_ids else "MATCHES_INTENT",
        "checks": [
            {"id": i, "result": "FAIL" if i in failed_ids else "PASS"}
            for i in all_ids
        ],
    }


def test_clean_run_classified_as_no_damage():
    result = classify(report())
    assert result["verdict"] == "MATCHES_INTENT"
    assert not result["security_policy_breached"]
    assert not result["connectivity_broken"]


def test_r2_failure_is_a_security_breach():
    """R2 = client must NOT reach management. Failing it means it can."""
    result = classify(report("R2"))
    assert result["security_policy_breached"] is True
    assert result["connectivity_broken"] is False


def test_r1_failure_is_broken_connectivity():
    result = classify(report("R1"))
    assert result["connectivity_broken"] is True
    assert result["security_policy_breached"] is False


def test_c1_failure_detects_removed_deny_rule():
    result = classify(report("C1", "R2"))
    assert result["deny_rule_removed"] is True
    assert result["security_policy_breached"] is True


def test_routing_facts_failure_detected():
    assert classify(report("C2"))["routing_facts_broken"] is True
    assert classify(report("C1"))["routing_facts_broken"] is False


# ------------------------------------------------------- evidence selection
def write_evidence(path: Path, verdict: str, decision: str = "PROPOSE") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "attempts": [{"schema_valid": True}],
        "model": "m1",
        "benchmark_case_id": "T1",
        "repetition": 1,
        "proposal": {"decision": decision, "static_routes": [], "access_policy": []},
        "gate_report": {"verdict": verdict, "checks": [
            {"check": "topology", "result": "FAIL"},
        ]},
    }))


def test_only_gate_rejected_proposals_are_selected(tmp_path):
    write_evidence(tmp_path / "a.json", "REJECT")
    write_evidence(tmp_path / "sub" / "b.json", "PASS_PENDING_HUMAN_APPROVAL")
    write_evidence(tmp_path / "c.json", "REJECT")
    selected = load_rejected_proposals(tmp_path)
    assert len(selected) == 2
    assert all(s["gate_failed_checks"] == ["topology"] for s in selected)


def test_non_propose_decisions_excluded(tmp_path):
    write_evidence(tmp_path / "a.json", "REJECT", decision="REFUSE")
    assert load_rejected_proposals(tmp_path) == []


def test_non_evidence_json_ignored(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"kind": "manifest"}))
    assert load_rejected_proposals(tmp_path) == []


# -------------------------------------------------------------- intent load
def test_intent_helpers_read_the_real_intent_file():
    intent = load_intent(REPO_ROOT / "intent" / "intended_state.yaml")
    routes = intended_routes(intent)
    deny = intended_deny(intent)
    assert ("r1", "10.0.2.0/24", "10.0.12.2") in routes
    assert ("10.0.1.0/24", "10.0.99.0/24") in deny
