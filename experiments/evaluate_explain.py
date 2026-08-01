#!/usr/bin/env python3
"""experiments/evaluate_explain.py — score preserved EXPLAIN outputs against intent.

Evidence generation (run_explain_experiments.py) is separated from
evaluation (this script), mirroring the V2 campaign design.

Ground truth is derived from intent/intended_state.yaml — the same single
source of truth used by both validators. The model never saw this file,
so agreement measures real factual grounding, not prompt echo.

Metrics per run:
    schema_valid       response parsed against the strict Pydantic schema
    route_recall       fraction of intended routes correctly explained
    route_precision    fraction of claimed routes that actually exist
    iface_recall       fraction of real interface addresses covered
    iface_precision    fraction of claimed addresses that are real
    policy_recall      fraction of real deny rules explained (None if device
                       has none — then phantom_policy_claims is the signal)
    phantom_policy     claimed deny rules that do NOT exist on that device;
                       a fabricated security control is the dangerous
                       direction of error (it tells the engineer a protection
                       exists when it does not)
    hallucinations     claimed prefixes/IPs outside the lab inventory

Usage:
    python3 experiments/evaluate_explain.py --evidence docs/evidence/week6-explain \
        [--out docs/evidence/week6-explain/explain-evaluation.json]
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
INTENT_PATH = REPO_ROOT / "intent" / "intended_state.yaml"


def ground_truth(intent_path: Path = INTENT_PATH) -> dict:
    intent = yaml.safe_load(Path(intent_path).read_text())
    segments = intent["segments"]

    # Transit links: legacy single "transit" mapping, or a "transits" list.
    if "transits" in intent:
        transit_links = [(t["subnet"], t["ips"]) for t in intent["transits"]]
    else:
        t = intent["transit"]
        transit_links = [(t["subnet"], {k: v for k, v in t.items() if k != "subnet"})]

    devices = sorted(
        {seg["connected_to"] for seg in segments.values()}
        | {node for _, ips in transit_links for node in ips}
    )

    routes = {d: set() for d in devices}
    for fact in intent["config_facts"]:
        if fact["kind"] == "route":
            routes[fact["node"]].add((fact["prefix"], fact["via"]))

    ifaces = {d: set() for d in devices}
    for seg in segments.values():
        ifaces[seg["connected_to"]].add(seg["gateway"])
    for _, ips in transit_links:
        for node, ip in ips.items():
            ifaces[node].add(ip)

    # Deny rules per node; legacy entries without "node" belong to r1.
    deny = {d: set() for d in devices}
    for rule in intent["policy_rules"]["must_deny"]:
        deny[rule.get("node", "r1")].add((rule["src"], rule["dst"]))

    inventory = {seg["subnet"] for seg in segments.values()}
    inventory |= {seg["gateway"] for seg in segments.values()}
    inventory |= {seg["host"] for seg in segments.values()}
    for subnet, ips in transit_links:
        inventory.add(subnet)
        inventory |= set(ips.values())

    return {"routes": routes, "ifaces": ifaces, "deny": deny, "inventory": inventory}


def norm_ip(value: str) -> str:
    return value.strip().split("/")[0]


def score_run(record: dict, truth: dict) -> dict:
    result = {
        "device": record["device"],
        "model": record["model"],
        "rep": record["rep"],
        "schema_valid": record["schema_valid"],
        "route_recall": 0.0,
        "route_precision": 0.0,
        "iface_recall": 0.0,
        "iface_precision": 0.0,
        "policy_recall": None,
        "phantom_policy_claims": 0,
        "hallucinations": [],
    }
    if not record["schema_valid"]:
        return result

    parsed = record["parsed"]
    device = record["device"]

    true_routes = truth["routes"][device]
    claimed_routes = {(r["prefix"].strip(), norm_ip(r["next_hop"])) for r in parsed["static_routes"]}
    hit_routes = claimed_routes & true_routes
    result["route_recall"] = round(len(hit_routes) / len(true_routes), 3) if true_routes else 1.0
    result["route_precision"] = round(len(hit_routes) / len(claimed_routes), 3) if claimed_routes else 0.0

    true_ifaces = truth["ifaces"][device]
    claimed_ifaces = {norm_ip(i["ip_address"]) for i in parsed["interfaces"]}
    hit_ifaces = claimed_ifaces & true_ifaces
    result["iface_recall"] = round(len(hit_ifaces) / len(true_ifaces), 3) if true_ifaces else 1.0
    result["iface_precision"] = round(len(hit_ifaces) / len(claimed_ifaces), 3) if claimed_ifaces else 0.0

    true_denies = truth["deny"].get(device, set())
    claimed_denies = {
        (r["src"].strip(), r["dst"].strip())
        for r in parsed["access_rules"]
        if r["action"] == "deny"
    }
    # Fabricated security controls are counted for EVERY device, including
    # devices with no deny rules at all — that is exactly the case where
    # policy_recall is undefined and would otherwise hide the error.
    result["phantom_policy_claims"] = len(claimed_denies - true_denies)
    if true_denies:
        result["policy_recall"] = round(len(claimed_denies & true_denies) / len(true_denies), 3)

    known = truth["inventory"]
    for r in parsed["static_routes"]:
        for value in (r["prefix"].strip(), norm_ip(r["next_hop"])):
            if value not in known:
                result["hallucinations"].append(value)
    for i in parsed["interfaces"]:
        if norm_ip(i["ip_address"]) not in known:
            result["hallucinations"].append(norm_ip(i["ip_address"]))

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--intent",
        default=str(INTENT_PATH),
        help="intent file used as ground truth (default: thesis-core intent)",
    )
    args = parser.parse_args()

    evidence_dir = Path(args.evidence)
    run_files = sorted(evidence_dir.rglob("EXPLAIN_*.json"))
    if not run_files:
        print(f"No EXPLAIN_*.json files in {evidence_dir}", file=sys.stderr)
        return 1

    truth = ground_truth(Path(args.intent))
    rows = [score_run(json.loads(f.read_text()), truth) for f in run_files]

    by_model: dict = {}
    for row in rows:
        by_model.setdefault(row["model"], []).append(row)

    aggregate = {}
    for model, model_rows in by_model.items():
        n = len(model_rows)
        valid = [r for r in model_rows if r["schema_valid"]]
        policy_rows = [r for r in valid if r["policy_recall"] is not None]
        aggregate[model] = {
            "runs": n,
            "schema_validity": round(len(valid) / n, 3),
            "mean_route_recall": round(sum(r["route_recall"] for r in valid) / len(valid), 3) if valid else 0.0,
            "mean_route_precision": round(sum(r["route_precision"] for r in valid) / len(valid), 3) if valid else 0.0,
            "mean_iface_recall": round(sum(r["iface_recall"] for r in valid) / len(valid), 3) if valid else 0.0,
            "mean_iface_precision": round(sum(r["iface_precision"] for r in valid) / len(valid), 3) if valid else 0.0,
            "mean_policy_recall": round(sum(r["policy_recall"] for r in policy_rows) / len(policy_rows), 3) if policy_rows else None,
            "total_phantom_policy_claims": sum(r["phantom_policy_claims"] for r in valid),
            "total_hallucinations": sum(len(r["hallucinations"]) for r in valid),
        }

    report = {"per_run": rows, "aggregate": aggregate}
    out_path = Path(args.out) if args.out else evidence_dir / "explain-evaluation.json"
    out_path.write_text(json.dumps(report, indent=2))

    print(json.dumps(aggregate, indent=2))
    print(f"\nFull report -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
