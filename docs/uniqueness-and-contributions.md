# What makes this thesis unique — contribution map

Written 27 July 2026. Purpose: state precisely what this work does that the published literature does not, so the Вовед, Заклучок, and defence answers can claim it accurately and defensibly.

---

## The four claims

### C1. Cryptographic binding of human approval to the deployed artifact

Human approval is bound to a SHA-256 digest of the exact proposal bytes, recomputed immediately before deployment; if the bytes change after approval, deployment stops.

**Why it is novel:** the three 2026 benchmarks for LLM-driven network management — Cornetto (arXiv:2604.22513), NetAgentBench (arXiv:2604.09678), Network Arena (arXiv:2512.16381) — evaluate *whether models produce correct configurations*. The IETF NMRG drafts (draft-cui-nmrg-llm-nm-01) specify human approval as a required stage but do not specify artifact integrity. None binds the authorization to an immutable artifact. Approval in those systems attaches to an *intention*; here it attaches to *bytes*.

**Defensible phrasing:** "To the best of our knowledge, no evaluated LLM-network-management system in the surveyed literature binds human authorization cryptographically to the specific artifact deployed."

### C2. Explanation faithfulness as a measured safety property

The literature measures whether models *generate* correct configurations. This thesis also measures whether models *describe existing* configurations faithfully — and scores that description deterministically against machine-readable intent the model never sees.

**The finding that makes it matter:** across 12 model families and 288 runs, structural description was essentially perfect (100% schema validity, ~100% route and interface recall/precision, **zero hallucinated entities**), while **three models never reported a single access-control rule in 24 runs each**, and two more did so inconsistently. Five of twelve models would hand an engineer a fluent, structurally complete, and security-blind account of a device.

**Why it is novel:** configuration *explanation* is an assistive role that operators actually use (understanding inherited configs), and no benchmark in the surveyed literature scores it for factual grounding, let alone for security-relevant omission. The failure mode is invisible to a reader — the explanation looks complete.

### C3. Cross-task consistency analysis (EXPLAIN ↔ PROPOSE)

The same 12 models are measured on both assistive roles against the same intent file, and the results are joined per model to test whether policy-blindness in explanation predicts policy-violation in proposal.

**The question:** is failing to *see* the policy the same underlying deficit as failing to *respect* it?
- **If correlated:** the cheap read-only EXPLAIN benchmark becomes a screening proxy for risk in the expensive generative task — a practical model-selection tool.
- **If independent:** neither task can substitute for the other, and both must be measured — a methodological warning for benchmark designers.

Both outcomes are publishable-quality results for a bachelor thesis, and the analysis is honest either way because the hypothesis is registered before the data is seen.

**Why it is novel:** existing benchmarks measure generation only. Measuring two assistive roles per model against one source of truth, then correlating them, is not done in the surveyed literature.

**RESULT (27 July 2026): independent.** No correlation reached significance at n = 12 (strongest |r| = 0.329 against a critical value of 0.576). The conclusion is *no evidence of association* — the second pre-registered outcome — and it carries a concrete consequence: **neither task can serve as a proxy for the other**, so a model-selection process that measures only generation is blind to the omission failure documented in C2. Full analysis and the confound caveat: `docs/cross-task-results.md`.

### C4. Model-independent safety, demonstrated at breadth

The central architectural claim — *safety comes from the deterministic gate, not from model quality* — is tested across 12 models rather than argued.

**The invariant:** `system_policy_safety = 1.0` for every model, even those whose raw proposals violate intent. Model quality varies enormously (§C2); whole-system safety must not. `analyze_cross_task.py` reports this as a pass/fail invariant and names any model that violates it.

**Why it matters:** this converts an architectural assertion into an empirical result over ~650 runs. It also produces the most useful practical sentence in the thesis: *the choice of model materially affects the quality of assistance, but not the safety of the system.*

**RESULT (27 July 2026): the invariant holds.** Across 360 PROPOSE runs, **9 of 12 models produced at least one intent-violating proposal and none of them reached an accepted outcome**; `system_policy_safety = 1.000` for every model while model-level safety ranged from 0.50 to 1.00. This is the strongest single result in the thesis.

---

## Supporting findings (honest, and they strengthen the work)

**Repetition variance under nominally deterministic settings.** Three of twelve models returned different outputs for identical prompts at temperature 0 with a fixed seed, with the first repetition consistently differing from the subsequent two — consistent with runtime/cache state rather than sampling randomness. Reported as an observation with a plausible mechanism, explicitly *not* a proven cause. This forces a qualification of the V2 repeatability claim and adds an independent argument for deterministic gating: output that is not reproducible cannot be trusted on inspection alone.

**No prior-bias correction.** Given deliberately faulty configurations, all models described what was written — including a wrong next hop — and none fabricated the deny rule that had been removed. The failure mode identified in C2 is therefore **omission, not fabrication**, which is a meaningful bound on the risk.

**Fabricated-control metric.** `phantom_policy_claims` counts claimed deny rules that do not exist on the device, and is counted even for devices with no deny rules — precisely the case where recall is undefined and the error would otherwise be invisible.

---

## Positioning against the 2026 literature

Cornetto's conclusion, from Vanbever's group at ETH Zürich, is that reliable LLM-powered network automation requires integrating models into workflows guided by formal verification — reached by benchmarking 9 models over 231 repair problems on topologies up to 754 nodes. That is the same conclusion this thesis reaches, from the opposite direction: instead of measuring how models fail at scale, it builds the guarded workflow end to end, in miniature, with artifact integrity and human accountability, and measures that the containment holds.

**One-sentence positioning:** *Where recent benchmarks measure how far LLMs fall short of autonomous network configuration, this thesis measures how completely a deterministic pipeline can contain that shortfall — and shows the containment is invariant to which model is used.*

---

## What this thesis explicitly does not claim

Stating limits precisely is itself a defence asset:

1. Not that LLM-generated network changes are safe in general — only that within a declared topology, policy set, and repair scope, unsafe proposals were contained in every observed run.
2. Not statistical significance from repetition counts — identical repetitions under deterministic settings are not independent observations, and this is stated wherever repetitions are reported.
3. Not a user study — the human-in-the-loop element is a single-participant case study, framed as such.
4. Not scale results — the largest topology is three routers and five segments. Cornetto is cited for the scale dimension rather than imitated.
5. Not a custom or fine-tuned model — all models are off-the-shelf local weights; fine-tuning on the campaign evidence is future work.
