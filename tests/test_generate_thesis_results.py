"""Tests for deterministic thesis-result generation from preserved evidence."""

import copy
import unittest

from experiments.generate_thesis_results import (
    DEFAULT_CHAPTER,
    DEFAULT_FIGURE_DIR,
    ResultsError,
    build_outputs,
    load_sources,
    validate_sources,
)


class TestGenerateThesisResults(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sources = load_sources()

    def test_preserved_sources_pass_consistency_checks(self):
        validate_sources(self.sources)

    def test_expected_deterministic_outputs_are_built(self):
        first = build_outputs(self.sources)
        second = build_outputs(self.sources)

        self.assertEqual(first, second)
        self.assertEqual(
            set(first),
            {
                DEFAULT_CHAPTER,
                DEFAULT_FIGURE_DIR / "model-quality.svg",
                DEFAULT_FIGURE_DIR / "latency.svg",
            },
        )

    def test_chapter_contains_key_results_and_safety_claim(self):
        chapter = build_outputs(self.sources)[DEFAULT_CHAPTER]

        self.assertIn("60 live Ollama runs", chapter)
        self.assertIn("| Decision accuracy | 90% | 80% |", chapter)
        self.assertIn("| Whole-system safety | 100% | 100% |", chapter)
        self.assertIn("9/10 for Qwen2.5-Coder and 8/10 for Qwen3", chapter)
        self.assertRegex(chapter, r"all `7` of `7`\s+checks passed")
        self.assertIn("automatic network changes", chapter)

    def test_figures_are_accessible_svg_with_exact_values(self):
        outputs = build_outputs(self.sources)
        quality = outputs[DEFAULT_FIGURE_DIR / "model-quality.svg"]
        latency = outputs[DEFAULT_FIGURE_DIR / "latency.svg"]

        self.assertIn('role="img"', quality)
        self.assertIn("<title", quality)
        self.assertIn("100.0%", quality)
        self.assertIn("66.7%", quality)
        self.assertIn("10.4 s", latency)
        self.assertIn("1.2 s", latency)

    def test_mock_runs_mixed_into_metrics_are_rejected(self):
        altered = copy.deepcopy(self.sources)
        altered.evaluation["mock_runs"] = 1

        with self.assertRaisesRegex(ResultsError, "Mock|mock"):
            validate_sources(altered)

    def test_missing_repetition_is_rejected(self):
        altered = copy.deepcopy(self.sources)
        altered.evaluation["per_run"].pop()

        with self.assertRaisesRegex(ResultsError, "60 per-run rows"):
            validate_sources(altered)

    def test_approval_checksum_mismatch_is_rejected(self):
        altered = copy.deepcopy(self.sources)
        altered.approval["proposal_sha256"] = "0" * 64

        with self.assertRaisesRegex(ResultsError, "checksum"):
            validate_sources(altered)

    def test_non_matching_post_repair_state_is_rejected(self):
        altered = copy.deepcopy(self.sources)
        altered.after["summary"] = {"passed": 6, "failed": 1}

        with self.assertRaisesRegex(ResultsError, "7/7"):
            validate_sources(altered)


if __name__ == "__main__":
    unittest.main()
