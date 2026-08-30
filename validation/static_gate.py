#!/usr/bin/env python3
"""static_gate.py — deterministic PRE-DEPLOYMENT validation gate.

Validates a proposed configuration suggestion (JSON file) BEFORE anything
touches the network. Three deterministic layers:

  1. schema   -> strict Pydantic validation (unknown fields rejected)
  2. topology -> every prefix / next-hop / subnet must exist in the lab
                 (catches hallucinated networks and impossible next-hops)
  3. policy   -> the suggestion must not contradict the intended access policy

Verdict is either REJECT (with machine-readable reasons) or
PASS_PENDING_HUMAN_APPROVAL. A pass NEVER deploys anything — it only makes
the suggestion eligible for review by the network engineer.

Usage:
    python3 validation/static_gate.py examples/suggestion_good.json
    python3 validation/static_gate.py examples/suggestion_bad_route.json --out evidence/gate_bad_route.json

Exit code: 0 = pass (pending approval), 1 = reject, 2 = usage error.
"""
import argparse
import ipaddress
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema import (ConfigSuggestion, devices_in_intent,  # noqa: E402
                    suggestion_model_for)

PKG_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTENT = PKG_ROOT / "intent" / "intended_state.yaml"


def load_intent(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def transit_links(intent: dict) -> list[tuple[str, dict[str, str]]]:
    """(subnet, {router: address}) for every transit link in the intent.

    Two shapes are in use and both must work, otherwise the gate silently
    knows nothing about a lab's transits — and a gate that knows no next hops
    rejects every route, which looks like the model failing:

        transit:  {subnet: ..., r1: ..., r2: ...}          one link
        transits: [{subnet: ..., ips: {r1: ..., r2: ...}}]  a list
    """
    links: list[tuple[str, dict[str, str]]] = []
    for item in intent.get("transits", []):
        links.append((item["subnet"], dict(item.get("ips", {}))))
    single = intent.get("transit")
    if isinstance(single, dict):
        links.append((single["subnet"],
                      {k: v for k, v in single.items() if k != "subnet"}))
    if not links:
        raise ValueError(
            "intent declares no transit link: the gate would have no valid "
            "next hop for any router and would reject every route")
    return links


def known_networks(intent: dict) -> dict:
    nets = {
        name: ipaddress.ip_network(seg["subnet"])
        for name, seg in intent["segments"].items()
    }
    for index, (subnet, _) in enumerate(transit_links(intent)):
        # The single-transit lab named this key exactly "transit"; keeping that
        # name for the first link leaves its reports unchanged.
        key = "transit" if index == 0 else f"transit{index + 1}"
        nets[key] = ipaddress.ip_network(subnet)
    return nets


def valid_next_hops(intent: dict) -> dict[str, set[str]]:
    """The directly connected peer addresses of each router.

    Derived from the transit links rather than hard-coded for r1 and r2, so a
    lab with five routers and six links is constrained just as tightly.
    """
    hops: dict[str, set[str]] = {}
    for _, ips in transit_links(intent):
        for router in ips:
            hops.setdefault(router, set()).update(
                str(ipaddress.ip_address(addr))
                for peer, addr in ips.items() if peer != router
            )
    return hops


def directly_connected_networks(intent: dict) -> dict[str, set]:
    """Return the networks attached directly to each router."""
    connected: dict[str, set] = {}
    for subnet, ips in transit_links(intent):
        network = ipaddress.ip_network(subnet)
        for router in ips:
            connected.setdefault(router, set()).add(network)

    for segment in intent["segments"].values():
        node = segment["connected_to"]
        connected.setdefault(node, set()).add(
            ipaddress.ip_network(segment["subnet"])
        )

    return connected


def is_within_known_network(network, known: dict) -> bool:
    """Accept an exact network or a subnet inside a declared lab network."""
    return any(
        network.version == candidate.version
        and network.subnet_of(candidate)
        for candidate in known.values()
    )


def traffic_overlaps(rule, intended_pair: dict) -> bool:
    """Return True when a proposed ACL rule intersects an intended pair."""
    try:
        proposed_src = ipaddress.ip_network(rule.src)
        proposed_dst = ipaddress.ip_network(rule.dst)
        intended_src = ipaddress.ip_network(intended_pair["src"])
        intended_dst = ipaddress.ip_network(intended_pair["dst"])
    except ValueError:
        return False

    same_versions = (
        proposed_src.version == intended_src.version
        and proposed_dst.version == intended_dst.version
    )

    return (
        same_versions
        and proposed_src.overlaps(intended_src)
        and proposed_dst.overlaps(intended_dst)
    )


class Gate:
    def __init__(self, intent: dict):
        self.intent = intent
        # Договорот се гради од уредите на оваа лабораторија. За основната
        # тоа дава ист список како замрзнатиот, па нејзиното однесување
        # останува непроменето.
        self.model = suggestion_model_for(devices_in_intent(intent))
        self.nets = known_networks(intent)
        self.hops_by_node = valid_next_hops(intent)
        self.connected_by_node = directly_connected_networks(intent)
        self.checks = []

    def record(self, category: str, description: str, ok: bool, detail: str = ""):
        self.checks.append(
            {
                "category": category,  # schema | topology | policy
                "description": description,
                "result": "PASS" if ok else "FAIL",
                "detail": detail,
            }
        )
        return ok

    # ---------- layer 1: schema ----------
    def check_schema(self, raw: str):
        try:
            suggestion = self.model.model_validate_json(raw)
            self.record("schema", "suggestion conforms to ConfigSuggestion schema", True)
            return suggestion
        except ValidationError as e:
            self.record(
                "schema",
                "suggestion conforms to ConfigSuggestion schema",
                False,
                detail=str(e),
            )
            return None

    # ---------- layer 2: topology ----------
    def check_topology(self, s: ConfigSuggestion):
        for r in s.static_routes:
            label = f"route on {r.node}: {r.prefix} via {r.next_hop}"
            try:
                prefix = ipaddress.ip_network(r.prefix)
            except ValueError:
                self.record("topology", label, False, "prefix is not a valid network")
                continue
            if prefix not in self.nets.values():
                self.record(
                    "topology",
                    label,
                    False,
                    "destination network does not exist in the lab (possible hallucination)",
                )
                continue
            if prefix in self.connected_by_node[r.node]:
                self.record(
                    "topology",
                    label,
                    False,
                    f"{prefix} is directly connected to {r.node}; "
                    "a static route is nonsensical",
                )
                continue
            try:
                hop = str(ipaddress.ip_address(r.next_hop))
            except ValueError:
                self.record("topology", label, False, "next-hop is not a valid IP address")
                continue
            allowed_hops =self.hops_by_node[r.node]
            if hop not in allowed_hops:
                self.record(
                    "topology",
                    label,
                    False,
                    f"next-hop {hop} is not a valid directly connected peer for "
                    f"{r.node}; allowed: {', '.join(sorted(allowed_hops))}",
                )
                continue
            self.record("topology", label, True)

        for a in s.access_policy:
            label = f"acl on {a.node}: {a.action} {a.src} -> {a.dst}"
            ok = True
            for field, value in (("src", a.src), ("dst", a.dst)):
                try:
                    net = ipaddress.ip_network(value)
                    if not is_within_known_network(net, self.nets):
                        ok = self.record(
                            "topology", label, False,
                            f"{field} subnet {value} is outside the declared lab networks",
                        )
                        break
                except ValueError:
                    ok = self.record(
                        "topology", label, False, f"{field} is not a valid subnet"
                    )
                    break
            if ok:
                self.record("topology", label, True)

    # ---------- layer 3: policy ----------
    def check_policy(self, s: ConfigSuggestion):
        # internal contradiction: same src/dst with both permit and deny
        seen = {}
        for a in s.access_policy:
            key = (a.node, a.src, a.dst)
            if key in seen and seen[key] != a.action:
                self.record(
                    "policy",
                    f"internally consistent rules for {a.src} -> {a.dst} on {a.node}",
                    False,
                    "suggestion contains both permit and deny for the same traffic",
                )
            seen[key] = a.action

        for pair in self.intent["policy_rules"]["must_deny"]:
            label = f"intent requires DENY {pair['src']} -> {pair['dst']}"
            violation = any(
                a.action == "permit" and traffic_overlaps(a, pair)
                for a in s.access_policy
            )
            self.record(
                "policy",
                label,
                not violation,
                "" if not violation else "suggestion PERMITS traffic the intent requires to be denied",
            )

        for pair in self.intent["policy_rules"]["must_allow"]:
            label = f"intent requires ALLOW {pair['src']} -> {pair['dst']}"
            violation = any(
                a.action == "deny" and traffic_overlaps(a, pair)
                for a in s.access_policy
            )
            self.record(
                "policy",
                label,
                not violation,
                "" if not violation else "suggestion DENIES traffic the intent requires to be allowed",
            )

    def run(self, raw: str, source: str) -> dict:
        suggestion = self.check_schema(raw)
        if suggestion is not None:
            self.check_topology(suggestion)
            self.check_policy(suggestion)
        failed = sum(1 for c in self.checks if c["result"] == "FAIL")
        verdict = "PASS_PENDING_HUMAN_APPROVAL" if failed == 0 else "REJECT"
        return {
            "gate": "static_pre_deployment",
            "suggestion_file": source,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "verdict": verdict,
            "summary": {
                "passed": sum(1 for c in self.checks if c["result"] == "PASS"),
                "failed": failed,
            },
            "checks": self.checks,
        }


def main() -> int:
    ap = argparse.ArgumentParser(description="Static pre-deployment validation gate")
    ap.add_argument("suggestion", help="path to a suggestion JSON file")
    ap.add_argument("--intent", default=str(DEFAULT_INTENT))
    ap.add_argument("--out", help="also write the JSON report to this path")
    args = ap.parse_args()

    sug_path = Path(args.suggestion)
    if not sug_path.exists():
        print(f"error: no such file: {sug_path}", file=sys.stderr)
        return 2

    gate = Gate(load_intent(Path(args.intent)))
    report = gate.run(sug_path.read_text(), str(sug_path))

    for c in report["checks"]:
        mark = "PASS" if c["result"] == "PASS" else "FAIL"
        line = f"{mark} | {c['category']:<8} | {c['description']}"
        if c["detail"]:
            line += f"  [{c['detail'].splitlines()[0][:110]}]"
        print(line)
    print("-" * 72)
    print(f"VERDICT: {report['verdict']}"
          f"  (passed={report['summary']['passed']}, failed={report['summary']['failed']})")
    if report["verdict"] == "PASS_PENDING_HUMAN_APPROVAL":
        print("NOTE: nothing was deployed. A human engineer must approve this change.")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(f"report written to {out}")

    return 0 if report["verdict"] == "PASS_PENDING_HUMAN_APPROVAL" else 1


if __name__ == "__main__":
    sys.exit(main())
