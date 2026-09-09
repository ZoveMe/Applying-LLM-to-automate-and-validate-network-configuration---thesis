#!/usr/bin/env python3
"""experiments/live_topology_analysis.py — EXPLAIN against the LIVE network.

The Week 6 EXPLAIN campaign asked models to describe a stored configuration
file. This asks them to describe the network as the routers actually report
it right now: routes read from vtysh, rules read from iptables.

The scoring question is the one that separated the models before: does the
description mention the access restriction at all? Routing is the easy half;
the deny rule is the half that three of twelve models silently omitted.

Each answer is scored deterministically against intent/intended_state.yaml —
which the model never sees — on four axes:

    mentions_deny        the access restriction is described at all
    correct_direction    described as client -> management, not reversed
    routes_covered       fraction of intended route facts described
    hallucinated         addresses or prefixes outside the lab inventory

Reads the live lab; changes nothing. Deploys nothing.

Usage:
    python3 experiments/live_topology_analysis.py                 # all models
    python3 experiments/live_topology_analysis.py --models mistral:7b-instruct-q4_K_M
    python3 experiments/live_topology_analysis.py --reps 3 --out docs/evidence/week8-live-topology
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments"))

from live_freeform_ablation import (  # noqa: E402
    OLLAMA_URL,
    call_ollama,
    load_intent,
    run_in,
)

MODELS_FILE = REPO_ROOT / "benchmarks" / "models_13.txt"
DEFAULT_INTENT = REPO_ROOT / "intent" / "intended_state.yaml"
DEVICES = ("r1", "r2")

# NEUTRAL PROMPT. It must not name access rules, policy or restrictions:
# naming them cues every model to mention them and destroys the measurement.
# The question is what the model considers worth reporting unprompted.
PROMPT = """You are a network engineer reviewing a small laboratory network.
Below is the live state read from each router.

{inventory}

Segments: 10.0.1.0/24 client (on r1), 10.0.2.0/24 server (on r2),
10.0.99.0/24 management (on r2), 10.0.12.0/30 transit between r1 and r2.

In at most six sentences, describe this network for a colleague who has not
seen it before. Plain prose, no commands, no bullet lists."""


# --------------------------------------------------------------- live read
def read_live_state() -> dict:
    state = {}
    for device in DEVICES:
        routes = run_in(device, "vtysh -c 'show ip route static'")
        rules = run_in(device, "iptables -S FORWARD")
        state[device] = {
            "routes": routes.stdout.strip() or "(none)",
            "firewall": rules.stdout.strip() or "(none)",
        }
    return state


def build_inventory(state: dict) -> str:
    return "\n\n".join(
        f"=== {dev} ===\nstatic routes:\n{state[dev]['routes']}\n"
        f"forwarding rules:\n{state[dev]['firewall']}"
        for dev in sorted(state)
    )


# ----------------------------------------------------------------- scoring
def ground_truth(intent: dict) -> dict:
    routes = [(f["node"], f["prefix"], f["via"])
              for f in intent["config_facts"] if f["kind"] == "route"]
    deny = [(r["src"], r["dst"]) for r in intent["policy_rules"]["must_deny"]]
    segments = intent["segments"]
    inventory = {s["subnet"] for s in segments.values()}
    inventory |= {s["gateway"] for s in segments.values()}
    inventory |= {s["host"] for s in segments.values()}
    transit = intent["transit"]
    inventory |= {transit["subnet"], transit["r1"], transit["r2"]}
    return {"routes": routes, "deny": deny, "inventory": inventory}


IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b")
DENY_WORDS = ("deny", "denied", "block", "blocked", "blocking", "drop",
              "dropped", "restrict", "restricted", "prevent", "prohibit",
              "not permitted", "cannot reach", "no access", "forbidden",
              "isolat")


def score(text: str, truth: dict) -> dict:
    lower = text.lower()

    mentions_deny = any(w in lower for w in DENY_WORDS)

    # Correct direction: the restriction runs client -> management.
    correct_direction = False
    if mentions_deny:
        # Split on sentence boundaries only: a period followed by whitespace.
        # Splitting on every "." would shred IP addresses into fragments.
        for sentence in re.split(r"[.;!?]\s+|\n", lower):
            if not any(w in sentence for w in DENY_WORDS):
                continue
            has_client = "10.0.1." in sentence or "client" in sentence
            has_mgmt = "10.0.99." in sentence or "management" in sentence or "mgmt" in sentence
            if has_client and has_mgmt:
                ci = min([sentence.find(x) for x in ("10.0.1.", "client")
                          if x in sentence] or [10**6])
                mi = min([sentence.find(x) for x in ("10.0.99.", "management", "mgmt")
                          if x in sentence] or [10**6])
                if ci < mi:
                    correct_direction = True
                    break

    # Route coverage: is each intended destination segment described as
    # reachable? Prose descriptions name segments, not route tuples, so
    # requiring the next-hop literal would measure formatting, not grounding.
    NAMES = {"10.0.1.": ("client",), "10.0.2.": ("server", "web"),
             "10.0.99.": ("management", "mgmt")}
    destinations = {prefix for _n, prefix, _v in truth["routes"]}
    covered = 0
    for prefix in destinations:
        stem = prefix.split("/")[0].rsplit(".", 1)[0] + "."
        if stem in text or any(n in lower for n in NAMES.get(stem, ())):
            covered += 1
    routes_covered = round(covered / len(destinations), 3) if destinations else 1.0

    hallucinated = sorted({
        m for m in IPV4.findall(text)
        if m not in truth["inventory"]
        and m.split("/")[0] not in truth["inventory"]
        and not any(m.startswith(p.rsplit(".", 1)[0]) for p in truth["inventory"])
    })

    return {
        "mentions_deny": mentions_deny,
        "correct_direction": correct_direction,
        "routes_covered": routes_covered,
        "hallucinated": hallucinated,
        "sentences": len([s for s in re.split(r"[.!?]", text) if s.strip()]),
        "chars": len(text),
    }


def load_models(names: list[str] | None) -> list[str]:
    if names:
        return names
    return [line.strip() for line in MODELS_FILE.read_text().splitlines()
            if line.strip() and not line.startswith("#")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="*")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--intent", default=str(DEFAULT_INTENT))
    parser.add_argument("--out", default=str(
        REPO_ROOT / "docs/evidence/week8-live-topology"))
    args = parser.parse_args()

    intent = load_intent(Path(args.intent))
    truth = ground_truth(intent)
    models = load_models(args.models)

    print("reading live router state ...")
    state = read_live_state()
    if all(v["routes"] == "(none)" for v in state.values()):
        print("ERROR: no routes read from the routers. Is the lab deployed?\n"
              "  sudo clab deploy -t topology.clab.yml --reconfigure",
              file=sys.stderr)
        return 2
    inventory = build_inventory(state)
    print(f"models: {len(models)}  reps: {args.reps}  "
          f"=> {len(models) * args.reps} runs\n")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "live-state.txt").write_text(inventory + "\n")

    records = []
    for model in models:
        for rep in range(1, args.reps + 1):
            started = time.monotonic()
            try:
                text = call_ollama(PROMPT.format(inventory=inventory), model)
            except Exception as exc:  # noqa: BLE001
                print(f"  {model:<44} ERROR {type(exc).__name__}")
                records.append({"model": model, "rep": rep,
                                "error": f"{type(exc).__name__}: {exc}"})
                continue
            latency = round(time.monotonic() - started, 2)
            result = score(text, truth)
            records.append({"model": model, "rep": rep, "answer": text.strip(),
                            "latency_s": latency, **result})
            flag = "OK " if result["mentions_deny"] else "OMITS POLICY"
            direction = "" if not result["mentions_deny"] else (
                "" if result["correct_direction"] else "  (direction unclear)")
            print(f"  {model:<44} routes={result['routes_covered']:.2f}  "
                  f"{flag}{direction}  {latency}s")

    # aggregate per model
    agg = {}
    for r in records:
        if "error" in r:
            continue
        a = agg.setdefault(r["model"], {"runs": 0, "deny": 0, "dir": 0,
                                        "routes": 0.0, "halluc": 0})
        a["runs"] += 1
        a["deny"] += bool(r["mentions_deny"])
        a["dir"] += bool(r["correct_direction"])
        a["routes"] += r["routes_covered"]
        a["halluc"] += len(r["hallucinated"])
    for a in agg.values():
        a["deny_rate"] = round(a["deny"] / a["runs"], 3)
        a["direction_rate"] = round(a["dir"] / a["runs"], 3)
        a["mean_routes_covered"] = round(a["routes"] / a["runs"], 3)

    summary = {
        "experiment": "live_topology_analysis_v1",
        "generated": datetime.now().isoformat(timespec="seconds"),
        "ollama_url": OLLAMA_URL,
        "note": ("EXPLAIN task run against the live network state rather than "
                 "a stored configuration file. Scored against intent, which "
                 "the model never sees."),
        "models": len(agg),
        "runs": len([r for r in records if "error" not in r]),
        "models_omitting_policy": sorted(
            m for m, a in agg.items() if a["deny_rate"] == 0.0),
        "aggregate": agg,
        "records": records,
    }
    (out_dir / "live-topology-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n")

    print("\n=== LIVE TOPOLOGY ANALYSIS ===")
    print(f"{'model':<44}{'deny':>7}{'dir':>7}{'routes':>9}{'halluc':>8}")
    for model in sorted(agg, key=lambda m: (agg[m]["deny_rate"], m)):
        a = agg[model]
        print(f"{model:<44}{a['deny_rate']:>7.2f}{a['direction_rate']:>7.2f}"
              f"{a['mean_routes_covered']:>9.2f}{a['halluc']:>8}")

    omitting = summary["models_omitting_policy"]
    if omitting:
        print(f"\nModels that never mentioned the access restriction "
              f"({len(omitting)} of {len(agg)}):")
        for m in omitting:
            print(f"  {m}")
        print("\nThese descriptions are fluent and structurally correct, and\n"
              "silently omit the security-relevant half of the configuration.")
    else:
        print("\nEvery model mentioned the access restriction.")

    print(f"\nSummary -> {out_dir / 'live-topology-summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
