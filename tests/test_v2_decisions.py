"""Regression tests for the V2 decision schema and LLM pipeline."""

import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from llm.ollama_client import build_prompt, run_pipeline
from validation.schema import ConfigSuggestion
from validation.static_gate import DEFAULT_INTENT, Gate, load_intent


REPO_ROOT = Path(__file__).resolve().parents[1]

VALID_ROUTE = {
    "node": "r1",
    "prefix": "10.0.2.0/24",
    "next_hop": "10.0.12.2",
}

FORBIDDEN_PERMIT = {
    "node": "r1",
    "src": "10.0.1.0/24",
    "dst": "10.0.99.0/24",
    "action": "permit",
}


def suggestion(
    decision,
    reason_code,
    *,
    static_routes=None,
    access_policy=None,
    **extra,
):
    return {
        "rationale": f"Test {decision} decision.",
        "decision": decision,
        "reason_code": reason_code,
        "static_routes": static_routes or [],
        "access_policy": access_policy or [],
        **extra,
    }


class SchemaInvariantTests(unittest.TestCase):
    def test_valid_decisions(self):
        cases = [
            suggestion(
                "PROPOSE",
                "ACTIONABLE_CHANGE",
                static_routes=[VALID_ROUTE],
            ),
            suggestion("CLARIFY", "AMBIGUOUS_REQUIREMENT"),
            suggestion("REFUSE", "POLICY_CONFLICT"),
            suggestion("REFUSE", "OUT_OF_SCOPE"),
            suggestion("REFUSE", "INVALID_INVENTORY"),
            suggestion("REFUSE", "UNSAFE_OPERATION"),
        ]

        for case in cases:
            with self.subTest(decision=case["decision"]):
                parsed = ConfigSuggestion.model_validate(case)
                self.assertEqual(parsed.decision, case["decision"])

    def test_invalid_decision_payloads(self):
        cases = [
            suggestion("PROPOSE", "ACTIONABLE_CHANGE"),
            suggestion(
                "PROPOSE",
                "POLICY_CONFLICT",
                static_routes=[VALID_ROUTE],
            ),
            suggestion(
                "CLARIFY",
                "AMBIGUOUS_REQUIREMENT",
                static_routes=[VALID_ROUTE],
            ),
            suggestion("CLARIFY", "ACTIONABLE_CHANGE"),
            suggestion(
                "REFUSE",
                "POLICY_CONFLICT",
                access_policy=[FORBIDDEN_PERMIT],
            ),
            suggestion("REFUSE", "ACTIONABLE_CHANGE"),
            suggestion(
                "PROPOSE",
                "ACTIONABLE_CHANGE",
                static_routes=[VALID_ROUTE],
                invented_field=True,
            ),
        ]

        for number, case in enumerate(cases, start=1):
            with self.subTest(case=number):
                with self.assertRaises(ValidationError):
                    ConfigSuggestion.model_validate(case)


class PromptAndPipelineTests(unittest.TestCase):
    def test_prompt_is_fully_rendered(self):
        prompt = build_prompt("Make the network faster.")

        self.assertNotIn("{{SCHEMA}}", prompt)
        self.assertNotIn("{{REQUIREMENT}}", prompt)
        self.assertIn('"decision"', prompt)
        self.assertIn('"reason_code"', prompt)
        self.assertIn("PROPOSE", prompt)
        self.assertIn("CLARIFY", prompt)
        self.assertIn("REFUSE", prompt)
        self.assertIn("Make the network faster.", prompt)
        self.assertIn(
            "10.0.1.0/24 to management 10.0.99.0/24 MUST be denied",
            prompt,
        )

    def test_decisions_receive_distinct_outcomes(self):
        cases = [
            (
                suggestion("CLARIFY", "AMBIGUOUS_REQUIREMENT"),
                "CLARIFICATION_REQUIRED",
                False,
            ),
            (
                suggestion("REFUSE", "POLICY_CONFLICT"),
                "REFUSED",
                False,
            ),
            (
                suggestion(
                    "PROPOSE",
                    "ACTIONABLE_CHANGE",
                    static_routes=[VALID_ROUTE],
                ),
                "ACCEPTED",
                True,
            ),
        ]

        for response, expected_outcome, expects_gate in cases:
            with self.subTest(outcome=expected_outcome):
                evidence = run_pipeline(
                    "Offline regression test.",
                    mock_raw=json.dumps(response),
                )

                self.assertEqual(evidence["outcome"], expected_outcome)
                self.assertEqual(
                    evidence["gate_report"] is not None,
                    expects_gate,
                )

    def test_forbidden_live_proposal_is_rejected_by_gate(self):
        response = suggestion(
            "PROPOSE",
            "ACTIONABLE_CHANGE",
            access_policy=[FORBIDDEN_PERMIT],
        )

        evidence = run_pipeline(
            "Allow clients to access management.",
            mock_raw=json.dumps(response),
        )

        self.assertEqual(evidence["outcome"], "REJECTED_GATE")
        self.assertEqual(evidence["gate_report"]["verdict"], "REJECT")

        failed_categories = {
            check["category"]
            for check in evidence["gate_report"]["checks"]
            if check["result"] == "FAIL"
        }
        self.assertIn("policy", failed_categories)


class ExampleRegressionTests(unittest.TestCase):
    def test_example_verdicts(self):
        expected = {
            "suggestion_good.json": "PASS_PENDING_HUMAN_APPROVAL",
            "suggestion_bad_route.json": "REJECT",
            "suggestion_bad_policy.json": "REJECT",
            "suggestion_bad_schema.json": "REJECT",
        }

        intent = load_intent(Path(DEFAULT_INTENT))

        for filename, expected_verdict in expected.items():
            with self.subTest(example=filename):
                path = REPO_ROOT / "examples" / filename
                report = Gate(intent).run(path.read_text(), str(path))
                self.assertEqual(report["verdict"], expected_verdict)


if __name__ == "__main__":
    unittest.main()