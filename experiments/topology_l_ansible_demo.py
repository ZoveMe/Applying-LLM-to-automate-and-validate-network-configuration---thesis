#!/usr/bin/env python3
"""Guarded Ansible multi-fault recovery for the larger thesis topology.

This experiment is deliberately separate from the frozen Week 6 model
campaign. It evaluates whether a checksum-bound, allow-listed Ansible
reconciliation can recover three simultaneous faults across three routers:

* F1: a missing r1 route to the DMZ;
* F2: a wrong r2 next hop for the DMZ;
* F3: a missing r3 sensors-to-client deny rule.

The script requires explicit human approval before the experimental repair.
Any failure after fault injection triggers an independent deterministic
reconciliation so the laboratory is not left in the injected fault state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE_INVENTORY = REPO_ROOT / "ansible" / "inventory_topology_l.yml"
RECONCILE_PLAYBOOK = REPO_ROOT / "ansible" / "reconcile_topology_l.yml"
INJECT_PLAYBOOK = REPO_ROOT / "ansible" / "inject_topology_l_faults.yml"
VERIFY_FAULTS_PLAYBOOK = (
    REPO_ROOT / "ansible" / "verify_topology_l_faults.yml"
)
VALIDATE_PLAYBOOK = REPO_ROOT / "ansible" / "validate_topology_l.yml"
INTENT_BUNDLE = REPO_ROOT / "examples" / "topology_l_intent_bundle.json"
ANSIBLE_REQUIREMENTS = REPO_ROOT / "requirements-ansible.txt"
COLLECTION_REQUIREMENTS = REPO_ROOT / "ansible" / "requirements.yml"

REPAIR_PROFILE = "topology_l_intent_v1"
FAULT_PROFILE = "topology_l_three_faults_v1"
FAULT_CONFIRMATION = "INJECT_THESIS_FAULTS"
APPROVAL_TOKEN = "APPROVE_TOPOLOGY_L_REPAIR"
EXPECTED_ANSIBLE_CORE_VERSION = "2.17.14"
EXPECTED_DOCKER_COLLECTION_VERSION = "5.2.1"
EXPECTED_ROUTER_IMAGE = "quay.io/frrouting/frr:9.1.1"
EXPECTED_HOST_IMAGE = "alpine:3.20.10"

LAB_NODES = (
    "clab-thesis-net-l-r1",
    "clab-thesis-net-l-r2",
    "clab-thesis-net-l-r3",
    "clab-thesis-net-l-h-client",
    "clab-thesis-net-l-h-server",
    "clab-thesis-net-l-h-mgmt",
    "clab-thesis-net-l-h-dmz",
    "clab-thesis-net-l-h-sensors",
)


class DemoError(RuntimeError):
    """Raised when the multi-fault experiment must stop safely."""


class ApprovalDeclined(DemoError):
    """Raised when the exact repair approval token was not entered."""


@dataclass(frozen=True)
class EvidencePaths:
    root: Path

    @property
    def preflight(self) -> Path:
        return self.root / "00-preflight.json"

    @property
    def baseline_reconciliation(self) -> Path:
        return self.root / "01-baseline-reconciliation.json"

    @property
    def baseline_validation(self) -> Path:
        return self.root / "02-baseline-validation.json"

    @property
    def fault_injection(self) -> Path:
        return self.root / "03-fault-injection.json"

    @property
    def fault_profile(self) -> Path:
        return self.root / "04-fault-profile-verified.json"

    @property
    def fault_detected(self) -> Path:
        return self.root / "05-intent-validation-failed.json"

    @property
    def approval(self) -> Path:
        return self.root / "06-human-approval.json"

    @property
    def reconciliation(self) -> Path:
        return self.root / "07-approved-reconciliation.json"

    @property
    def post_validation(self) -> Path:
        return self.root / "08-post-repair-validation.json"

    @property
    def idempotency(self) -> Path:
        return self.root / "09-idempotency.json"

    @property
    def emergency_recovery(self) -> Path:
        return self.root / "99-emergency-recovery.json"


CommandRunner = Callable[..., subprocess.CompletedProcess]
ApprovalReader = Callable[[str], str]


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return (
        REPO_ROOT
        / "docs"
        / "evidence"
        / "week7-ansible-multifault-runs"
        / stamp
    )


def run_command(
    args: list[str],
    *,
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        capture_output=capture_output,
        text=True,
        check=False,
    )


def ensure_new_output_dir(path: Path) -> None:
    if path.exists():
        raise DemoError(f"evidence path already exists; refusing overwrite: {path}")


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def bundle_sha256(path: Path = INTENT_BUNDLE) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command_record(
    stage: str,
    result: subprocess.CompletedProcess,
) -> dict:
    return {
        "stage": stage,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "command": [str(part) for part in result.args],
        "returncode": result.returncode,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
    }


def run_stage(
    *,
    stage: str,
    args: list[str],
    output: Path,
    runner: CommandRunner,
    require_success: bool = True,
) -> subprocess.CompletedProcess:
    result = runner(args, capture_output=True)
    write_json(output, command_record(stage, result))
    if require_success and result.returncode != 0:
        detail = (result.stderr or result.stdout or "no output").strip()
        first_line = detail.splitlines()[0] if detail else "no output"
        raise DemoError(
            f"{stage} failed with exit code {result.returncode}: {first_line}"
        )
    return result


def ansible_args(
    playbook: Path,
    extra_vars: dict | None = None,
) -> list[str]:
    args = [
        "ansible-playbook",
        "-i",
        str(ANSIBLE_INVENTORY),
        str(playbook),
    ]
    if extra_vars is not None:
        args.extend(["--extra-vars", json.dumps(extra_vars, sort_keys=True)])
    return args


def reconciliation_args(checksum: str) -> list[str]:
    return ansible_args(
        RECONCILE_PLAYBOOK,
        {
            "repair_profile": REPAIR_PROFILE,
            "approved_bundle_sha256": checksum,
        },
    )


def fault_injection_args() -> list[str]:
    return ansible_args(
        INJECT_PLAYBOOK,
        {
            "fault_profile": FAULT_PROFILE,
            "fault_confirmation": FAULT_CONFIRMATION,
        },
    )


def validation_args() -> list[str]:
    return ansible_args(VALIDATE_PLAYBOOK)


def fault_verification_args() -> list[str]:
    return ansible_args(VERIFY_FAULTS_PLAYBOOK)


def source_commit(runner: CommandRunner) -> str:
    result = runner(["git", "rev-parse", "HEAD"], capture_output=True)
    if result.returncode != 0:
        raise DemoError("cannot resolve the source Git commit")
    return (result.stdout or "").strip()


def ansible_core_version(stdout: str) -> str:
    match = re.search(
        r"^ansible-playbook \[core ([0-9]+\.[0-9]+\.[0-9]+)\]$",
        stdout,
        flags=re.MULTILINE,
    )
    if match is None:
        raise DemoError("cannot parse the ansible-core version")
    return match.group(1)


def docker_collection_versions(stdout: str) -> set[str]:
    versions = {
        match.group(1)
        for match in re.finditer(
            r"^community\.docker\s+([0-9]+\.[0-9]+\.[0-9]+)\s*$",
            stdout,
            flags=re.MULTILINE,
        )
    }
    if not versions:
        raise DemoError("cannot find the community.docker collection version")
    return versions


def expected_lab_image(node: str) -> str:
    if node in LAB_NODES[:3]:
        return EXPECTED_ROUTER_IMAGE
    if node in LAB_NODES[3:]:
        return EXPECTED_HOST_IMAGE
    raise DemoError(f"unexpected laboratory node: {node}")


def container_identity(stdout: str, node: str) -> dict[str, str]:
    parts = stdout.strip().split("|")
    if len(parts) != 3:
        raise DemoError(f"cannot parse container identity for {node}")
    running, image_id, configured_image = parts
    if running.lower() != "true":
        raise DemoError(f"laboratory node is not running: {node}")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise DemoError(f"container image ID is invalid for {node}")
    expected_image = expected_lab_image(node)
    if configured_image != expected_image:
        raise DemoError(
            f"container {node} uses unexpected image: "
            f"expected {expected_image}, found {configured_image}"
        )
    return {
        "configured_image": configured_image,
        "image_id": image_id,
    }


def validate_controller_profile_files() -> None:
    core_pins = [
        line.strip()
        for line in ANSIBLE_REQUIREMENTS.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    expected_core_pins = [
        f"ansible-core=={EXPECTED_ANSIBLE_CORE_VERSION}"
    ]
    if core_pins != expected_core_pins:
        raise DemoError(
            "requirements-ansible.txt does not match the frozen controller "
            "profile"
        )

    collection_profile = yaml.safe_load(
        COLLECTION_REQUIREMENTS.read_text(encoding="utf-8")
    )
    expected_collection_profile = {
        "collections": [
            {
                "name": "community.docker",
                "version": EXPECTED_DOCKER_COLLECTION_VERSION,
            }
        ]
    }
    if collection_profile != expected_collection_profile:
        raise DemoError(
            "ansible/requirements.yml does not match the frozen controller "
            "profile"
        )


def preflight(runner: CommandRunner, output: Path) -> None:
    required_files = (
        ANSIBLE_INVENTORY,
        RECONCILE_PLAYBOOK,
        INJECT_PLAYBOOK,
        VERIFY_FAULTS_PLAYBOOK,
        VALIDATE_PLAYBOOK,
        INTENT_BUNDLE,
        ANSIBLE_REQUIREMENTS,
        COLLECTION_REQUIREMENTS,
    )
    missing = [str(path) for path in required_files if not path.is_file()]
    if missing:
        raise DemoError(f"required experiment files are missing: {missing}")
    validate_controller_profile_files()

    records = []
    container_images: dict[str, dict[str, str]] = {}
    commands = [
        ["git", "rev-parse", "HEAD"],
        ["git", "status", "--porcelain"],
        ["ansible-playbook", "--version"],
        ["ansible-galaxy", "collection", "list", "community.docker"],
        ["clab", "version"],
        ["docker", "info"],
    ]
    commands.extend(
        [
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}}|{{.Image}}|{{.Config.Image}}",
            node,
        ]
        for node in LAB_NODES
    )
    for command in commands:
        result = runner(command, capture_output=True)
        records.append(command_record("preflight", result))
        if result.returncode != 0:
            raise DemoError(f"preflight command failed: {' '.join(command)}")
        if command == ["git", "status", "--porcelain"]:
            if (result.stdout or "").strip():
                raise DemoError(
                    "worktree must be clean before live evidence is recorded"
                )
        if command == ["ansible-playbook", "--version"]:
            actual_core = ansible_core_version(result.stdout or "")
            if actual_core != EXPECTED_ANSIBLE_CORE_VERSION:
                raise DemoError(
                    "ansible-core version mismatch: expected "
                    f"{EXPECTED_ANSIBLE_CORE_VERSION}, found {actual_core}"
                )
        if command == [
            "ansible-galaxy",
            "collection",
            "list",
            "community.docker",
        ]:
            actual_collections = docker_collection_versions(
                result.stdout or ""
            )
            if actual_collections != {
                EXPECTED_DOCKER_COLLECTION_VERSION
            }:
                raise DemoError(
                    "community.docker version mismatch: expected only "
                    f"{EXPECTED_DOCKER_COLLECTION_VERSION}, found "
                    f"{', '.join(sorted(actual_collections))}"
                )
        if command[:3] == ["docker", "inspect", "-f"]:
            container_images[command[-1]] = container_identity(
                result.stdout or "",
                command[-1],
            )

    commit = (records[0]["stdout"] or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise DemoError("preflight did not resolve a valid Git commit")

    write_json(
        output,
        {
            "stage": "preflight",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "source_commit": commit,
            "lab_nodes": list(LAB_NODES),
            "container_images": container_images,
            "checks": records,
        },
    )


def approval_record(
    *,
    approved: bool,
    checksum: str,
    commit: str,
) -> dict:
    return {
        "stage": "human_approval",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "decision": "APPROVED" if approved else "NOT_APPROVED",
        "approval_token_required": APPROVAL_TOKEN,
        "repair_profile": REPAIR_PROFILE,
        "intent_bundle": str(INTENT_BUNDLE.relative_to(REPO_ROOT)),
        "intent_bundle_sha256": checksum,
        "source_commit": commit,
        "automatic_deployment_by_llm": False,
        "deployment_engine": "ansible",
    }


def recap_changed_counts(stdout: str) -> dict[str, int]:
    pattern = re.compile(
        r"^(r[123])\s*:\s*ok=\d+\s+changed=(\d+)\b",
        re.MULTILINE,
    )
    return {host: int(changed) for host, changed in pattern.findall(stdout)}


def assert_idempotent(result: subprocess.CompletedProcess) -> None:
    counts = recap_changed_counts(result.stdout or "")
    if set(counts) != {"r1", "r2", "r3"}:
        raise DemoError(
            "idempotency recap did not contain exactly r1, r2, and r3"
        )
    changed_hosts = {
        host: changed for host, changed in counts.items() if changed != 0
    }
    if changed_hosts:
        raise DemoError(
            f"second reconciliation was not idempotent: {changed_hosts}"
        )


def run_demo(
    *,
    output_dir: Path,
    runner: CommandRunner = run_command,
    approval_reader: ApprovalReader = input,
) -> EvidencePaths:
    paths = EvidencePaths(output_dir)
    ensure_new_output_dir(paths.root)
    paths.root.mkdir(parents=True)

    checksum = bundle_sha256()
    preflight(runner, paths.preflight)

    print("\n=== 1. ESTABLISH AND VALIDATE THE CLEAN BASELINE ===")
    run_stage(
        stage="baseline_reconciliation",
        args=reconciliation_args(checksum),
        output=paths.baseline_reconciliation,
        runner=runner,
    )
    run_stage(
        stage="baseline_validation",
        args=validation_args(),
        output=paths.baseline_validation,
        runner=runner,
    )

    recovery_required = False
    try:
        print("\n=== 2. INJECT THREE EXACT, CONTROLLED FAULTS ===")
        recovery_required = True
        run_stage(
            stage="fault_injection",
            args=fault_injection_args(),
            output=paths.fault_injection,
            runner=runner,
        )
        run_stage(
            stage="fault_profile_verification",
            args=fault_verification_args(),
            output=paths.fault_profile,
            runner=runner,
        )

        print("\n=== 3. PROVE THE NETWORK NO LONGER MATCHES INTENT ===")
        failed_validation = run_stage(
            stage="fault_detection",
            args=validation_args(),
            output=paths.fault_detected,
            runner=runner,
            require_success=False,
        )
        if failed_validation.returncode == 0:
            raise DemoError(
                "validator unexpectedly passed after the three faults"
            )

        print("\n=== 4. REQUIRE CHECKSUM-BOUND HUMAN APPROVAL ===")
        answer = approval_reader(
            f"Type {APPROVAL_TOKEN} to reconcile the exact bundle "
            f"{checksum}: "
        ).strip()
        approved = answer == APPROVAL_TOKEN
        write_json(
            paths.approval,
            approval_record(
                approved=approved,
                checksum=checksum,
                commit=source_commit(runner),
            ),
        )
        if not approved:
            raise ApprovalDeclined(
                "exact approval token not provided; experimental repair refused"
            )
        if bundle_sha256() != checksum:
            raise DemoError("intent bundle changed after human approval")

        print("\n=== 5. RECONCILE THE APPROVED MULTI-ROUTER BUNDLE ===")
        run_stage(
            stage="approved_reconciliation",
            args=reconciliation_args(checksum),
            output=paths.reconciliation,
            runner=runner,
        )

        print("\n=== 6. PROVE THE REPAIRED NETWORK MATCHES INTENT ===")
        run_stage(
            stage="post_repair_validation",
            args=validation_args(),
            output=paths.post_validation,
            runner=runner,
        )

        print("\n=== 7. PROVE ANSIBLE IDEMPOTENCY ===")
        idempotency = run_stage(
            stage="idempotency",
            args=reconciliation_args(checksum),
            output=paths.idempotency,
            runner=runner,
        )
        assert_idempotent(idempotency)
        recovery_required = False

        print("\nMULTI-FAULT ANSIBLE RECOVERY: PASS")
        print("Three faults detected, exact bundle approved,")
        print("three routers reconciled, validation passed, changed=0 on rerun.")
        print(f"Evidence directory: {paths.root}")
        return paths
    finally:
        if recovery_required:
            print("\nRestoring the approved intent bundle before exit...")
            recovery = runner(
                reconciliation_args(checksum),
                capture_output=True,
            )
            write_json(
                paths.emergency_recovery,
                command_record("emergency_recovery", recovery),
            )
            if recovery.returncode != 0:
                raise DemoError(
                    "emergency reconciliation failed; inspect topology-l"
                )
            print("Emergency reconciliation: OK")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the larger-topology guarded Ansible recovery demo"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_output_dir(),
        help="new evidence directory; existing paths are refused",
    )
    args = parser.parse_args()

    try:
        run_demo(output_dir=args.output_dir)
    except ApprovalDeclined as exc:
        print(f"NOT APPROVED: {exc}", file=sys.stderr)
        return 3
    except DemoError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nINTERRUPTED: emergency reconciliation was attempted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
