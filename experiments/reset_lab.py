#!/usr/bin/env python3
"""experiments/reset_lab.py — restore the laboratory to its intended state.

Deterministically reasserts interface addressing, static routes and the access
policy from intent/intended_state.yaml, then verifies the result with the
independent runtime validator.

Use this after an interactive console/dashboard session, or whenever an
experiment aborts with a dirty baseline. Unlike policies/apply-policy.sh, this
also removes rules and routes that should NOT be there — an experiment can
leave an ACCEPT rule sitting above the required DROP, which apply-policy.sh
will not detect because the DROP itself is still present.

Usage:
    python3 experiments/reset_lab.py
    python3 experiments/reset_lab.py --check     # report state, change nothing
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments"))

from live_freeform_ablation import (  # noqa: E402
    classify,
    deep_reset,
    load_intent,
    reset_and_verify,
    run_validator,
)

DEFAULT_INTENT = REPO_ROOT / "intent" / "intended_state.yaml"
SCRATCH = REPO_ROOT / "docs" / "evidence" / "_reset-scratch"


def report(outcome: dict) -> None:
    print(f"VERDICT: {outcome['verdict']}")
    if outcome["failed_checks"]:
        print(f"  failed checks: {', '.join(outcome['failed_checks'])}")
    if outcome["security_policy_breached"]:
        print("  *** SECURITY POLICY BREACHED — client can reach management ***")
    if outcome["connectivity_broken"]:
        print("  *** REQUIRED CONNECTIVITY BROKEN ***")
    if outcome["deny_rule_removed"]:
        print("  *** DENY RULE DESTROYED ***")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", default=str(DEFAULT_INTENT))
    parser.add_argument("--check", action="store_true",
                        help="validate only; make no changes")
    parser.add_argument("--deep", action="store_true",
                        help="go straight to restarting the routing daemons")
    args = parser.parse_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    intent_path = Path(args.intent)
    intent = load_intent(intent_path)

    before = classify(run_validator(intent_path, SCRATCH / "before.json"))
    print("current state:")
    report(before)

    if args.check:
        return 0 if before["verdict"] == "MATCHES_INTENT" else 1

    if before["verdict"] == "MATCHES_INTENT":
        print("\nalready matches intent; nothing to do.")
        return 0

    if args.deep:
        print("\ndeep reset: restarting the routing daemons ...")
        for action in deep_reset(intent):
            print(f"  {action}")
        after = classify(run_validator(intent_path, SCRATCH / "after.json"))
        print()
        report(after)
        if after["verdict"] == "MATCHES_INTENT":
            print("\nlab restored — safe to run experiments.")
            return 0
        print("\nDEEP RESET FAILED — redeploy the laboratory:\n"
              "  sudo clab destroy -t topology.clab.yml --cleanup\n"
              "  sudo clab deploy -t topology.clab.yml\n"
              "  bash policies/apply-policy.sh && bash verify.sh",
              file=sys.stderr)
        return 1

    print("\nresetting ...")
    ok = reset_and_verify(intent, intent_path, SCRATCH / "after.json")
    after = classify(run_validator(intent_path, SCRATCH / "after.json"))
    print()
    report(after)

    if ok:
        print("\nlab restored — safe to run experiments.")
        return 0
    print("\nRESET FAILED. Redeploy the laboratory:\n"
          "  sudo clab destroy -t topology.clab.yml --cleanup\n"
          "  sudo clab deploy -t topology.clab.yml\n"
          "  bash policies/apply-policy.sh && bash verify.sh",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
