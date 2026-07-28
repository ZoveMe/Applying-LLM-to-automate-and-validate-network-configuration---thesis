"""Offline tests for the independent Week 7 live-evidence verifier."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from experiments.topology_l_ansible_demo import (
    EXPECTED_ANSIBLE_CORE_VERSION,
    EXPECTED_DOCKER_COLLECTION_VERSION,
    EXPECTED_HOST_IMAGE,
    EXPECTED_ROUTER_IMAGE,
    LAB_NODES,
    RECONCILE_PLAYBOOK,
    ansible_args,
    approval_record,
    bundle_sha256,
    fault_injection_args,
    fault_verification_args,
    expected_lab_image,
    reconciliation_args,
    validation_args,
)
from experiments.verify_topology_l_ansible_evidence import (
    EvidenceVerificationError,
    render_macedonian_summary,
    verify_evidence,
    write_verification_outputs,
)


SOURCE_COMMIT = "b" * 40
TIMESTAMP = datetime(2026, 7, 28, 12, 0, 0).isoformat()


def command_record(
    stage: str,
    command: list[str],
    *,
    returncode: int = 0,
    stdout: str = "",
) -> dict:
    return {
        "stage": stage,
        "timestamp": TIMESTAMP,
        "command": command,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": "",
    }


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def recap(changed: dict[str, int], *, failed: int = 0) -> str:
    return "\n".join(
        f"{host} : ok=9 changed={changed[host]} "
        f"unreachable=0 failed={failed}"
        for host in ("r1", "r2", "r3")
    )


def build_valid_evidence(root: Path) -> None:
    root.mkdir()
    image_id = f"sha256:{'a' * 64}"
    container_images = {
        node: {
            "configured_image": expected_lab_image(node),
            "image_id": image_id,
        }
        for node in LAB_NODES
    }
    checks = [
        command_record(
            "preflight",
            ["git", "rev-parse", "HEAD"],
            stdout=f"{SOURCE_COMMIT}\n",
        ),
        command_record(
            "preflight",
            ["git", "status", "--porcelain"],
            stdout="",
        ),
        command_record(
            "preflight",
            ["ansible-playbook", "--version"],
            stdout=(
                "ansible-playbook "
                f"[core {EXPECTED_ANSIBLE_CORE_VERSION}]\n"
            ),
        ),
        command_record(
            "preflight",
            [
                "ansible-galaxy",
                "collection",
                "list",
                "community.docker",
            ],
            stdout=(
                "Collection       Version\n"
                "---------------- -------\n"
                "community.docker "
                f"{EXPECTED_DOCKER_COLLECTION_VERSION}\n"
            ),
        ),
        command_record(
            "preflight",
            ["clab", "version"],
            stdout="version: 0.68.0\ncommit: test\n",
        ),
        command_record(
            "preflight",
            ["docker", "info"],
            stdout="ok\n",
        ),
    ]
    checks.extend(
        command_record(
            "preflight",
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}}|{{.Image}}|{{.Config.Image}}",
                node,
            ],
            stdout=(
                f"true|{image_id}|{expected_lab_image(node)}\n"
            ),
        )
        for node in LAB_NODES
    )
    write_json(
        root / "00-preflight.json",
        {
            "stage": "preflight",
            "timestamp": TIMESTAMP,
            "source_commit": SOURCE_COMMIT,
            "lab_nodes": list(LAB_NODES),
            "container_images": container_images,
            "checks": checks,
        },
    )

    checksum = bundle_sha256()
    write_json(
        root / "01-baseline-reconciliation.json",
        command_record(
            "baseline_reconciliation",
            reconciliation_args(checksum),
            stdout=f"PLAY RECAP\n{recap({'r1': 0, 'r2': 0, 'r3': 0})}\n",
        ),
    )
    write_json(
        root / "02-baseline-validation.json",
        command_record(
            "baseline_validation",
            validation_args(),
            stdout="PASS: baseline intent validated\n",
        ),
    )
    write_json(
        root / "03-fault-injection.json",
        command_record(
            "fault_injection",
            fault_injection_args(),
            stdout="FAULT_F1_INJECTED\nFAULT_F2_INJECTED\nFAULT_F3_INJECTED\n",
        ),
    )
    write_json(
        root / "04-fault-profile-verified.json",
        command_record(
            "fault_profile_verification",
            fault_verification_args(),
            stdout="PASS: F1\nPASS: F2\nPASS: F3\n",
        ),
    )
    write_json(
        root / "05-intent-validation-failed.json",
        command_record(
            "fault_detection",
            validation_args(),
            returncode=2,
            stdout=(
                "fatal: [r1]: FAILED! => FAIL: r1 route is missing\n"
                f"PLAY RECAP\n{recap({'r1': 0, 'r2': 0, 'r3': 0}, failed=1)}\n"
            ),
        ),
    )
    approval = approval_record(
        approved=True,
        checksum=checksum,
        commit=SOURCE_COMMIT,
    )
    approval["timestamp"] = TIMESTAMP
    write_json(root / "06-human-approval.json", approval)
    write_json(
        root / "07-approved-reconciliation.json",
        command_record(
            "approved_reconciliation",
            reconciliation_args(checksum),
            stdout=f"PLAY RECAP\n{recap({'r1': 1, 'r2': 1, 'r3': 1})}\n",
        ),
    )
    write_json(
        root / "08-post-repair-validation.json",
        command_record(
            "post_repair_validation",
            validation_args(),
            stdout="PASS: repaired intent validated\n",
        ),
    )
    write_json(
        root / "09-idempotency.json",
        command_record(
            "idempotency",
            reconciliation_args(checksum),
            stdout=f"PLAY RECAP\n{recap({'r1': 0, 'r2': 0, 'r3': 0})}\n",
        ),
    )


def mutate_json(path: Path, change) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    write_json(path, value)


class EvidenceVerifierTests(unittest.TestCase):
    def test_accepts_complete_consistent_live_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            build_valid_evidence(root)

            summary = verify_evidence(root)
            markdown = render_macedonian_summary(summary)

            self.assertEqual(summary["verification_status"], "PASS")
            self.assertEqual(summary["faults_verified"], ["F1", "F2", "F3"])
            self.assertEqual(
                summary["idempotency_changed"],
                {"r1": 0, "r2": 0, "r3": 0},
            )
            self.assertFalse(summary["automatic_deployment_by_llm"])
            self.assertEqual(
                summary["ansible_core_version"],
                EXPECTED_ANSIBLE_CORE_VERSION,
            )
            self.assertEqual(
                summary["community_docker_version"],
                EXPECTED_DOCKER_COLLECTION_VERSION,
            )
            self.assertEqual(
                summary["router_image"],
                EXPECTED_ROUTER_IMAGE,
            )
            self.assertEqual(
                summary["host_image"],
                EXPECTED_HOST_IMAGE,
            )
            self.assertEqual(
                set(summary["container_images"]),
                set(LAB_NODES),
            )
            self.assertNotIn("[ПОПОЛНИ]", markdown)
            self.assertIn("Статус на независната проверка: PASS", markdown)

    def test_rejects_unexpected_container_image_in_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            build_valid_evidence(root)
            preflight = root / "00-preflight.json"
            mutate_json(
                preflight,
                lambda value: value["checks"][6].update(
                    {
                        "stdout": (
                            f"true|sha256:{'a' * 64}|"
                            "frrouting/frr:latest\n"
                        )
                    }
                ),
            )

            with self.assertRaisesRegex(
                EvidenceVerificationError,
                "uses unexpected image",
            ):
                verify_evidence(root)

    def test_rejects_wrong_ansible_core_version_in_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            build_valid_evidence(root)
            preflight = root / "00-preflight.json"
            mutate_json(
                preflight,
                lambda value: value["checks"][2].update(
                    {"stdout": "ansible-playbook [core 2.17.13]\n"}
                ),
            )

            with self.assertRaisesRegex(
                EvidenceVerificationError,
                "ansible-core version",
            ):
                verify_evidence(root)

    def test_rejects_wrong_docker_collection_version_in_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            build_valid_evidence(root)
            preflight = root / "00-preflight.json"
            mutate_json(
                preflight,
                lambda value: value["checks"][3].update(
                    {
                        "stdout": (
                            "Collection       Version\n"
                            "---------------- -------\n"
                            "community.docker 5.2.0\n"
                        )
                    }
                ),
            )

            with self.assertRaisesRegex(
                EvidenceVerificationError,
                "community.docker version",
            ):
                verify_evidence(root)

    def test_rejects_missing_required_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            build_valid_evidence(root)
            (root / "08-post-repair-validation.json").unlink()

            with self.assertRaisesRegex(
                EvidenceVerificationError,
                "required live evidence is missing",
            ):
                verify_evidence(root)

    def test_rejects_emergency_recovery_run_as_normal_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            build_valid_evidence(root)
            write_json(
                root / "99-emergency-recovery.json",
                command_record(
                    "emergency_recovery",
                    reconciliation_args(bundle_sha256()),
                ),
            )

            with self.assertRaisesRegex(
                EvidenceVerificationError,
                "emergency recovery evidence is present",
            ):
                verify_evidence(root)

    def test_rejects_approval_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            build_valid_evidence(root)
            approval = root / "06-human-approval.json"
            mutate_json(
                approval,
                lambda value: value.update(
                    {"intent_bundle_sha256": "0" * 64}
                ),
            )

            with self.assertRaisesRegex(
                EvidenceVerificationError,
                "intent_bundle_sha256",
            ):
                verify_evidence(root)

    def test_rejects_fault_validation_that_passed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            build_valid_evidence(root)
            fault = root / "05-intent-validation-failed.json"
            mutate_json(fault, lambda value: value.update({"returncode": 0}))

            with self.assertRaisesRegex(
                EvidenceVerificationError,
                "unexpectedly succeeded",
            ):
                verify_evidence(root)

    def test_rejects_non_idempotent_second_reconciliation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            build_valid_evidence(root)
            idempotency = root / "09-idempotency.json"
            mutate_json(
                idempotency,
                lambda value: value.update(
                    {
                        "stdout": recap(
                            {"r1": 0, "r2": 1, "r3": 0}
                        )
                    }
                ),
            )

            with self.assertRaisesRegex(
                EvidenceVerificationError,
                "not changed=0",
            ):
                verify_evidence(root)

    def test_rejects_unexpected_playbook_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            build_valid_evidence(root)
            post = root / "08-post-repair-validation.json"
            mutate_json(
                post,
                lambda value: value.update(
                    {
                        "command": ansible_args(RECONCILE_PLAYBOOK)
                    }
                ),
            )

            with self.assertRaisesRegex(
                EvidenceVerificationError,
                "wrong playbook",
            ):
                verify_evidence(root)

    def test_refuses_derived_output_inside_raw_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            build_valid_evidence(root)
            summary = verify_evidence(root)

            with self.assertRaisesRegex(
                EvidenceVerificationError,
                "must not be written inside",
            ):
                write_verification_outputs(
                    summary,
                    evidence_dir=root,
                    json_out=root / "derived.json",
                    markdown_out=None,
                )


if __name__ == "__main__":
    unittest.main()
