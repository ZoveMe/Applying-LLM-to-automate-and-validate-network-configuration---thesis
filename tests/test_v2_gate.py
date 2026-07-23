"""Regression tests for V2 topology and policy protections."""

import json
import unittest
from pathlib import Path

from llm.ollama_client import build_prompt
from validation.static_gate import DEFAULT_INTENT, Gate, load_intent


def proposal(*, static_routes=None, access_policy=None):
    return {
        "rationale": "V2 gate regression test.",
        "decision": "PROPOSE",
        "reason_code": "ACTIONABLE_CHANGE",
        "static_routes": static_routes or [],
        "access_policy": access_policy or [],
    }


class GateV2SafetyTests(unittest.TestCase):
    @staticmethod
    def run_gate(payload):
        intent = load_intent(Path(DEFAULT_INTENT))
        return Gate(intent).run(json.dumps(payload), "inline-v2-test")

    @staticmethod
    def failed_checks(report, category):
        return [
            check
            for check in report["checks"]
            if check["category"] == category
            and check["result"] == "FAIL"
        ]

    def test_prompt_declares_router_attachments(self):
        prompt = build_prompt("Add a required route.")

        self.assertIn(
            "r1: 10.0.1.0/24 and 10.0.12.0/30",
            prompt,
        )
        self.assertIn(
            "r2: 10.0.2.0/24, 10.0.99.0/24, and 10.0.12.0/30",
            prompt,
        )
        self.assertIn(
            "Never propose a static route to a network directly connected",
            prompt,
        )

    def test_rejects_route_to_directly_connected_network(self):
        payload = proposal(
            static_routes=[
                {
                    "node": "r2",
                    "prefix": "10.0.99.0/24",
                    "next_hop": "10.0.12.1",
                }
            ]
        )

        report = self.run_gate(payload)

        self.assertEqual(report["verdict"], "REJECT")
        failures = self.failed_checks(report, "topology")
        self.assertTrue(
            any("directly connected" in check["detail"] for check in failures)
        )

    def test_rejects_narrow_permit_inside_must_deny_pair(self):
        payload = proposal(
            access_policy=[
                {
                    "node": "r1",
                    "src": "10.0.1.128/25",
                    "dst": "10.0.99.128/25",
                    "action": "permit",
                }
            ]
        )

        report = self.run_gate(payload)

        self.assertEqual(report["verdict"], "REJECT")
        self.assertFalse(self.failed_checks(report, "topology"))
        self.assertTrue(self.failed_checks(report, "policy"))

    def test_rejects_narrow_deny_inside_must_allow_pair(self):
        payload = proposal(
            access_policy=[
                {
                    "node": "r1",
                    "src": "10.0.1.0/25",
                    "dst": "10.0.2.0/25",
                    "action": "deny",
                }
            ]
        )

        report = self.run_gate(payload)

        self.assertEqual(report["verdict"], "REJECT")
        self.assertFalse(self.failed_checks(report, "topology"))
        self.assertTrue(self.failed_checks(report, "policy"))

    def test_accepts_non_conflicting_contained_subnets(self):
        payload = proposal(
            access_policy=[
                {
                    "node": "r1",
                    "src": "10.0.1.0/25",
                    "dst": "10.0.2.0/25",
                    "action": "permit",
                }
            ]
        )

        report = self.run_gate(payload)

        self.assertEqual(
            report["verdict"],
            "PASS_PENDING_HUMAN_APPROVAL",
        )


if __name__ == "__main__":
    unittest.main()
