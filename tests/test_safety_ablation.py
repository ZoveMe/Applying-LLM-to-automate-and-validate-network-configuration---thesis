"""Regression tests for the preserved-corpus safety-layer ablation."""

from experiments.analyze_safety_ablation import (
    DEFAULT_EVIDENCE_ROOT,
    analyze_corpus,
    render_markdown,
    render_svg,
)


REPORT = analyze_corpus(DEFAULT_EVIDENCE_ROOT)
LAYERS = {layer["key"]: layer for layer in REPORT["layers"]}


def test_corpus_has_exact_registered_grain():
    quality = REPORT["quality"]
    assert quality["models"] == 12
    assert quality["cases"] == 10
    assert quality["repetitions_per_case"] == 3
    assert quality["run_records"] == 360


def test_corpus_quality_gates_are_clean():
    quality = REPORT["quality"]
    assert quality["mock_runs"] == 0
    assert quality["duplicate_candidate_keys"] == 0
    assert quality["missing_candidate_keys"] == 0
    assert quality["manifest_run_count_mismatches"] == 0
    assert quality["recorded_vs_recomputed_gate_mismatches"] == 0


def test_schema_profile_is_preserved():
    population = REPORT["population"]
    assert population["final_schema_valid"] == 349
    assert population["schema_invalid"] == 11


def test_actionable_proposal_denominator_is_explicit():
    population = REPORT["population"]
    assert population["actionable_proposals"] == 156
    assert population["non_actionable_refuse_or_clarify"] == 193


def test_no_gate_allows_eighty_known_unsafe_proposals():
    layer = LAYERS["no_deterministic_gate"]
    assert layer["allowed"] == 156
    assert layer["known_unsafe_allowed"] == 80
    assert layer["unsafe_share_of_allowed"] == 0.5128


def test_schema_only_does_not_add_semantic_safety():
    layer = LAYERS["schema_only"]
    assert layer["allowed"] == 156
    assert layer["known_unsafe_allowed"] == 80


def test_policy_only_leaves_twenty_nine_topology_violations():
    layer = LAYERS["schema_plus_policy"]
    assert layer["blocked"] == 51
    assert layer["allowed"] == 105
    assert layer["known_unsafe_allowed"] == 29


def test_complete_gate_allows_no_known_violation():
    layer = LAYERS["complete_gate"]
    assert layer["blocked"] == 80
    assert layer["allowed"] == 76
    assert layer["known_unsafe_allowed"] == 0


def test_checksum_probe_rejects_every_one_byte_mutation():
    probe = REPORT["checksum_probe"]
    assert probe["one_byte_tampering_probes"] == 76
    assert probe["tampered_proposals_accepted"] == 0
    assert probe["tampered_proposals_rejected"] == 76


def test_failure_overlap_and_rendered_report_agree():
    assert REPORT["failure_overlap"] == {
        "policy_only": 36,
        "topology_only": 29,
        "policy_and_topology": 15,
        "no_known_violation": 76,
    }
    assert len(REPORT["per_model"]) == 12
    markdown = render_markdown(REPORT)
    svg = render_svg(REPORT)
    assert "80 од 156" in markdown
    assert "29 тополошки" in markdown
    assert "Познати небезбедни предлози" in svg
