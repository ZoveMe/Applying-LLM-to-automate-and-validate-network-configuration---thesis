"""Offline tests for the V2 live experiment runner."""

import json
import tempfile
import unittest
from pathlib import Path

from experiments.run_v2_experiments import (
    load_cases,
    run_matrix,
    sanitize,
    select_cases,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = REPO_ROOT / "experiments" / "v2_benchmark_cases.yaml"


def fake_evidence(requirement, model, retries, url):
    return {
        "timestamp": "2026-07-24T12:00:00",
        "model": model,
        "ollama_url": url,
        "generation_options": {},
        "requirement": requirement,
        "attempts": [
            {
                "n": 1,
                "raw": "{}",
                "schema_valid": True,
                "error": None,
                "latency_s": 0.1,
            }
        ],
        "proposal": {
            "rationale": "Test fixture.",
            "decision": "CLARIFY",
            "reason_code": "AMBIGUOUS_REQUIREMENT",
            "static_routes": [],
            "access_policy": [],
        },
        "gate_report": None,
        "outcome": "CLARIFICATION_REQUIRED",
        "total_latency_s": 0.1,
    }


class V2LiveRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_cases(CASES_PATH)

    def test_loads_complete_unique_benchmark(self):
        self.assertEqual(len(self.cases), 10)
        self.assertEqual(len({case["id"] for case in self.cases}), 10)

    def test_case_selection_preserves_benchmark_order(self):
        selected = select_cases(self.cases, ["T10", "T2"])
        self.assertEqual([case["id"] for case in selected], ["T2", "T10"])

    def test_unknown_case_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown case"):
            select_cases(self.cases, ["T99"])

    def test_sanitize_model_name(self):
        self.assertEqual(
            sanitize("registry/model:tag name"),
            "registry_model_tag_name",
        )

    def test_matrix_writes_live_files_and_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "evidence"
            manifest = run_matrix(
                cases=[self.cases[5]],
                models=["offline-test-model"],
                repetitions=2,
                retries=3,
                url="http://unused.invalid",
                out_dir=out_dir,
                pipeline=fake_evidence,
            )

            run_files = sorted(out_dir.glob("T6_*.json"))
            self.assertEqual(len(run_files), 2)
            self.assertEqual(manifest["runs"], 2)
            self.assertEqual(manifest["kind"], "LIVE_OLLAMA_EXPERIMENT")

            evidence = json.loads(run_files[0].read_text())
            self.assertEqual(evidence["benchmark_case_id"], "T6")
            self.assertFalse(evidence["model"].startswith("MOCK("))

            saved_manifest = json.loads(
                (out_dir / "manifest.json").read_text()
            )
            self.assertEqual(saved_manifest["runs"], 2)

    def test_existing_evidence_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "evidence"
            out_dir.mkdir()
            marker = out_dir / "keep.txt"
            marker.write_text("preserve me")

            with self.assertRaisesRegex(FileExistsError, "not empty"):
                run_matrix(
                    cases=[self.cases[5]],
                    models=["offline-test-model"],
                    repetitions=1,
                    retries=1,
                    url="http://unused.invalid",
                    out_dir=out_dir,
                    pipeline=fake_evidence,
                )

            self.assertEqual(marker.read_text(), "preserve me")

    def test_mock_evidence_is_rejected(self):
        def fake_mock(requirement, model, retries, url):
            evidence = fake_evidence(requirement, model, retries, url)
            evidence["model"] = f"MOCK({model})"
            return evidence

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "mock evidence"):
                run_matrix(
                    cases=[self.cases[5]],
                    models=["offline-test-model"],
                    repetitions=1,
                    retries=1,
                    url="http://unused.invalid",
                    out_dir=Path(temporary) / "evidence",
                    pipeline=fake_mock,
                )


if __name__ == "__main__":
    unittest.main()
