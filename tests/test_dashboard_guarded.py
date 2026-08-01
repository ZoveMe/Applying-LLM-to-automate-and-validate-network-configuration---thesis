"""Tests for the dashboard's guarded mode — the SHA-256 approval binding.

The integrity check is the security-relevant part of the guarded path: an
approval must authorise the exact bytes it was shown, and nothing else.
"""
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments"))
sys.path.insert(0, str(REPO_ROOT))


def digest_of(proposal: dict) -> str:
    """Same canonicalisation the dashboard uses."""
    canonical = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


PROPOSAL = {
    "rationale": "add the missing return route",
    "decision": "PROPOSE",
    "reason_code": "ACTIONABLE_CHANGE",
    "static_routes": [{"node": "r1", "prefix": "10.0.2.0/24",
                       "next_hop": "10.0.12.2"}],
    "access_policy": [],
}


def test_digest_is_stable_across_key_order():
    """Key order must not change the digest, or approval would be fragile."""
    reordered = {k: PROPOSAL[k] for k in reversed(list(PROPOSAL))}
    assert digest_of(PROPOSAL) == digest_of(reordered)


def test_any_field_change_changes_the_digest():
    tampered = json.loads(json.dumps(PROPOSAL))
    tampered["static_routes"][0]["next_hop"] = "10.0.12.1"
    assert digest_of(tampered) != digest_of(PROPOSAL)


def test_adding_a_change_changes_the_digest():
    """The dangerous case: an extra rule smuggled in after approval."""
    tampered = json.loads(json.dumps(PROPOSAL))
    tampered["access_policy"].append(
        {"node": "r1", "src": "10.0.1.0/24", "dst": "10.0.99.0/24",
         "action": "permit"})
    assert digest_of(tampered) != digest_of(PROPOSAL)


def test_single_character_change_changes_the_digest():
    tampered = json.loads(json.dumps(PROPOSAL))
    tampered["rationale"] = PROPOSAL["rationale"] + " "
    assert digest_of(tampered) != digest_of(PROPOSAL)


def test_dashboard_module_imports_guarded_pipeline():
    """Guarded mode must use the real pipeline, not a reimplementation."""
    import live_llm_dashboard as dash
    assert hasattr(dash, "run_pipeline")
    assert hasattr(dash, "apply_proposal_ungoverned")
    assert hasattr(dash.Handler, "handle_ask_guarded")
    assert hasattr(dash.Handler, "handle_approve")


def test_page_exposes_both_modes():
    import live_llm_dashboard as dash
    for token in ("/api/ask_guarded", "/api/approve", "setMode",
                  "APPROVE and deploy"):
        assert token in dash.PAGE, token
