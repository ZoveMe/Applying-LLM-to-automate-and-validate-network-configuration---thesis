"""Offline tests for the larger-topology Ansible multi-fault experiment."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from experiments.topology_l_ansible_demo import (
    ANSIBLE_INVENTORY,
    APPROVAL_TOKEN,
    EXPECTED_ANSIBLE_CORE_VERSION,
    EXPECTED_DOCKER_COLLECTION_VERSION,
    EXPECTED_HOST_IMAGE,
    EXPECTED_ROUTER_IMAGE,
    FAULT_CONFIRMATION,
    FAULT_PROFILE,
    INJECT_PLAYBOOK,
    INTENT_BUNDLE,
    RECONCILE_PLAYBOOK,
    REPAIR_PROFILE,
    VALIDATE_PLAYBOOK,
    VERIFY_FAULTS_PLAYBOOK,
    ApprovalDeclined,
    DemoError,
    ansible_args,
    assert_idempotent,
    bundle_sha256,
    fault_injection_args,
    reconciliation_args,
    run_demo,
    validate_controller_profile_files,
    expected_lab_image,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
LARGE_INTENT = REPO_ROOT / "benchmarks" / "topology-l" / "intent_l.yaml"
LARGE_TOPOLOGY = (
    REPO_ROOT / "benchmarks" / "topology-l" / "topology-l.clab.yml"
)


class FakeTopologyL:
    def __init__(
        self,
        *,
        fail_reconciliation_call: int | None = None,
        dirty_worktree: bool = False,
        ansible_core_version: str = EXPECTED_ANSIBLE_CORE_VERSION,
        docker_collection_version: str = EXPECTED_DOCKER_COLLECTION_VERSION,
        container_image_override: str | None = None,
    ):
        self.reconciliation_calls = 0
        self.validation_calls = 0
        self.fault_injections = 0
        self.fault_verifications = 0
        self.fail_reconciliation_call = fail_reconciliation_call
        self.dirty_worktree = dirty_worktree
        self.ansible_core_version = ansible_core_version
        self.docker_collection_version = docker_collection_version
        self.container_image_override = container_image_override

    def __call__(self, args, *, capture_output=False):
        args = [str(part) for part in args]

        if args[:2] == ["ansible-playbook", "--version"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=(
                    "ansible-playbook "
                    f"[core {self.ansible_core_version}]\n"
                ),
                stderr="",
            )
        if args == [
            "ansible-galaxy",
            "collection",
            "list",
            "community.docker",
        ]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=(
                    "Collection       Version\n"
                    "---------------- -------\n"
                    "community.docker "
                    f"{self.docker_collection_version}\n"
                ),
                stderr="",
            )
        if args[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="ok\n", stderr=""
            )
        if args == ["clab", "version"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="version: 0.68.0\ncommit: test\n",
                stderr="",
            )
        if args[:3] == ["docker", "inspect", "-f"]:
            node = args[-1]
            image = (
                self.container_image_override
                or expected_lab_image(node)
            )
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=f"true|sha256:{'a' * 64}|{image}\n",
                stderr="",
            )
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="bbe3eda4db797229a37ec35125a651d3828c263e\n",
                stderr="",
            )
        if args == ["git", "status", "--porcelain"]:
            stdout = " M README.md\n" if self.dirty_worktree else ""
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=stdout,
                stderr="",
            )

        if args and args[0] == "ansible-playbook":
            playbook = Path(args[3])
            if playbook == RECONCILE_PLAYBOOK:
                self.reconciliation_calls += 1
                if (
                    self.fail_reconciliation_call
                    == self.reconciliation_calls
                ):
                    return subprocess.CompletedProcess(
                        args, 2, stdout="", stderr="reconciliation failed"
                    )
                changed = (
                    0 if self.reconciliation_calls in {1, 3} else 3
                )
                recap = "\n".join(
                    f"{host} : ok=9 changed={changed} "
                    "unreachable=0 failed=0 skipped=0 rescued=0 ignored=0"
                    for host in ("r1", "r2", "r3")
                )
                return subprocess.CompletedProcess(
                    args, 0, stdout=f"PLAY RECAP\n{recap}\n", stderr=""
                )
            if playbook == INJECT_PLAYBOOK:
                self.fault_injections += 1
                return subprocess.CompletedProcess(
                    args, 0, stdout="three faults injected\n", stderr=""
                )
            if playbook == VERIFY_FAULTS_PLAYBOOK:
                self.fault_verifications += 1
                return subprocess.CompletedProcess(
                    args, 0, stdout="three faults verified\n", stderr=""
                )
            if playbook == VALIDATE_PLAYBOOK:
                self.validation_calls += 1
                if self.validation_calls == 2:
                    return subprocess.CompletedProcess(
                        args,
                        2,
                        stdout=(
                            "fatal: [r1]: FAILED! => "
                            "FAIL: r1 route is missing\n"
                            "PLAY RECAP\n"
                            "r1 : ok=4 changed=0 unreachable=0 failed=1\n"
                        ),
                        stderr="",
                    )
                return subprocess.CompletedProcess(
                    args, 0, stdout="intent validation passed\n", stderr=""
                )

        raise AssertionError(f"unexpected command: {args}")


class BundleTests(unittest.TestCase):
    def test_live_topology_uses_fixed_router_and_endpoint_images(self):
        topology = yaml.safe_load(
            LARGE_TOPOLOGY.read_text(encoding="utf-8")
        )
        nodes = topology["topology"]["nodes"]
        self.assertEqual(
            {nodes[name]["image"] for name in ("r1", "r2", "r3")},
            {EXPECTED_ROUTER_IMAGE},
        )
        self.assertEqual(
            {
                nodes[name]["image"]
                for name in (
                    "h-client",
                    "h-server",
                    "h-mgmt",
                    "h-dmz",
                    "h-sensors",
                )
            },
            {EXPECTED_HOST_IMAGE},
        )
        self.assertNotIn(
            ":latest",
            LARGE_TOPOLOGY.read_text(encoding="utf-8"),
        )

    def test_bundle_is_canonical_and_matches_large_intent(self):
        bundle = json.loads(INTENT_BUNDLE.read_text(encoding="utf-8"))
        intent = yaml.safe_load(LARGE_INTENT.read_text(encoding="utf-8"))

        expected_routes = {
            (fact["node"], fact["prefix"], fact["via"])
            for fact in intent["config_facts"]
            if fact["kind"] == "route"
        }
        actual_routes = {
            (route["node"], route["prefix"], route["next_hop"])
            for route in bundle["routes"]
        }
        expected_policies = {
            (rule["node"], rule["src"], rule["dst"], "DROP")
            for rule in intent["policy_rules"]["must_deny"]
        }
        actual_policies = {
            (
                policy["node"],
                policy["src"],
                policy["dst"],
                policy["action"],
            )
            for policy in bundle["policies"]
        }

        self.assertEqual(bundle["profile"], REPAIR_PROFILE)
        self.assertEqual(bundle["lab"], "thesis-net-l")
        self.assertEqual(len(bundle["routes"]), 10)
        self.assertEqual(len(bundle["policies"]), 2)
        self.assertEqual(actual_routes, expected_routes)
        self.assertEqual(actual_policies, expected_policies)

    def test_checksum_hashes_exact_bundle_bytes(self):
        self.assertEqual(
            bundle_sha256(),
            hashlib.sha256(INTENT_BUNDLE.read_bytes()).hexdigest(),
        )


class PlaybookSafetyTests(unittest.TestCase):
    def test_rejects_controller_requirement_file_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            core = root / "requirements-ansible.txt"
            collections = root / "requirements.yml"
            core.write_text(
                "ansible-core==2.17.13\n",
                encoding="utf-8",
            )
            collections.write_text(
                "---\ncollections:\n"
                "  - name: community.docker\n"
                "    version: 5.2.1\n",
                encoding="utf-8",
            )
            with (
                patch(
                    "experiments.topology_l_ansible_demo."
                    "ANSIBLE_REQUIREMENTS",
                    core,
                ),
                patch(
                    "experiments.topology_l_ansible_demo."
                    "COLLECTION_REQUIREMENTS",
                    collections,
                ),
                self.assertRaisesRegex(
                    DemoError,
                    "does not match the frozen controller profile",
                ),
            ):
                validate_controller_profile_files()

    def test_reconciliation_command_binds_profile_and_checksum(self):
        checksum = "a" * 64
        command = reconciliation_args(checksum)
        self.assertEqual(command[:3], [
            "ansible-playbook",
            "-i",
            str(ANSIBLE_INVENTORY),
        ])
        self.assertEqual(Path(command[3]), RECONCILE_PLAYBOOK)
        extra = json.loads(command[command.index("--extra-vars") + 1])
        self.assertEqual(
            extra,
            {
                "repair_profile": REPAIR_PROFILE,
                "approved_bundle_sha256": checksum,
            },
        )

    def test_fault_command_requires_exact_profile_and_confirmation(self):
        command = fault_injection_args()
        self.assertEqual(Path(command[3]), INJECT_PLAYBOOK)
        extra = json.loads(command[command.index("--extra-vars") + 1])
        self.assertEqual(extra["fault_profile"], FAULT_PROFILE)
        self.assertEqual(
            extra["fault_confirmation"],
            FAULT_CONFIRMATION,
        )

    def test_reconciliation_playbook_fails_closed_and_is_serial(self):
        text = RECONCILE_PLAYBOOK.read_text(encoding="utf-8")
        self.assertIn("serial: 1", text)
        self.assertIn("any_errors_fatal: true", text)
        self.assertIn("approved_bundle_sha256", text)
        self.assertIn("calculated_bundle_sha256", text)
        self.assertIn("Fail closed unless the approved bundle is exact", text)
        self.assertIn("STALE_ROUTE_REMOVED", text)
        self.assertIn("ROUTE_ADDED", text)
        self.assertIn("POLICY_ADDED", text)

    def test_validation_covers_control_plane_policy_and_data_plane(self):
        text = VALIDATE_PLAYBOOK.read_text(encoding="utf-8")
        self.assertIn("expected route uses the intended next hop", text)
        self.assertIn("client reaches server and DMZ", text)
        self.assertNotIn("client reaches server, DMZ, and sensors", text)
        self.assertIn("client is isolated from management", text)
        self.assertIn("sensors are isolated from the client segment", text)
        self.assertIn("sensors reach the server segment", text)

    def test_explicit_playbook_paths_are_fixed(self):
        self.assertEqual(
            ansible_args(VALIDATE_PLAYBOOK)[3],
            str(VALIDATE_PLAYBOOK),
        )


class IdempotencyTests(unittest.TestCase):
    def test_accepts_zero_changes_on_all_three_routers(self):
        stdout = "\n".join(
            f"{host} : ok=9 changed=0 unreachable=0 failed=0"
            for host in ("r1", "r2", "r3")
        )
        result = subprocess.CompletedProcess(
            ["ansible-playbook"], 0, stdout=stdout, stderr=""
        )
        assert_idempotent(result)

    def test_rejects_any_change_on_second_reconciliation(self):
        stdout = "\n".join(
            [
                "r1 : ok=9 changed=0 unreachable=0 failed=0",
                "r2 : ok=9 changed=1 unreachable=0 failed=0",
                "r3 : ok=9 changed=0 unreachable=0 failed=0",
            ]
        )
        result = subprocess.CompletedProcess(
            ["ansible-playbook"], 0, stdout=stdout, stderr=""
        )
        with self.assertRaisesRegex(DemoError, "not idempotent"):
            assert_idempotent(result)


class WorkflowTests(unittest.TestCase):
    def test_unexpected_container_image_is_refused_before_fault_injection(self):
        fake = FakeTopologyL(container_image_override="alpine:latest")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(DemoError, "uses unexpected image"):
                run_demo(
                    output_dir=Path(temporary) / "run",
                    runner=fake,
                    approval_reader=lambda prompt: APPROVAL_TOKEN,
                )

            self.assertEqual(fake.fault_injections, 0)

    def test_wrong_ansible_core_version_is_refused_before_fault_injection(self):
        fake = FakeTopologyL(ansible_core_version="2.17.13")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(DemoError, "ansible-core version mismatch"):
                run_demo(
                    output_dir=Path(temporary) / "run",
                    runner=fake,
                    approval_reader=lambda prompt: APPROVAL_TOKEN,
                )

            self.assertEqual(fake.fault_injections, 0)

    def test_wrong_docker_collection_is_refused_before_fault_injection(self):
        fake = FakeTopologyL(docker_collection_version="5.2.0")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                DemoError,
                "community.docker version mismatch",
            ):
                run_demo(
                    output_dir=Path(temporary) / "run",
                    runner=fake,
                    approval_reader=lambda prompt: APPROVAL_TOKEN,
                )

            self.assertEqual(fake.fault_injections, 0)

    def test_dirty_worktree_is_refused_before_fault_injection(self):
        fake = FakeTopologyL(dirty_worktree=True)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(DemoError, "worktree must be clean"):
                run_demo(
                    output_dir=Path(temporary) / "run",
                    runner=fake,
                    approval_reader=lambda prompt: APPROVAL_TOKEN,
                )

            self.assertEqual(fake.fault_injections, 0)
            self.assertEqual(fake.reconciliation_calls, 0)

    def test_success_records_approval_validation_and_idempotency(self):
        fake = FakeTopologyL()
        with tempfile.TemporaryDirectory() as temporary:
            paths = run_demo(
                output_dir=Path(temporary) / "run",
                runner=fake,
                approval_reader=lambda prompt: APPROVAL_TOKEN,
            )

            approval = json.loads(
                paths.approval.read_text(encoding="utf-8")
            )
            self.assertEqual(approval["decision"], "APPROVED")
            self.assertFalse(approval["automatic_deployment_by_llm"])
            self.assertEqual(
                approval["intent_bundle_sha256"],
                bundle_sha256(),
            )
            self.assertTrue(paths.post_validation.is_file())
            self.assertTrue(paths.idempotency.is_file())
            self.assertFalse(paths.emergency_recovery.exists())
            self.assertEqual(fake.fault_injections, 1)
            self.assertEqual(fake.fault_verifications, 1)
            self.assertEqual(fake.validation_calls, 3)
            self.assertEqual(fake.reconciliation_calls, 3)

    def test_wrong_approval_token_refuses_repair_and_recovers(self):
        fake = FakeTopologyL()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            with self.assertRaises(ApprovalDeclined):
                run_demo(
                    output_dir=root,
                    runner=fake,
                    approval_reader=lambda prompt: "approve",
                )

            approval = json.loads(
                (root / "06-human-approval.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(approval["decision"], "NOT_APPROVED")
            self.assertFalse((root / "07-approved-reconciliation.json").exists())
            self.assertTrue((root / "99-emergency-recovery.json").is_file())
            self.assertEqual(fake.reconciliation_calls, 2)

    def test_failed_approved_reconciliation_uses_emergency_recovery(self):
        fake = FakeTopologyL(fail_reconciliation_call=2)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            with self.assertRaisesRegex(
                DemoError,
                "approved_reconciliation failed",
            ):
                run_demo(
                    output_dir=root,
                    runner=fake,
                    approval_reader=lambda prompt: APPROVAL_TOKEN,
                )

            self.assertTrue((root / "99-emergency-recovery.json").is_file())
            self.assertEqual(fake.reconciliation_calls, 3)


if __name__ == "__main__":
    unittest.main()
