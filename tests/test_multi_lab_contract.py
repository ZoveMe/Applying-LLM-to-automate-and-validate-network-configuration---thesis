"""The proposal contract and the gate, on a laboratory other than the original.

Two things have to hold at once. The frozen two-router path must behave exactly
as it did when the 360 preserved runs were produced, and a larger laboratory
must be constrained just as tightly rather than more loosely. A contract that
quietly widened to accept any device name would turn a schema failure into a
silent pass, and the reported count of schema-invalid outputs would no longer
mean what the thesis says it means.
"""
import json
import sys
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "validation"))

from schema import (ConfigSuggestion, devices_in_intent,  # noqa: E402
                    suggestion_model_for)
from static_gate import (Gate, load_intent, transit_links,  # noqa: E402
                         valid_next_hops)

CORE = REPO / "intent" / "intended_state.yaml"
ENT = REPO / "labs" / "topology-ent" / "intent_ent.yaml"


def proposal(routes=(), policy=()):
    return json.dumps({
        "decision": "PROPOSE", "reason_code": "ACTIONABLE_CHANGE",
        "rationale": "test", "static_routes": list(routes),
        "access_policy": list(policy),
    })


class TransitShapes(unittest.TestCase):
    """Both intent shapes must be understood; neither may yield nothing."""

    def test_single_transit_shape(self):
        links = transit_links(yaml.safe_load(CORE.read_text(encoding="utf-8")))
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0][1], {"r1": "10.0.12.1", "r2": "10.0.12.2"})

    def test_list_transit_shape(self):
        links = transit_links(yaml.safe_load(ENT.read_text(encoding="utf-8")))
        self.assertEqual(len(links), 6)

    def test_intent_without_transit_is_refused(self):
        """Silently having no next hops would reject every route instead."""
        with self.assertRaises(ValueError):
            transit_links({"segments": {}})

    def test_next_hops_are_only_direct_neighbours(self):
        hops = valid_next_hops(yaml.safe_load(ENT.read_text(encoding="utf-8")))
        # fw-dmz sits on one transit link, so it has exactly one legal next hop
        self.assertEqual(hops["fw-dmz"], {"10.0.0.21"})
        self.assertNotIn("10.0.0.6", hops["fw-dmz"])


class ContractStaysClosed(unittest.TestCase):
    def test_core_contract_is_unchanged(self):
        derived = suggestion_model_for(devices_in_intent(
            yaml.safe_load(CORE.read_text(encoding="utf-8"))))
        frozen = ConfigSuggestion.model_json_schema()
        self.assertEqual(
            derived.model_json_schema()["$defs"]["StaticRoute"]["properties"]["node"],
            frozen["$defs"]["StaticRoute"]["properties"]["node"])

    def test_enterprise_contract_lists_its_own_devices(self):
        model = suggestion_model_for(devices_in_intent(
            yaml.safe_load(ENT.read_text(encoding="utf-8"))))
        enum = model.model_json_schema()["$defs"]["StaticRoute"]["properties"]["node"]["enum"]
        self.assertEqual(sorted(enum),
                         ["core1", "core2", "edge-br", "edge-hq", "fw-dmz"])

    def test_device_of_another_lab_is_a_schema_failure(self):
        """r1 does not exist here; it must fail at the schema, not later."""
        model = suggestion_model_for(devices_in_intent(
            yaml.safe_load(ENT.read_text(encoding="utf-8"))))
        with self.assertRaises(Exception):
            model.model_validate_json(proposal(
                [{"node": "r1", "prefix": "10.30.30.0/24",
                  "next_hop": "10.0.0.13"}]))


class GateOnEnterpriseLab(unittest.TestCase):
    def setUp(self):
        self.gate_intent = load_intent(ENT)

    def verdict(self, raw):
        report = Gate(self.gate_intent).run(raw, source="test")
        failed = sorted({c["category"] for c in report["checks"]
                         if c["result"] == "FAIL"})
        return report["verdict"], failed

    def test_valid_route_passes(self):
        v, failed = self.verdict(proposal(
            [{"node": "edge-br", "prefix": "10.30.30.0/24",
              "next_hop": "10.0.0.13"}]))
        self.assertEqual(v, "PASS_PENDING_HUMAN_APPROVAL")
        self.assertEqual(failed, [])

    def test_next_hop_of_a_different_router_is_rejected(self):
        """10.0.0.21 is a real address, but not a neighbour of edge-br."""
        v, failed = self.verdict(proposal(
            [{"node": "edge-br", "prefix": "10.30.30.0/24",
              "next_hop": "10.0.0.21"}]))
        self.assertEqual(v, "REJECT")
        self.assertIn("topology", failed)

    def test_permit_over_a_declared_deny_is_rejected(self):
        v, failed = self.verdict(proposal(policy=[
            {"node": "fw-dmz", "src": "10.10.10.0/24",
             "dst": "10.30.30.12/32", "action": "permit"}]))
        self.assertEqual(v, "REJECT")
        self.assertIn("policy", failed)

    def test_narrow_permit_inside_a_denied_pair_is_rejected(self):
        """A smaller subnet must not slip past the policy layer."""
        v, failed = self.verdict(proposal(policy=[
            {"node": "edge-hq", "src": "10.10.10.128/25",
             "dst": "10.10.99.0/24", "action": "permit"}]))
        self.assertEqual(v, "REJECT")
        self.assertIn("policy", failed)


if __name__ == "__main__":
    unittest.main()
