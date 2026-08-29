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
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

PKG_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTENT = PKG_ROOT / "intent" / "intended_state.yaml"

# Container name prefix of the lab under test. The experiment scripts already
# honour CLAB_PREFIX, so the validator must too — otherwise pointing --intent at
# another lab silently measures the wrong containers, or fails confusingly.
# Overridden by --prefix.
PREFIX = os.environ.get("CLAB_PREFIX", "clab-thesis-net-")



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


PROTOCOL_CODES = {
    # Leading code in the routing table output of FRR, per protocol.
    "ospf": "O",
    "bgp": "B",
    "rip": "R",
    "isis": "I",
    "static": "S",
    "connected": "C",
    "kernel": "K",
}


def protocol_route_lookup(node: str, prefix: str) -> str:
    """The routing table entry as the routing daemon itself reports it.

    `ip route` shows what ended up in the kernel and loses the origin, so the
    daemon is asked directly — that is the only place the protocol is recorded.
    """
    result = sh([
        "docker", "exec", PREFIX + node,
        "vtysh", "-c", f"show ip route {prefix}"
    ])
    if result.returncode != 0:
        raise InfrastructureError(
            f"routing table inspection on {node} failed: "
            f"{command_detail(result)}"
        )
    return result.stdout


def route_learned_by(output: str, protocol: str) -> bool:
    """True when the prefix is present, from `protocol`, and actually selected.

    FRR prints two different shapes and both have to be handled:

    Asking for one prefix gives the detailed entry, where the protocol is named
    in words and the chosen entry is marked `best`::

        Routing entry for 10.10.10.0/24
          Known via "ospf", distance 110, metric 20, best
          * 10.0.0.6, via eth2, weight 1

    Asking for the whole table gives one line per route, where the protocol is a
    letter code and selection is marked `>`::

        O>* 10.10.10.0/24 [110/20] via 10.0.0.6, eth2, weight 1

    Selection matters as much as origin: a route that is known but lost to
    another protocol carries no traffic, so it must not satisfy the fact.
    """
    proto = protocol.lower()
    code = PROTOCOL_CODES.get(proto)
    if code is None:
        raise ValueError(f"unknown routing protocol in intent: {protocol!r}")

    for line in output.splitlines():
        stripped = line.strip()
        # detailed form
        if stripped.startswith("Known via"):
            named = f'known via "{proto}"' in stripped.lower()
            if named and "best" in stripped.lower():
                return True
        # table form
        elif stripped.startswith(f"{code}>"):
            return True
    return False


def stopped_lab_containers() -> list[str]:
    """Containers of this lab that exist but are not running."""
    result = sh([
        "docker", "ps", "-a", "--filter", f"name={PREFIX}",
        "--format", "{{.Names}}\t{{.State}}"
    ])
    if result.returncode != 0:
        raise InfrastructureError(
            f"listing lab containers failed: {command_detail(result)}")
    stopped = []
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        name, state = line.split("\t", 1)
        if state.strip() != "running":
            stopped.append(name.strip())
    return sorted(stopped)


def nodes_named_in(intent: dict) -> list[str]:
    """Every container this intent will run a command inside.

    Hosts appear as the source of a reachability check; routers appear as the
    node of a config fact or as the gateway of a segment. Destinations are
    addresses, not container names, so they are deliberately left out.
    """
    names: set[str] = set()
    for item in intent.get("reachability", []):
        names.add(item["src"])
    for fact in intent.get("config_facts", []):
        names.add(fact["node"])
    for seg in (intent.get("segments") or {}).values():
        if isinstance(seg, dict) and seg.get("connected_to"):
            names.add(seg["connected_to"])
    return sorted(names)


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
    global PREFIX                       # must precede any use of PREFIX below

    ap = argparse.ArgumentParser(description="Dynamic post-deployment validation")
    ap.add_argument("--intent", default=str(DEFAULT_INTENT))
    ap.add_argument("--out", help="write the JSON report to this path")
    ap.add_argument(
        "--prefix", default=PREFIX,
        help="container name prefix of the lab (default: $CLAB_PREFIX or "
             "clab-thesis-net-)",
    )
    args = ap.parse_args()
    PREFIX = args.prefix

    require_docker()
    intent = yaml.safe_load(Path(args.intent).read_text())

    # Guard: the nodes this intent names must exist and run. The list comes from
    # the intent, not from a fixed list, so another lab checks its own nodes.
    for node in nodes_named_in(intent):
        if not container_running(node):
            print(f"error: container {PREFIX}{node} is not running.\n"
                  f"       Deploy the lab that goes with {Path(args.intent).name}, "
                  f"and check that --prefix matches it\n"
                  f"       (currently {PREFIX!r}).",
                  file=sys.stderr)
            return 2

    # Guard: every other container of the same lab must run too. A host that
    # only ever appears as a destination is never exec'd into, so the loop above
    # cannot see it — yet if it were down, a check expecting "unreachable" would
    # pass for the wrong reason and hide a broken policy.
    stopped = stopped_lab_containers()
    if stopped:
        print(f"error: these containers of the lab are not running: "
              f"{', '.join(stopped)}.\n"
              f"       A stopped host makes an 'unreachable' check pass without "
              f"the policy doing anything.",
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
        elif fact["kind"] == "route" and "protocol" in fact:
            # Dynamic routing: the next hop is not fixed, because more than one
            # path may exist and the protocol chooses. What must hold is that
            # the prefix is present AND was learned by the declared protocol —
            # a static route left behind by hand would satisfy "reachable" while
            # proving nothing about whether the protocol actually converged.
            proto = fact["protocol"]
            out = protocol_route_lookup(fact["node"], fact["prefix"])
            ok = route_learned_by(out, proto)
            checks.append(
                {
                    "id": fact["id"],
                    "type": "config_fact",
                    "description": (
                        f"route on {fact['node']}: {fact['prefix']} learned via "
                        f"{proto.upper()} ({fact['why']})"
                    ),
                    "expected": f"present, protocol {proto}",
                    "actual": out.strip() or "route absent",
                    "result": "PASS" if ok else "FAIL",
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
