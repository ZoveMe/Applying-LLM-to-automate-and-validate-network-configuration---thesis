#!/usr/bin/env python3
"""
Recompute every headline number from the preserved evidence and check that the
thesis states it.

The point is direction of trust: figures are derived from docs/evidence/ and the
document is checked against them, never the other way round. Run this before
sending a draft anywhere.

Exit code 0 = document agrees with the evidence.

Run:  python3 tools/verify_thesis_claims.py
"""
import json
import re
import sys
from pathlib import Path

from docx import Document

REPO = Path("/sessions/ecstatic-pensive-volta/mnt/thesis-net")
EV = REPO / "docs" / "evidence"
DOC = REPO / "docs" / "Дипломска-работа-Митровски-222022.docx"

FAMILIES = ["codellama", "deepseek", "gemma", "granite",
            "llama", "mistral", "phi", "qwen"]


def family(model: str) -> str:
    m = model.lower()
    return next((f for f in FAMILIES if f in m), m)


def load(rel):
    return json.loads((EV / rel).read_text(encoding="utf-8"))


def derive():
    """Every number below is computed from evidence, never copied from prose."""
    v2 = load("week6-v2-12m/v2-12m-evaluation.json")
    deep = load("week6-v2-12m/v2-deep-analysis.json")
    abl = load("week8-live-ablation/live-ablation-summary.json")
    ff = load("week8-freeform/freeform-summary.json")
    gate = deep["gate_confusion_matrix"]

    models = {r["model"] for r in abl["runs"]} | set(deep.get("per_model", {}))
    t9 = [r for r in abl["runs"] if r["case"] == "T9"]
    t9_broken = sum(1 for r in t9
                    if (r["outcome"] or {}).get("verdict") == "DOES_NOT_MATCH_INTENT")

    resets = [json.loads(p.read_text(encoding="utf-8"))
              for p in sorted((EV / "week8-freeform").glob("reset-*.json"))]
    unrestored = sum(1 for r in resets if r["verdict"] != "MATCHES_INTENT")

    # the EXPLAIN campaign is three configuration packs scored separately
    explain_runs = sum(
        len(load(f"week6-explain-13m/{name}-evaluation.json")["per_run"])
        for name in ("small", "l-clean", "l-faulty")
    )

    return {
        "guarded runs": v2["runs_total"],
        "explain runs": explain_runs,
        "ablation proposals": abl["proposals_replayed"],
        "free-form runs": ff["runs"],
        "total live runs": v2["runs_total"] + explain_runs
                           + abl["proposals_replayed"] + ff["runs"],
        "model count": len(deep.get("per_model", {})) or len(models),
        "family count": len({family(m) for m in deep.get("per_model", {})}),
        "gate recall": gate["recall"],
        "gate false negatives": gate["false_negatives_violation_allowed"],
        "gated runs": gate["gated_runs"],
        "ablation not matching intent": abl["left_network_not_matching_intent"],
        "ablation policy breached": abl["security_policy_breached"],
        "ablation deny rule removed": abl["deny_rule_removed"],
        "ablation connectivity broken": abl["connectivity_broken"],
        "T9 repetitions": len(t9),
        "T9 broken": t9_broken,
        "T9 families": len({family(r["model"]) for r in t9}),
        "T9 distinct models": len({r["model"] for r in t9}),
        "free-form not matching intent": ff["left_network_not_matching_intent"],
        "free-form policy breached": ff["security_policy_breached"],
        "free-form deny removed": ff["deny_rule_removed"],
        "free-form connectivity broken": ff["connectivity_broken"],
        "free-form commands executed": ff["total_commands_executed"],
        "free-form rejected by device": ff["total_commands_rejected_by_device"],
        "free-form unrestored labs": unrestored,
    }


# Macedonian spells small numbers as words, so a figure may legitimately
# appear either way; both forms count as stating the number.
WORDS = {2: "две", 6: "шест", 8: "осум", 12: "дванаесет", 24: "дваесет и четири"}


def forms(n):
    out = [str(n)]
    if n in WORDS:
        out.append(WORDS[n])
    return out


# phrases the document must contain, keyed to the derived value
def expectations(d):
    """(list of accepted phrasings, label) — any one of them satisfies the check."""
    def variants(n, tail=""):
        return [f"{f} {tail}".strip() for f in forms(n)]

    return [
        (variants(d["total live runs"], "извршувања"), "total live runs"),
        (variants(d["gated runs"]), "gated runs"),
        (variants(d["ablation not matching intent"]), "ablation not matching intent"),
        (variants(d["ablation deny rule removed"], "случаи било отстрането правилото"),
         "ablation deny rule removed"),
        (variants(d["T9 repetitions"], "повторувања"), "T9 repetitions"),
        (variants(d["T9 distinct models"], "различни модели"), "T9 distinct models"),
        # „семејства" е редактираниот термин; „фамилии" се прифаќа за постари верзии
        (variants(d["T9 families"], "семејства") + variants(d["T9 families"], "фамилии"),
         "T9 families"),
        (variants(d["free-form not matching intent"], "ја оставија мрежата"),
         "free-form not matching intent"),
        (variants(d["free-form commands executed"], "извршени команди"),
         "free-form commands executed"),
        (variants(d["free-form rejected by device"], "беа одбиени"),
         "free-form rejected by device"),
        (variants(d["free-form unrestored labs"], "извршувања ја оставија "
                  "лабораториската околина"), "free-form unrestored labs"),
    ]


def main():
    derived = derive()
    text = re.sub(r"\s+", " ", "\n".join(p.text for p in Document(str(DOC)).paragraphs))

    print("Derived from evidence:")
    for k, v in derived.items():
        print(f"  {k:32} {v}")

    print("\nChecking the document states each figure:")
    failures = []
    for variants, label in expectations(derived):
        found = next((v for v in variants if v in text), None)
        print(f"  [{'ok ' if found else 'MISS'}] {label:32} "
              f"«{found or ' | '.join(variants)}»")
        if not found:
            failures.append((label, variants))

    # internal coherence: families must not be claimed as models
    for bad in ("дванаесет различни фамилии", "дванаесет различни семејства"):
        if bad in text:
            failures.append(("family/model conflation",
                             f"«{bad}» contradicts {derived['family count']} families"))
            print("  [MISS] family/model conflation")

    if failures:
        print(f"\nFAILED: {len(failures)} claim(s) not supported by the document")
        return 1
    print("\nAll checked claims agree with the evidence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
