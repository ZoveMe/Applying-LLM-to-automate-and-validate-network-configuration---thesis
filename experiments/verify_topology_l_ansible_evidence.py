#!/usr/bin/env python3
"""Independently verify a Week 7 larger-topology live evidence directory.

The live runner records raw command evidence. This verifier is a separate,
read-only reporting gate: it checks that the complete experiment sequence is
present, internally consistent, bound to the current intent bundle, and safe
to cite. It never contacts Docker, Ansible, Containerlab, or Ollama.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

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
    LAB_NODES,
    RECONCILE_PLAYBOOK,
    REPAIR_PROFILE,
    VALIDATE_PLAYBOOK,
    VERIFY_FAULTS_PLAYBOOK,
    EvidencePaths,
    ansible_core_version,
    bundle_actions,
    bundle_sha256,
    container_identity,
    docker_collection_versions,
    recap_changed_counts,
)


class EvidenceVerificationError(RuntimeError):
    """Raised when live evidence is incomplete or unsafe to cite."""


STAGE_SPECS = (
    (
        "01-baseline-reconciliation.json",
        "baseline_reconciliation",
        RECONCILE_PLAYBOOK.name,
        "success",
    ),
    (
        "02-baseline-validation.json",
        "baseline_validation",
        VALIDATE_PLAYBOOK.name,
        "success",
    ),
    (
        "03-fault-injection.json",
        "fault_injection",
        INJECT_PLAYBOOK.name,
        "success",
    ),
    (
        "04-fault-profile-verified.json",
        "fault_profile_verification",
        VERIFY_FAULTS_PLAYBOOK.name,
        "success",
    ),
    (
        "05-intent-validation-failed.json",
        "fault_detection",
        VALIDATE_PLAYBOOK.name,
        "failure",
    ),
    (
        "07-approved-reconciliation.json",
        "approved_reconciliation",
        RECONCILE_PLAYBOOK.name,
        "success",
    ),
    (
        "08-post-repair-validation.json",
        "post_repair_validation",
        VALIDATE_PLAYBOOK.name,
        "success",
    ),
    (
        "09-idempotency.json",
        "idempotency",
        RECONCILE_PLAYBOOK.name,
        "success",
    ),
)

RAW_EVIDENCE_FILES = (
    "00-preflight.json",
    "01-baseline-reconciliation.json",
    "02-baseline-validation.json",
    "03-fault-injection.json",
    "04-fault-profile-verified.json",
    "05-intent-validation-failed.json",
    "06-human-approval.json",
    "07-approved-reconciliation.json",
    "08-post-repair-validation.json",
    "09-idempotency.json",
)


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvidenceVerificationError(
            f"cannot read evidence file {path.name}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise EvidenceVerificationError(
            f"invalid JSON in evidence file {path.name}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise EvidenceVerificationError(
            f"evidence file must contain an object: {path.name}"
        )
    return value


def require_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceVerificationError(f"{label} timestamp is missing")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceVerificationError(
            f"{label} timestamp is not ISO-8601"
        ) from exc


def require_returncode(record: dict[str, Any], label: str) -> int:
    value = record.get("returncode")
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvidenceVerificationError(f"{label} returncode is not an integer")
    return value


def require_command_timing(
    record: dict[str, Any],
    label: str,
) -> tuple[datetime, float]:
    started = require_timestamp(record.get("started_at"), f"{label} start")
    completed = require_timestamp(
        record.get("completed_at"),
        f"{label} completion",
    )
    timestamp = require_timestamp(record.get("timestamp"), label)
    if completed < started:
        raise EvidenceVerificationError(
            f"{label} completion precedes its start"
        )
    if timestamp != completed:
        raise EvidenceVerificationError(
            f"{label} timestamp differs from completed_at"
        )
    duration = record.get("duration_seconds")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration < 0
    ):
        raise EvidenceVerificationError(
            f"{label} duration_seconds is invalid"
        )
    return completed, float(duration)


def normalized_basename(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def validate_ansible_command(
    record: dict[str, Any],
    *,
    label: str,
    expected_playbook: str,
    expected_extra_vars: dict[str, str] | None,
) -> None:
    command = record.get("command")
    if not isinstance(command, list) or not all(
        isinstance(part, str) for part in command
    ):
        raise EvidenceVerificationError(f"{label} command is not a string list")

    expected_length = 6 if expected_extra_vars is not None else 4
    if len(command) != expected_length:
        raise EvidenceVerificationError(
            f"{label} command has an unexpected argument count"
        )
    if command[0:2] != ["ansible-playbook", "-i"]:
        raise EvidenceVerificationError(
            f"{label} did not invoke ansible-playbook with a fixed inventory"
        )
    if normalized_basename(command[2]) != ANSIBLE_INVENTORY.name:
        raise EvidenceVerificationError(f"{label} used the wrong inventory")
    if normalized_basename(command[3]) != expected_playbook:
        raise EvidenceVerificationError(f"{label} used the wrong playbook")

    if expected_extra_vars is None:
        return
    if command[4] != "--extra-vars":
        raise EvidenceVerificationError(f"{label} omitted --extra-vars")
    try:
        actual_extra_vars = json.loads(command[5])
    except json.JSONDecodeError as exc:
        raise EvidenceVerificationError(
            f"{label} extra vars are not valid JSON"
        ) from exc
    if actual_extra_vars != expected_extra_vars:
        raise EvidenceVerificationError(
            f"{label} extra vars do not match the approved profile"
        )


def validate_preflight(
    record: dict[str, Any],
) -> tuple[
    str,
    datetime,
    str,
    str,
    str,
    dict[str, dict[str, str]],
]:
    if record.get("stage") != "preflight":
        raise EvidenceVerificationError("preflight stage label is incorrect")
    timestamp = require_timestamp(record.get("timestamp"), "preflight")

    source_commit = record.get("source_commit")
    if not isinstance(source_commit, str) or not re.fullmatch(
        r"[0-9a-f]{40,64}", source_commit
    ):
        raise EvidenceVerificationError("preflight source commit is invalid")
    if record.get("lab_nodes") != list(LAB_NODES):
        raise EvidenceVerificationError("preflight lab-node set is not exact")

    checks = record.get("checks")
    if not isinstance(checks, list):
        raise EvidenceVerificationError("preflight checks are missing")
    expected_commands = [
        ["git", "rev-parse", "HEAD"],
        ["git", "status", "--porcelain"],
        ["ansible-playbook", "--version"],
        ["ansible-galaxy", "collection", "list", "community.docker"],
        ["clab", "version"],
        ["docker", "info"],
    ]
    expected_commands.extend(
        [
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}}|{{.Image}}|{{.Config.Image}}",
            node,
        ]
        for node in LAB_NODES
    )
    if len(checks) != len(expected_commands):
        raise EvidenceVerificationError(
            "preflight did not record every required command"
        )

    for index, (check, expected) in enumerate(
        zip(checks, expected_commands, strict=True)
    ):
        if not isinstance(check, dict):
            raise EvidenceVerificationError(
                f"preflight check {index} is not an object"
            )
        if check.get("command") != expected:
            raise EvidenceVerificationError(
                f"preflight command {index} is not the expected command"
            )
        if require_returncode(check, f"preflight check {index}") != 0:
            raise EvidenceVerificationError(
                f"preflight command {index} did not succeed"
            )
        require_command_timing(check, f"preflight check {index}")

    if (checks[0].get("stdout") or "").strip() != source_commit:
        raise EvidenceVerificationError(
            "preflight commit differs from git rev-parse evidence"
        )
    if (checks[1].get("stdout") or "").strip():
        raise EvidenceVerificationError(
            "live experiment started from a dirty worktree"
        )
    try:
        core_version = ansible_core_version(
            str(checks[2].get("stdout") or "")
        )
        collection_versions = docker_collection_versions(
            str(checks[3].get("stdout") or "")
        )
    except RuntimeError as exc:
        raise EvidenceVerificationError(str(exc)) from exc
    if core_version != EXPECTED_ANSIBLE_CORE_VERSION:
        raise EvidenceVerificationError(
            "preflight ansible-core version does not match the frozen profile"
        )
    if collection_versions != {EXPECTED_DOCKER_COLLECTION_VERSION}:
        raise EvidenceVerificationError(
            "preflight community.docker version does not match the frozen "
            "profile"
        )
    clab_version = " ".join(
        str(checks[4].get("stdout") or "").split()
    )
    if not clab_version:
        raise EvidenceVerificationError(
            "preflight Containerlab version is missing"
        )

    container_images: dict[str, dict[str, str]] = {}
    for node, check in zip(LAB_NODES, checks[6:], strict=True):
        try:
            container_images[node] = container_identity(
                str(check.get("stdout") or ""),
                node,
            )
        except RuntimeError as exc:
            raise EvidenceVerificationError(str(exc)) from exc
    if record.get("container_images") != container_images:
        raise EvidenceVerificationError(
            "preflight container-image summary differs from inspect evidence"
        )
    return (
        source_commit,
        timestamp,
        core_version,
        EXPECTED_DOCKER_COLLECTION_VERSION,
        clab_version,
        container_images,
    )


def validate_approval(
    record: dict[str, Any],
    *,
    source_commit: str,
    current_checksum: str,
    current_actions: list[dict[str, str]],
) -> datetime:
    if record.get("stage") != "human_approval":
        raise EvidenceVerificationError("approval stage label is incorrect")
    timestamp = require_timestamp(record.get("timestamp"), "approval")
    exact_values = {
        "decision": "APPROVED",
        "approval_token_required": APPROVAL_TOKEN,
        "repair_profile": REPAIR_PROFILE,
        "intent_bundle_sha256": current_checksum,
        "source_commit": source_commit,
        "approved_action_count": len(current_actions),
        "approved_actions": current_actions,
        "human_review_required": True,
        "automatic_deployment_by_llm": False,
        "deployment_engine": "ansible",
    }
    for field, expected in exact_values.items():
        if record.get(field) != expected:
            raise EvidenceVerificationError(
                f"approval field {field} does not match the live protocol"
            )
    bundle_name = str(record.get("intent_bundle", "")).replace("\\", "/")
    if bundle_name != "examples/topology_l_intent_bundle.json":
        raise EvidenceVerificationError(
            "approval is bound to an unexpected intent bundle"
        )
    return timestamp


def evidence_set_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for name in RAW_EVIDENCE_FILES:
        path = root / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def verify_evidence(
    evidence_dir: Path,
    *,
    intent_bundle: Path = INTENT_BUNDLE,
) -> dict[str, Any]:
    root = evidence_dir.resolve()
    if not root.is_dir():
        raise EvidenceVerificationError(
            f"evidence directory does not exist: {evidence_dir}"
        )

    missing = [name for name in RAW_EVIDENCE_FILES if not (root / name).is_file()]
    if missing:
        raise EvidenceVerificationError(
            f"required live evidence is missing: {', '.join(missing)}"
        )
    emergency = EvidencePaths(root).emergency_recovery
    if emergency.exists():
        raise EvidenceVerificationError(
            "emergency recovery evidence is present; this is not a normal "
            "successful run"
        )

    current_checksum = bundle_sha256(intent_bundle)
    current_actions = bundle_actions(intent_bundle)
    preflight = load_json_object(root / "00-preflight.json")
    (
        source_commit,
        preflight_time,
        ansible_core,
        docker_collection,
        clab_version,
        container_images,
    ) = validate_preflight(preflight)

    approval = load_json_object(root / "06-human-approval.json")
    approval_time = validate_approval(
        approval,
        source_commit=source_commit,
        current_checksum=current_checksum,
        current_actions=current_actions,
    )

    records: dict[str, dict[str, Any]] = {}
    stage_durations: dict[str, float] = {}
    times = [preflight_time]
    reconcile_vars = {
        "repair_profile": REPAIR_PROFILE,
        "approved_bundle_sha256": current_checksum,
    }
    fault_vars = {
        "fault_profile": FAULT_PROFILE,
        "fault_confirmation": FAULT_CONFIRMATION,
    }

    for filename, stage, playbook, outcome in STAGE_SPECS:
        record = load_json_object(root / filename)
        records[stage] = record
        if record.get("stage") != stage:
            raise EvidenceVerificationError(
                f"{filename} has the wrong stage label"
            )
        stage_time, stage_duration = require_command_timing(record, stage)
        times.append(stage_time)
        stage_durations[stage] = stage_duration
        returncode = require_returncode(record, stage)
        if outcome == "success" and returncode != 0:
            raise EvidenceVerificationError(f"{stage} did not succeed")
        if outcome == "failure" and returncode == 0:
            raise EvidenceVerificationError(
                "fault-time validation unexpectedly succeeded"
            )

        if playbook == RECONCILE_PLAYBOOK.name:
            extra_vars = reconcile_vars
        elif playbook == INJECT_PLAYBOOK.name:
            extra_vars = fault_vars
        else:
            extra_vars = None
        validate_ansible_command(
            record,
            label=stage,
            expected_playbook=playbook,
            expected_extra_vars=extra_vars,
        )

    times.insert(6, approval_time)
    if times != sorted(times):
        raise EvidenceVerificationError(
            "evidence timestamps are not in experiment order"
        )

    fault_stdout = str(records["fault_detection"].get("stdout", ""))
    if "FAIL:" not in fault_stdout:
        raise EvidenceVerificationError(
            "fault-time validation failed without an intent assertion"
        )

    idempotency_stdout = str(records["idempotency"].get("stdout", ""))
    changed = recap_changed_counts(idempotency_stdout)
    if changed != {"r1": 0, "r2": 0, "r3": 0}:
        raise EvidenceVerificationError(
            "idempotency evidence is not changed=0 on all three routers"
        )

    return {
        "verification_status": "PASS",
        "experiment": "week7-ansible-multifault",
        "evidence_directory": str(root),
        "evidence_set_sha256": evidence_set_sha256(root),
        "source_commit": source_commit,
        "ansible_core_version": ansible_core,
        "community_docker_version": docker_collection,
        "containerlab_version": clab_version,
        "router_image": EXPECTED_ROUTER_IMAGE,
        "host_image": EXPECTED_HOST_IMAGE,
        "container_images": container_images,
        "intent_bundle": "examples/topology_l_intent_bundle.json",
        "intent_bundle_sha256": current_checksum,
        "faults_verified": ["F1", "F2", "F3"],
        "fault_validation_returncode": require_returncode(
            records["fault_detection"], "fault_detection"
        ),
        "baseline_validation": "PASS",
        "human_approval": "APPROVED",
        "approved_action_count": len(current_actions),
        "automatic_deployment_by_llm": False,
        "approved_reconciliation": "PASS",
        "approved_reconciliation_duration_seconds": stage_durations[
            "approved_reconciliation"
        ],
        "post_repair_validation": "PASS",
        "post_repair_validation_duration_seconds": stage_durations[
            "post_repair_validation"
        ],
        "idempotency_changed": changed,
        "idempotency_duration_seconds": stage_durations["idempotency"],
        "recorded_automation_duration_seconds": round(
            sum(stage_durations.values()),
            6,
        ),
        "stage_durations_seconds": stage_durations,
        "emergency_recovery_present": False,
        "started_at": preflight_time.isoformat(),
        "approved_at": approval_time.isoformat(),
        "completed_at": times[-1].isoformat(),
    }


def render_macedonian_summary(summary: dict[str, Any]) -> str:
    changed = summary["idempotency_changed"]
    faults = ", ".join(summary["faults_verified"])
    return f"""# Верификуван резултат — Week 7 Ansible експеримент

**Статус на независната проверка: {summary["verification_status"]}**

- Git commit: `{summary["source_commit"]}`
- Ansible controller: `ansible-core {summary["ansible_core_version"]}`
- Docker connection collection: `community.docker {summary["community_docker_version"]}`
- Containerlab: `{summary["containerlab_version"]}`
- Router image: `{summary["router_image"]}`
- Endpoint image: `{summary["host_image"]}`
- SHA-256 на одобрениот intent пакет: `{summary["intent_bundle_sha256"]}`
- SHA-256 на комплетот со live докази: `{summary["evidence_set_sha256"]}`
- Потврдени грешки: {faults}
- Валидација по инјектирањето: очекувано неуспешна \
(return code {summary["fault_validation_returncode"]})
- Човечко одобрување: {summary["human_approval"]}
- Експлицитно прегледани акции: {summary["approved_action_count"]}
- Автоматска примена од LLM: не
- Усогласување со Ansible: {summary["approved_reconciliation"]}
- Време за одобреното усогласување: \
{summary["approved_reconciliation_duration_seconds"]:.3f} s
- Валидација по поправката: {summary["post_repair_validation"]}
- Време за валидација по поправката: \
{summary["post_repair_validation_duration_seconds"]:.3f} s
- Идемпотентност: `r1 changed={changed["r1"]}`, \
`r2 changed={changed["r2"]}`, `r3 changed={changed["r3"]}`
- Време за идемпотентното повторување: \
{summary["idempotency_duration_seconds"]:.3f} s
- Вкупно време на снимените автоматизирани фази: \
{summary["recorded_automation_duration_seconds"]:.3f} s
- Итно враќање во нормалниот успешен тек: не е активирано

Овој резултат се однесува на детерминистичкиот Ansible и валидациски слој.
Тој не претставува дополнителна евалуација на квалитетот на јазичниот модел.
"""


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def write_new_text(path: Path, text: str) -> None:
    if path.exists():
        raise EvidenceVerificationError(
            f"refusing to overwrite derived output: {path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_verification_outputs(
    summary: dict[str, Any],
    *,
    evidence_dir: Path,
    json_out: Path | None,
    markdown_out: Path | None,
) -> None:
    outputs = [path for path in (json_out, markdown_out) if path is not None]
    if len({path.resolve() for path in outputs}) != len(outputs):
        raise EvidenceVerificationError(
            "JSON and Markdown outputs must use different paths"
        )
    for path in outputs:
        if is_within(path, evidence_dir):
            raise EvidenceVerificationError(
                "derived verification output must not be written inside the "
                "raw evidence directory"
            )
    if json_out is not None:
        write_new_text(
            json_out,
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        )
    if markdown_out is not None:
        write_new_text(markdown_out, render_macedonian_summary(summary))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a Week 7 larger-topology live evidence directory"
    )
    parser.add_argument("evidence_dir", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    args = parser.parse_args()

    try:
        summary = verify_evidence(args.evidence_dir)
        write_verification_outputs(
            summary,
            evidence_dir=args.evidence_dir,
            json_out=args.json_out,
            markdown_out=args.markdown_out,
        )
    except EvidenceVerificationError as exc:
        print(f"EVIDENCE VERIFICATION FAILED: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
