"""Offline safety tests for the reusable guarded repair demonstration."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from experiments.guarded_repair_demo import (
    APPROVAL_TOKEN,
    DYNAMIC_VALIDATOR,
    EXPECTED_ROUTE,
    ApprovalDeclined,
    DemoError,
    ensure_new_output_dir,
    make_approval_record,
    run_demo,
    validate_runtime_report,
    validate_exact_repair,
    vtysh_args,
)
from llm.ollama_client import run_pipeline


def accepted_evidence():
    return {
        "model": "MOCK(test-model)",
        "outcome": "ACCEPTED",
        "proposal": {
            "rationale": "Restore the missing server route.",
            "decision": "PROPOSE",
            "reason_code": "ACTIONABLE_CHANGE",
            "static_routes": [EXPECTED_ROUTE.as_dict()],
            "access_policy": [],
        },
        "gate_report": {
            "verdict": "PASS_PENDING_HUMAN_APPROVAL",
            "summary": {"passed": 4, "failed": 0},
            "checks": [],
        },
    }


def fake_pipeline(requirement, model, retries, mock_raw, url):
    return accepted_evidence()


class FakeLab:
    def __init__(self, *, fail_restore=False, fail_remove=False):
        self.route_present = True
        self.events = []
        self.validation_runs = 0
        self.fail_restore = fail_restore
        self.fail_remove = fail_remove

    def __call__(self, args, *, capture_output=False):
        args = [str(part) for part in args]
        self.events.append(tuple(args))

        if args[:2] == ["containerlab", "version"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="version: 0.77.0\n",
                stderr="",
            )

        if args[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

        if args[:3] == ["docker", "inspect", "-f"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="true\n",
                stderr="",
            )

        if len(args) >= 5 and args[:2] == ["docker", "exec"]:
            if "vtysh" in args:
                commands = [
                    args[index + 1]
                    for index, value in enumerate(args[:-1])
                    if value == "-c"
                ]
                if any(command.startswith("ip route ") for command in commands):
                    if self.fail_restore:
                        return subprocess.CompletedProcess(
                            args,
                            1,
                            stdout="",
                            stderr="restore failed",
                        )
                    self.route_present = True
                elif any(
                    command.startswith("no ip route ")
                    for command in commands
                ):
                    self.route_present = False
                    if self.fail_remove:
                        return subprocess.CompletedProcess(
                            args,
                            1,
                            stdout="",
                            stderr="remove failed after partial change",
                        )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        if len(args) >= 2 and Path(args[1]) == DYNAMIC_VALIDATOR:
            self.validation_runs += 1
            if "--out" in args:
                output = Path(args[args.index("--out") + 1])
                checks = [
                    {
                        "id": check_id,
                        "result": (
                            "FAIL"
                            if check_id == "C2" and not self.route_present
                            else "PASS"
                        ),
                    }
                    for check_id in ("R1", "R2", "R3", "C1", "C2", "C3", "C4")
                ]
                failed = sum(check["result"] == "FAIL" for check in checks)
                output.write_text(
                    json.dumps(
                        {
                            "verdict": (
                                "MATCHES_INTENT"
                                if self.route_present
                                else "DOES_NOT_MATCH_INTENT"
                            ),
                            "summary": {
                                "passed": len(checks) - failed,
                                "failed": failed,
                            },
                            "checks": checks,
                        }
                    )
                )
            return subprocess.CompletedProcess(
                args,
                0 if self.route_present else 1,
                stdout="",
                stderr="",
            )

        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="78d8f2200000000000000000000000000000000\n",
                stderr="",
            )

        raise AssertionError(f"unexpected command: {args}")


class ExactProposalTests(unittest.TestCase):
    def test_accepts_only_the_expected_route(self):
        proposal = validate_exact_repair(accepted_evidence())
        self.assertEqual(proposal["static_routes"], [EXPECTED_ROUTE.as_dict()])

    def test_rejects_extra_route(self):
        evidence = accepted_evidence()
        evidence["proposal"]["static_routes"].append(
            {
                "node": "r1",
                "prefix": "10.0.99.0/24",
                "next_hop": "10.0.12.2",
            }
        )
        with self.assertRaisesRegex(DemoError, "scope mismatch"):
            validate_exact_repair(evidence)

    def test_rejects_access_policy_change(self):
        evidence = accepted_evidence()
        evidence["proposal"]["access_policy"] = [
            {
                "node": "r1",
                "src": "10.0.1.0/24",
                "dst": "10.0.99.0/24",
                "action": "deny",
            }
        ]
        with self.assertRaisesRegex(DemoError, "access-policy"):
            validate_exact_repair(evidence)

    def test_rejects_non_passing_gate(self):
        evidence = accepted_evidence()
        evidence["outcome"] = "REJECTED_GATE"
        evidence["gate_report"]["verdict"] = "REJECT"
        with self.assertRaisesRegex(DemoError, "not accepted"):
            validate_exact_repair(evidence)

    def test_route_commands_are_fixed_argument_lists(self):
        restore = vtysh_args(EXPECTED_ROUTE, "restore")
        self.assertEqual(restore[:4], [
            "docker",
            "exec",
            "clab-thesis-net-r1",
            "vtysh",
        ])
        self.assertIn("ip route 10.0.2.0/24 10.0.12.2", restore)
        self.assertNotIn("shell=True", restore)

    def test_checked_mock_fixture_passes_the_real_schema_and_gate(self):
        fixture = (
            Path(__file__).resolve().parents[1]
            / "examples"
            / "suggestion_repair_route.json"
        )
        evidence = run_pipeline(
            "Offline fixture check.",
            mock_raw=fixture.read_text(),
        )
        proposal = validate_exact_repair(evidence)
        self.assertEqual(proposal["static_routes"], [EXPECTED_ROUTE.as_dict()])


class EvidenceSafetyTests(unittest.TestCase):
    def test_existing_output_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "existing"
            path.mkdir()
            with self.assertRaisesRegex(DemoError, "refusing overwrite"):
                ensure_new_output_dir(path)

    def test_approval_record_hashes_the_proposal_and_marks_no_auto_deploy(self):
        with tempfile.TemporaryDirectory() as temporary:
            proposal = Path(temporary) / "proposal.json"
            proposal.write_text('{"safe": true}\n')

            record = make_approval_record(
                decision="APPROVED",
                route=EXPECTED_ROUTE,
                proposal_path=proposal,
                commit="abc123",
                generation_mode="mock",
            )

            self.assertEqual(
                record["proposal_sha256"],
                hashlib.sha256(proposal.read_bytes()).hexdigest(),
            )
            self.assertFalse(record["automatic_deployment_by_llm"])
            self.assertEqual(record["approval_scope"], EXPECTED_ROUTE.as_dict())

    def test_runtime_report_must_include_the_c2_route_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "verdict": "DOES_NOT_MATCH_INTENT",
                        "summary": {"passed": 0, "failed": 1},
                        "checks": [{"id": "R1", "result": "FAIL"}],
                    }
                )
            )
            with self.assertRaisesRegex(DemoError, "exactly one C2"):
                validate_runtime_report(
                    report,
                    expected_verdict="DOES_NOT_MATCH_INTENT",
                    expected_c2_result="FAIL",
                )

    def test_success_report_must_contain_all_seven_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "verdict": "MATCHES_INTENT",
                        "summary": {"passed": 1, "failed": 0},
                        "checks": [{"id": "C2", "result": "PASS"}],
                    }
                )
            )
            with self.assertRaisesRegex(DemoError, "exactly 7 checks"):
                validate_runtime_report(
                    report,
                    expected_verdict="MATCHES_INTENT",
                    expected_c2_result="PASS",
                )


class WorkflowTests(unittest.TestCase):
    def test_success_requires_approval_and_writes_four_evidence_files(self):
        lab = FakeLab()
        approval_events = []

        def approve(prompt):
            approval_events.append(prompt)
            return APPROVAL_TOKEN

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            mock = Path(temporary) / "mock.json"
            mock.write_text(json.dumps(accepted_evidence()["proposal"]))

            paths = run_demo(
                output_dir=root,
                requirement="Restore the missing route.",
                model="test-model",
                retries=1,
                url="http://unused.invalid",
                mock_path=mock,
                runner=lab,
                approval_reader=approve,
                pipeline=fake_pipeline,
                sleeper=lambda seconds: None,
            )

            self.assertEqual(len(approval_events), 1)
            self.assertTrue(paths.before.is_relative_to(root))
            self.assertEqual(
                {path.name for path in root.glob("*.json")},
                {
                    "01-fault-detected.json",
                    "02-ollama-proposal.json",
                    "03-human-approval.json",
                    "04-repair-validated.json",
                },
            )
            approval = json.loads(paths.approval.read_text())
            self.assertEqual(approval["decision"], "APPROVED")
            self.assertEqual(approval["generation_mode"], "mock")
            self.assertTrue(lab.route_present)
            self.assertEqual(lab.validation_runs, 3)

    def test_wrong_approval_token_deploys_nothing_and_recovers(self):
        lab = FakeLab()
        approval_seen_at = None

        def reject(prompt):
            nonlocal approval_seen_at
            approval_seen_at = len(lab.events)
            return "approve"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            mock = Path(temporary) / "mock.json"
            mock.write_text(json.dumps(accepted_evidence()["proposal"]))

            with self.assertRaises(ApprovalDeclined):
                run_demo(
                    output_dir=root,
                    requirement="Restore the missing route.",
                    model="test-model",
                    retries=1,
                    url="http://unused.invalid",
                    mock_path=mock,
                    runner=lab,
                    approval_reader=reject,
                    pipeline=fake_pipeline,
                    sleeper=lambda seconds: None,
                )

            route_additions = [
                index
                for index, event in enumerate(lab.events)
                if "ip route 10.0.2.0/24 10.0.12.2" in event
            ]
            self.assertTrue(route_additions)
            self.assertTrue(all(index >= approval_seen_at for index in route_additions))
            self.assertFalse((root / "04-repair-validated.json").exists())
            approval = json.loads(
                (root / "03-human-approval.json").read_text()
            )
            self.assertEqual(approval["decision"], "NOT_APPROVED")
            self.assertTrue(lab.route_present)

    def test_invalid_proposal_is_never_presented_for_approval(self):
        lab = FakeLab()
        prompted = False

        def invalid_pipeline(requirement, model, retries, mock_raw, url):
            evidence = accepted_evidence()
            evidence["proposal"]["static_routes"] = []
            return evidence

        def unexpected_prompt(prompt):
            nonlocal prompted
            prompted = True
            return APPROVAL_TOKEN

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            mock = Path(temporary) / "mock.json"
            mock.write_text("{}")

            with self.assertRaisesRegex(DemoError, "scope mismatch"):
                run_demo(
                    output_dir=root,
                    requirement="Restore the missing route.",
                    model="test-model",
                    retries=1,
                    url="http://unused.invalid",
                    mock_path=mock,
                    runner=lab,
                    approval_reader=unexpected_prompt,
                    pipeline=invalid_pipeline,
                    sleeper=lambda seconds: None,
                )

            self.assertFalse(prompted)
            self.assertFalse((root / "03-human-approval.json").exists())
            self.assertTrue(lab.route_present)

    def test_failed_emergency_recovery_is_a_hard_error(self):
        lab = FakeLab(fail_restore=True)

        def invalid_pipeline(requirement, model, retries, mock_raw, url):
            evidence = accepted_evidence()
            evidence["proposal"]["static_routes"] = []
            return evidence

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            mock = Path(temporary) / "mock.json"
            mock.write_text("{}")

            with self.assertRaisesRegex(
                DemoError,
                "automatic route recovery failed",
            ):
                run_demo(
                    output_dir=root,
                    requirement="Restore the missing route.",
                    model="test-model",
                    retries=1,
                    url="http://unused.invalid",
                    mock_path=mock,
                    runner=lab,
                    approval_reader=lambda prompt: APPROVAL_TOKEN,
                    pipeline=invalid_pipeline,
                    sleeper=lambda seconds: None,
                )

            self.assertFalse(lab.route_present)

    def test_partial_fault_injection_failure_still_restores_route(self):
        lab = FakeLab(fail_remove=True)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            mock = Path(temporary) / "mock.json"
            mock.write_text("{}")

            with self.assertRaisesRegex(DemoError, "route remove"):
                run_demo(
                    output_dir=root,
                    requirement="Restore the missing route.",
                    model="test-model",
                    retries=1,
                    url="http://unused.invalid",
                    mock_path=mock,
                    runner=lab,
                    approval_reader=lambda prompt: APPROVAL_TOKEN,
                    pipeline=fake_pipeline,
                    sleeper=lambda seconds: None,
                )

            self.assertTrue(lab.route_present)


if __name__ == "__main__":
    unittest.main()
