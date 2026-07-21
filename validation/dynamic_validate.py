#!/usr/bin/env python3
"""dynamic_validate.py — deterministic POST-DEPLOYMENT validation.

Compares the ACTUAL state of the running lab against the INTENDED state
defined in intent/intended_state.yaml, and emits a structured JSON report.
This is the upgraded, classified replacement for verify.sh.

Check types:
  reachability -> ping (or busybox-wget tcp) between containers
  config_fact  -> iptables rule presence, kernel route presence

Usage:
    python3 validation/dynamic_validate.py
    python3 validation/dynamic_validate.py --out docs/evidence/manual-dynamic-check.json

Exit code: 0 = network MATCHES intent, 1 = does not match, 2 = lab not running.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

PKG_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTENT = PKG_ROOT / "intent" / "intended_state.yaml"
PREFIX = "clab-thesis-net-"



class InfrastructureError(RuntimeError):
    """Raised when the lab cannot be measured reliably."""


def sh(args):
    try:
        return subprocess.run(args, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise InfrastructureError(
            f"required command not found: {args[0]}"
        ) from exc


def command_detail(result) -> str:
    details = (result.stderr or result.stdout).strip()
    return details.splitlines()[0] if details else "no diagnostic output"


def require_docker() -> None:
    result = sh(["docker", "info"])
    if result.returncode != 0:
        raise InfrastructureError(
            f"Docker is unavailable: {command_detail(result)}"
        )


def probe_succeeded(result, description: str) -> bool:
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False

    raise InfrastructureError(
        f"{description} failed with exit code {result.returncode}: "
        f"{command_detail(result)}"
    )


def container_running(name: str) -> bool:
    result = sh([
        "docker", "inspect", "-f",
        "{{.State.Running}}", PREFIX + name
    ])
    return result.returncode == 0 and result.stdout.strip() == "true"


def ping(src: str, dst: str) -> bool:
    result = sh([
        "docker", "exec", PREFIX + src,
        "ping", "-c", "1", "-W", "2", dst
    ])
    return probe_succeeded(result, f"ping from {src} to {dst}")


def tcp(src: str, dst: str, port: int) -> bool:
    url = f"http://{dst}:{port}"
    result = sh([
        "docker", "exec", PREFIX + src,
        "wget", "-q", "-T", "2",
        "-O", "/dev/null", url
    ])
    return probe_succeeded(
        result,
        f"TCP check from {src} to {dst}:{port}",
    )


def iptables_rule_present(node: str, match: str) -> bool:
    result = sh([
        "docker", "exec", PREFIX + node,
        "iptables", "-C", "FORWARD",
        *match.split(),
    ])
    return probe_succeeded(
        result,
        f"iptables inspection on {node}",
    )


def route_lookup(node: str, prefix: str) -> str:
    result = sh([
        "docker", "exec", PREFIX + node,
        "ip", "route", "show", prefix
    ])

    if result.returncode != 0:
        raise InfrastructureError(
            f"route inspection on {node} failed: "
            f"{command_detail(result)}"
        )

    return result.stdout.strip()



def main() -> int:
    ap = argparse.ArgumentParser(description="Dynamic post-deployment validation")
    ap.add_argument("--intent", default=str(DEFAULT_INTENT))
    ap.add_argument("--out", help="write the JSON report to this path")
    args = ap.parse_args()

    require_docker()
    intent = yaml.safe_load(Path(args.intent).read_text())

    # guard: lab must be up
    for node in ("r1", "r2", "h-client", "h-server", "h-mgmt"):
        if not container_running(node):
            print(f"error: container {PREFIX}{node} is not running. "
                  f"Deploy the lab first: sudo clab deploy -t topology.clab.yml",
                  file=sys.stderr)
            return 2

    checks = []

    for item in intent["reachability"]:
        method = item.get("method", "ping")
        if method == "tcp":
            ok = tcp(item["src"], item["dst"], int(item.get("port", 80)))
            actual = "reachable" if ok else "unreachable"
        else:
            actual = "reachable" if ping(item["src"], item["dst"]) else "unreachable"
        checks.append(
            {
                "id": item["id"],
                "type": "reachability",
                "description": f"{item['src']} -> {item['dst']} ({item['why']})",
                "expected": item["expect"],
                "actual": actual,
                "result": "PASS" if actual == item["expect"] else "FAIL",
            }
        )

    for fact in intent["config_facts"]:
        if fact["kind"] == "iptables_rule":
            present = iptables_rule_present(fact["node"], fact["match"])
            checks.append(
                {
                    "id": fact["id"],
                    "type": "config_fact",
                    "description": f"iptables on {fact['node']}: {fact['match']} ({fact['why']})",
                    "expected": "present",
                    "actual": "present" if present else "absent",
                    "result": "PASS" if present else "FAIL",
                }
            )
        elif fact["kind"] == "route":
            out = route_lookup(fact["node"], fact["prefix"])
            ok = bool(out) and f"via {fact['via']}" in out
            actual = out if out else "route absent"
            checks.append(
                {
                    "id": fact["id"],
                    "type": "config_fact",
                    "description": f"route on {fact['node']}: {fact['prefix']} via {fact['via']} ({fact['why']})",
                    "expected": f"via {fact['via']}",
                    "actual": actual,
                    "result": "PASS" if ok else "FAIL",
                }
            )

    failed = sum(1 for c in checks if c["result"] == "FAIL")
    report = {
        "gate": "dynamic_post_deployment",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "verdict": "MATCHES_INTENT" if failed == 0 else "DOES_NOT_MATCH_INTENT",
        "summary": {"passed": len(checks) - failed, "failed": failed},
        "checks": checks,
    }

    for c in checks:
        print(f"{c['result']} | {c['id']:<3} | {c['type']:<12} | {c['description']}"
              f"  expected={c['expected']}  actual={c['actual']}")
    print("-" * 72)
    print(f"VERDICT: {report['verdict']}"
          f"  (passed={report['summary']['passed']}, failed={report['summary']['failed']})")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(f"report written to {out}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except InfrastructureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
