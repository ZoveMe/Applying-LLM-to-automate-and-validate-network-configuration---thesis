"""Offline tests for the cross-task consistency analysis.

Synthetic fixtures only — no evidence, no model, no network.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments"))

from analyze_cross_task import load_explain, load_v2, pearson  # noqa: E402


def write_explain_suite(root: Path, suite: str, data: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{suite}-evaluation.json").write_text(
        json.dumps({"aggregate": data})
    )


def test_pearson_perfect_positive():
    assert pearson([0.0, 0.5, 1.0], [0.0, 0.5, 1.0]) == 1.0


def test_pearson_perfect_negative():
    assert pearson([0.0, 0.5, 1.0], [1.0, 0.5, 0.0]) == -1.0


def test_pearson_undefined_when_constant():
    assert pearson([1.0, 1.0, 1.0], [0.2, 0.5, 0.9]) is None


def test_pearson_undefined_when_too_few_points():
    assert pearson([0.0, 1.0], [0.0, 1.0]) is None


def test_load_explain_averages_across_suites(tmp_path):
    write_explain_suite(tmp_path, "small", {
        "m1": {"mean_policy_recall": 1.0},
        "m2": {"mean_policy_recall": 0.0},
    })
    write_explain_suite(tmp_path, "l-clean", {
        "m1": {"mean_policy_recall": 0.5},
        "m2": {"mean_policy_recall": 0.0},
    })
    result = load_explain(tmp_path)
    assert result["m1"] == 0.75
    assert result["m2"] == 0.0


def test_load_explain_skips_null_recall(tmp_path):
    """Devices without deny rules report null; those must not become zeros."""
    write_explain_suite(tmp_path, "small", {"m1": {"mean_policy_recall": None}})
    write_explain_suite(tmp_path, "l-clean", {"m1": {"mean_policy_recall": 1.0}})
    assert load_explain(tmp_path)["m1"] == 1.0


def test_load_explain_tolerates_missing_suite(tmp_path):
    write_explain_suite(tmp_path, "small", {"m1": {"mean_policy_recall": 1.0}})
    assert load_explain(tmp_path) == {"m1": 1.0}


def test_load_v2_extracts_per_model_metrics(tmp_path):
    path = tmp_path / "v2.json"
    path.write_text(json.dumps({
        "model_performance_live_only": [
            {
                "model": "m1",
                "model_policy_safety": 0.9,
                "system_policy_safety": 1.0,
                "decision_accuracy": 0.8,
                "schema_validity_first_attempt": 1.0,
                "runs": 30,
            }
        ]
    }))
    result = load_v2(path)
    assert result["m1"]["system_policy_safety"] == 1.0
    assert result["m1"]["runs"] == 30
