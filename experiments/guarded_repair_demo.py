#!/usr/bin/env python3
"""Reusable guarded repair demonstration for the thesis Containerlab.

The workflow deliberately separates proposal generation from deployment:

    baseline -> fault -> runtime detection -> Ollama proposal
             -> schema/policy gate -> exact-scope check -> human approval
             -> deployment -> runtime validation

The model never receives a command-execution interface. This orchestrator
passes one hard-coded, allow-listed repair to an idempotent Ansible playbook
only after the operator types the exact approval token. Any failure or
interruption restores the intended route through an independent recovery path.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from llm.ollama_client import DEFAULT_MODEL, OLLAMA_URL, run_pipeline  # noqa: E402


DYNAMIC_VALIDATOR = REPO_ROOT / "validation" / "dynamic_validate.py"
INTENT_FILE = REPO_ROOT / "intent" / "intended_state.yaml"
PROMPT_TEMPLATE = REPO_ROOT / "llm" / "prompt_template.txt"
TOPOLOGY_FILE = REPO_ROOT / "topology.clab.yml"
ANSIBLE_INVENTORY = REPO_ROOT / "ansible" / "inventory.yml"
ANSIBLE_REPAIR_PLAYBOOK = REPO_ROOT / "ansible" / "repair_route.yml"
EXPECTED_RUNTIME_CHECKS = 7
DEFAULT_REQUEST = (
    "The route from router r1 to the server network 10.0.2.0/24 is "
    "missing. Restore it using the transit link."
)
ROUTERS = ("clab-thesis-net-r1", "clab-thesis-net-r2")
LAB_NODES = (
    "clab-thesis-net-r1",
    "clab-thesis-net-r2",
    "clab-thesis-net-h-client",
    "clab-thesis-net-h-server",
    "clab-thesis-net-h-mgmt",
)
APPROVAL_TOKEN = "APPROVE"


class DemoError(RuntimeError):
    """Raised when the guarded workflow must stop safely."""


class ApprovalDeclined(DemoError):
    """Raised when the operator does not provide the exact approval token."""


@dataclass(frozen=True)
class RepairRoute:
    node: str = "r1"
    prefix: str = "10.0.2.0/24"
    next_hop: str = "10.0.12.2"

    @property
    def container(self) -> str:
        return f"clab-thesis-net-{self.node}"

    def as_dict(self) -> dict:
        return {
            "node": self.node,
            "prefix": self.prefix,
            "next_hop": self.next_hop,
        }


EXPECTED_ROUTE = RepairRoute()


@dataclass(frozen=True)
class EvidencePaths:
    root: Path

    @property
    def before(self) -> Path:
        return self.root / "01-fault-detected.json"

    @property
    def proposal(self) -> Path:
        return self.root / "02-ollama-proposal.json"

    @property
    def approval(self) -> Path:
        return self.root / "03-human-approval.json"

    @property
    def deployment(self) -> Path:
        return self.root / "04-ansible-deployment.json"

    @property
    def after(self) -> Path:
        return self.root / "05-repair-validated.json"


CommandRunner = Callable[..., subprocess.CompletedProcess]
ApprovalReader = Callable[[str], str]
Pipeline = Callable[..., dict]
Sleeper = Callable[[float], None]
OllamaProbe = Callable[[str, str], None]


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return REPO_ROOT / "docs" / "evidence" / "guarded-repair-runs" / stamp


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
    path.write_text(json.dumps(payload, indent=2) + "\n")


def command_detail(result: subprocess.CompletedProcess) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    return detail.splitlines()[0] if detail else "no diagnostic output"


def require_success(
    result: subprocess.CompletedProcess,
    description: str,
) -> None:
    if result.returncode != 0:
        raise DemoError(
            f"{description} failed with exit code {result.returncode}: "
            f"{command_detail(result)}"
        )


def vtysh_args(route: RepairRoute, action: str) -> list[str]:
    if action == "remove":
        commands = [
            "configure terminal",
            f"no ip route {route.prefix} blackhole",
            f"no ip route {route.prefix} {route.next_hop}",
        ]
    elif action == "restore":
        commands = [
            "configure terminal",
            f"no ip route {route.prefix} blackhole",
            f"no ip route {route.prefix} {route.next_hop}",
            f"ip route {route.prefix} {route.next_hop}",
        ]
    else:
        raise ValueError(f"unsupported route action: {action}")

    args = ["docker", "exec", route.container, "vtysh"]
    for command in commands:
        args.extend(["-c", command])
    return args


def prepare_vtysh(
    runner: CommandRunner,
) -> None:
    for router in ROUTERS:
        result = runner(
            [
                "docker",
                "exec",
                router,
                "sh",
                "-c",
                "mkdir -p /etc/frr && "
                "touch /etc/frr/vtysh.conf && "
                "chmod 0644 /etc/frr/vtysh.conf",
            ],
            capture_output=True,
        )
        require_success(result, f"vtysh preparation on {router}")


def change_route(
    route: RepairRoute,
    action: str,
    runner: CommandRunner,
) -> None:
    result = runner(vtysh_args(route, action), capture_output=True)
    require_success(result, f"route {action} on {route.node}")


def ansible_playbook_args(route: RepairRoute) -> list[str]:
    approved_scope = json.dumps(
        {
            "repair_node": route.node,
            "repair_prefix": route.prefix,
            "repair_next_hop": route.next_hop,
        },
        sort_keys=True,
    )
    return [
        "ansible-playbook",
        "-i",
        str(ANSIBLE_INVENTORY),
        str(ANSIBLE_REPAIR_PLAYBOOK),
        "--extra-vars",
        approved_scope,
    ]


def deploy_route_with_ansible(
    route: RepairRoute,
    runner: CommandRunner,
) -> subprocess.CompletedProcess:
    result = runner(ansible_playbook_args(route), capture_output=True)
    require_success(result, "Ansible approved-route deployment")
    return result


def run_runtime_validation(
    runner: CommandRunner,
    output: Path | None = None,
) -> int:
    command = [sys.executable, str(DYNAMIC_VALIDATOR)]
    if output is not None:
        command.extend(["--out", str(output)])
    return runner(command, capture_output=False).returncode


def validate_runtime_report(
    path: Path,
    *,
    expected_verdict: str,
    expected_c2_result: str,
) -> dict:
    try:
        report = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoError(f"invalid runtime evidence file {path}: {exc}") from exc

    if report.get("verdict") != expected_verdict:
        raise DemoError(
            f"runtime report verdict mismatch: expected {expected_verdict}, "
            f"got {report.get('verdict')}"
        )

    checks = report.get("checks")
    if not isinstance(checks, list):
        raise DemoError("runtime report has no structured checks")
    c2_checks = [check for check in checks if check.get("id") == "C2"]
    if len(c2_checks) != 1:
        raise DemoError("runtime report must contain exactly one C2 route check")
    if c2_checks[0].get("result") != expected_c2_result:
        raise DemoError(
            "C2 route-check mismatch: expected "
            f"{expected_c2_result}, got {c2_checks[0].get('result')}"
        )

    if len(checks) != EXPECTED_RUNTIME_CHECKS:
        raise DemoError(
            "runtime report must contain exactly "
            f"{EXPECTED_RUNTIME_CHECKS} checks; got {len(checks)}"
        )

    summary = report.get("summary", {})
    passed = summary.get("passed")
    failed = summary.get("failed")
    measured_passed = sum(check.get("result") == "PASS" for check in checks)
    measured_failed = sum(check.get("result") == "FAIL" for check in checks)
    if passed != measured_passed or failed != measured_failed:
        raise DemoError(
            "runtime summary does not match the structured check results"
        )

    if expected_verdict == "MATCHES_INTENT" and failed != 0:
        raise DemoError(
            "MATCHES_INTENT report contains a non-zero failed-check count"
        )
    if expected_verdict == "MATCHES_INTENT" and (
        passed != EXPECTED_RUNTIME_CHECKS
    ):
        raise DemoError(
            "repaired runtime state did not pass all "
            f"{EXPECTED_RUNTIME_CHECKS} checks"
        )
    if expected_verdict == "DOES_NOT_MATCH_INTENT" and (
        not isinstance(failed, int) or failed < 1
    ):
        raise DemoError(
            "DOES_NOT_MATCH_INTENT report contains no failed checks"
        )

    return report


def check_ollama(url: str, model: str) -> None:
    endpoint = url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(endpoint, timeout=10) as response:
            payload = json.load(response)
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise DemoError(f"Ollama preflight failed at {endpoint}: {exc}") from exc

    installed = {
        item.get("name") or item.get("model")
        for item in payload.get("models", [])
        if isinstance(item, dict)
    }
    if model not in installed:
        raise DemoError(
            f"required Ollama model is not installed: {model}"
        )


def preflight(
    runner: CommandRunner,
    mock_path: Path | None,
    model: str,
    url: str,
    ollama_probe: OllamaProbe,
) -> None:
    for required in (
        TOPOLOGY_FILE,
        INTENT_FILE,
        PROMPT_TEMPLATE,
        DYNAMIC_VALIDATOR,
        ANSIBLE_INVENTORY,
        ANSIBLE_REPAIR_PLAYBOOK,
    ):
        if not required.is_file():
            raise DemoError(f"required project file is missing: {required}")
    if mock_path is not None and not mock_path.is_file():
        raise DemoError(f"mock proposal file is missing: {mock_path}")

    containerlab = runner(["containerlab", "version"], capture_output=True)
    require_success(containerlab, "Containerlab preflight")

    docker = runner(["docker", "info"], capture_output=True)
    require_success(docker, "Docker preflight")

    ansible = runner(["ansible-playbook", "--version"], capture_output=True)
    require_success(ansible, "Ansible preflight")

    if mock_path is None:
        ollama_probe(url, model)

    for container in LAB_NODES:
        result = runner(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            capture_output=True,
        )
        if result.returncode != 0 or result.stdout.strip() != "true":
            raise DemoError(f"required lab container is not running: {container}")

    prepare_vtysh(runner)

    baseline_rc = run_runtime_validation(runner)
    if baseline_rc != 0:
        raise DemoError(
            "baseline does not match intent; repair the lab before injecting "
            f"a new fault (validator exit={baseline_rc})"
        )


def validate_exact_repair(
    evidence: dict,
    route: RepairRoute = EXPECTED_ROUTE,
) -> dict:
    expected = route.as_dict()
    proposal = evidence.get("proposal")
    gate = evidence.get("gate_report")

    if evidence.get("outcome") != "ACCEPTED":
        raise DemoError(
            "proposal was not accepted by the guarded LLM pipeline: "
            f"{evidence.get('outcome')}"
        )
    if not isinstance(proposal, dict):
        raise DemoError("accepted evidence has no structured proposal")
    if proposal.get("decision") != "PROPOSE":
        raise DemoError("accepted evidence is not a PROPOSE decision")
    if proposal.get("reason_code") != "ACTIONABLE_CHANGE":
        raise DemoError("proposal does not use ACTIONABLE_CHANGE")
    if proposal.get("static_routes") != [expected]:
        raise DemoError(
            "proposal scope mismatch; expected exactly one allow-listed route"
        )
    if proposal.get("access_policy") != []:
        raise DemoError("proposal contains an unapproved access-policy change")
    if not isinstance(gate, dict):
        raise DemoError("proposal has no deterministic gate report")
    if gate.get("verdict") != "PASS_PENDING_HUMAN_APPROVAL":
        raise DemoError(
            "deterministic gate did not require pending human approval"
        )
    if gate.get("summary", {}).get("failed") != 0:
        raise DemoError("deterministic gate report contains failed checks")

    return proposal


def proposal_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def display_path(path: Path) -> Path:
    try:
        return path.relative_to(REPO_ROOT)
    except ValueError:
        return path


def source_commit(runner: CommandRunner) -> str:
    result = runner(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
    )
    require_success(result, "source-commit lookup")
    commit = result.stdout.strip()
    if not commit:
        raise DemoError("source-commit lookup returned an empty value")
    return commit


def make_approval_record(
    *,
    decision: str,
    route: RepairRoute,
    proposal_path: Path,
    commit: str,
    generation_mode: str,
) -> dict:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "decision": decision,
        "approval_scope": route.as_dict(),
        "proposal_file": str(display_path(proposal_path)),
        "proposal_sha256": proposal_sha256(proposal_path),
        "source_commit": commit,
        "generation_mode": generation_mode,
        "deployment_engine": "ansible",
        "automatic_deployment_by_llm": False,
    }


def make_deployment_record(
    *,
    route: RepairRoute,
    proposal_path: Path,
    result: subprocess.CompletedProcess,
) -> dict:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "deployment_engine": "ansible",
        "playbook": str(display_path(ANSIBLE_REPAIR_PLAYBOOK)),
        "inventory": str(display_path(ANSIBLE_INVENTORY)),
        "approved_scope": route.as_dict(),
        "proposal_sha256": proposal_sha256(proposal_path),
        "returncode": result.returncode,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
        "automatic_deployment_by_llm": False,
    }


def run_demo(
    *,
    output_dir: Path,
    requirement: str,
    model: str,
    retries: int,
    url: str,
    mock_path: Path | None = None,
    route: RepairRoute = EXPECTED_ROUTE,
    runner: CommandRunner = run_command,
    approval_reader: ApprovalReader = input,
    pipeline: Pipeline = run_pipeline,
    sleeper: Sleeper = time.sleep,
    ollama_probe: OllamaProbe = check_ollama,
) -> EvidencePaths:
    paths = EvidencePaths(output_dir)
    ensure_new_output_dir(paths.root)
    preflight(runner, mock_path, model, url, ollama_probe)
    paths.root.mkdir(parents=True)

    recovery_required = False
    try:
        print("\n=== 1. INJECT ALLOW-LISTED MISSING-ROUTE FAULT ===")
        recovery_required = True
        change_route(route, "remove", runner)
        sleeper(1)

        print("\n=== 2. DETECT FAULT WITH THE RUNTIME VALIDATOR ===")
        before_rc = run_runtime_validation(runner, paths.before)
        if before_rc != 1:
            raise DemoError(
                "expected the runtime validator to detect the injected fault "
                f"with exit code 1; got {before_rc}"
            )
        validate_runtime_report(
            paths.before,
            expected_verdict="DOES_NOT_MATCH_INTENT",
            expected_c2_result="FAIL",
        )

        print("\n=== 3. GENERATE AN UNTRUSTED REPAIR PROPOSAL ===")
        mock_raw = mock_path.read_text() if mock_path is not None else None
        try:
            evidence = pipeline(
                requirement,
                model=model,
                retries=retries,
                mock_raw=mock_raw,
                url=url,
            )
        except Exception as exc:
            raise DemoError(f"proposal generation failed: {exc}") from exc
        if not isinstance(evidence, dict):
            raise DemoError("proposal pipeline returned non-object evidence")
        write_json(paths.proposal, evidence)

        print("\n=== 4. ENFORCE SCHEMA, GATE, AND EXACT CHANGE SCOPE ===")
        proposal = validate_exact_repair(evidence, route)
        print("Rationale:", proposal["rationale"])
        print("Approved scope candidate:")
        print(json.dumps(route.as_dict(), indent=2))
        print("PASS: exactly one allow-listed route; no policy changes")

        print("\n=== 5. REQUIRE EXPLICIT HUMAN APPROVAL ===")
        answer = approval_reader(
            f"Type {APPROVAL_TOKEN} to deploy this exact change: "
        ).strip()
        decision = "APPROVED" if answer == APPROVAL_TOKEN else "NOT_APPROVED"
        record = make_approval_record(
            decision=decision,
            route=route,
            proposal_path=paths.proposal,
            commit=source_commit(runner),
            generation_mode="mock" if mock_path is not None else "live_ollama",
        )
        write_json(paths.approval, record)

        if decision != "APPROVED":
            raise ApprovalDeclined(
                "exact approval token not provided; no repair was deployed"
            )

        expected_hash = record["proposal_sha256"]
        if proposal_sha256(paths.proposal) != expected_hash:
            raise DemoError("proposal evidence changed after human approval")

        print("\n=== 6. DEPLOY THE APPROVED ROUTE WITH ANSIBLE ===")
        deployment = deploy_route_with_ansible(route, runner)
        write_json(
            paths.deployment,
            make_deployment_record(
                route=route,
                proposal_path=paths.proposal,
                result=deployment,
            ),
        )
        sleeper(1)

        print("\n=== 7. VALIDATE THE POST-DEPLOYMENT NETWORK ===")
        after_rc = run_runtime_validation(runner, paths.after)
        if after_rc != 0:
            raise DemoError(
                "post-deployment network does not match intent "
                f"(validator exit={after_rc})"
            )
        validate_runtime_report(
            paths.after,
            expected_verdict="MATCHES_INTENT",
            expected_c2_result="PASS",
        )

        recovery_required = False
        print("\n=== GUARDED REPAIR RESULT: PASS ===")
        print("Fault detected, proposal gated, human approval recorded,")
        print("Ansible deployed the route, and the network matches intent.")
        print(f"Evidence directory: {display_path(paths.root)}")
        return paths
    finally:
        if recovery_required:
            print("\nRestoring the intended route before exit...")
            try:
                change_route(route, "restore", runner)
                print("Recovery cleanup: OK")
            except DemoError as exc:
                print(
                    f"CRITICAL: automatic route recovery failed: {exc}",
                    file=sys.stderr,
                )
                raise DemoError(
                    "automatic route recovery failed; inspect r1 before reuse"
                ) from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Guarded Ollama-to-Containerlab repair demonstration"
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="new evidence directory; defaults to a timestamped path",
    )
    parser.add_argument("--request", default=DEFAULT_REQUEST)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--url", default=OLLAMA_URL)
    parser.add_argument(
        "--mock",
        type=Path,
        help=(
            "schema-valid canned model output; skips Ollama but still uses "
            "the live lab"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = args.out_dir or default_output_dir()
    if not output_dir.is_absolute():
        output_dir = REPO_ROOT / output_dir
    mock_path = args.mock
    if mock_path is not None and not mock_path.is_absolute():
        mock_path = REPO_ROOT / mock_path

    try:
        run_demo(
            output_dir=output_dir,
            requirement=args.request,
            model=args.model,
            retries=max(1, args.retries),
            url=args.url,
            mock_path=mock_path,
        )
    except ApprovalDeclined as exc:
        print(f"NOT APPROVED: {exc}", file=sys.stderr)
        return 1
    except DemoError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nINTERRUPTED: recovery was attempted.", file=sys.stderr)
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())
