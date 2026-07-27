# Scaled EXPLAIN benchmark — results analysis

**Campaign:** 12 models × 3 suites × 3 repetitions = **288 live Ollama runs**
**Evidence:** `docs/evidence/week6-explain-13m/`
**Analysed:** 27 July 2026
**Settings:** temperature 0, seed 42, num_ctx 8192, num_predict 1024, schema-constrained decoding

Suites: `small` (thesis-core, r1–r2) · `l-clean` (3 routers, 5 segments, 10 routes, 2 policies) · `l-faulty` (same topology, 3 injected faults)

---

## 1. Aggregate results

Structural metrics (schema validity, route recall/precision, interface recall/precision) are **100% for every model in every suite**, with two single-run exceptions noted in §3. Hallucinated entities: **0 across all 288 runs**. The table below therefore reports only the discriminating metric.

### Policy recall (mean over policy-bearing runs)

| Model | small | l-clean | l-faulty | Behaviour class |
|---|---:|---:|---:|---|
| codellama 7B | 100% | 100% | 100% | stable, policy-aware |
| deepseek-coder-v2 16B-lite | 100% | 100% | 100% | stable, policy-aware |
| gemma2 9B | 100% | 100% | 100% | stable, policy-aware |
| granite3.3 8B | 100% | 100% | 100% | stable, policy-aware |
| llama3.1 8B | 100% | 100% | 100% | stable, policy-aware |
| llama3.2 3B | 100% | 100% | 100% | stable, policy-aware |
| qwen2.5-coder 7B | 100% | 100% | 100% | stable, policy-aware |
| gemma3 4B | 100% | **67%** | 100% | **unstable** |
| phi4-mini 3.8B | 100% | **33%** | **33%** | **unstable** |
| mistral 7B | **0%** | **0%** | **0%** | **systematically policy-blind** |
| qwen2.5 7B (general) | **0%** | **0%** | **0%** | **systematically policy-blind** |
| qwen3 4B | **0%** | **0%** | **0%** | **systematically policy-blind** |

Split: **7 / 12 stable and policy-aware · 2 / 12 unstable · 3 / 12 systematically policy-blind.**

Figure: `docs/figures/week6-explain-13m/policy-recall-by-model.svg`

---

## 2. Finding A — structural competence is solved; security semantics is not

Every model, including the 3B one, parsed every configuration perfectly: all interfaces, all static routes, correct next hops, zero invented entities — including in the larger 3-router topology and in the presence of a distractor comment (the reserved prefix `10.0.5.0/24`, never once reported as configured).

The entire spread between models lies in **one dimension**: whether the firewall policy is reported at all. Three models never emitted a single access rule in 24 runs each; two more did so inconsistently. Their explanations are fluent, structurally complete, and silently omit the security-relevant half of the configuration.

This is the strongest empirical result of the thesis. An engineer relying on such an explanation receives an accurate description of *connectivity* and no indication that a *deny* rule governs the device. That failure mode is invisible without deterministic checking against declared intent — which is precisely what the validation layer provides.

## 3. Finding B — repetition variance under nominally deterministic settings

Three models produced **different outputs across repetitions of an identical prompt** despite temperature 0 and a fixed seed:

| Model | Suite / device | rep 1 | rep 2 | rep 3 |
|---|---|---:|---:|---:|
| gemma3 4B | l-clean / r3 (policy recall) | 1.0 | 0.0 | 0.0 |
| phi4-mini 3.8B | l-clean / r1 (policy recall) | 1.0 | 0.0 | 0.0 |
| phi4-mini 3.8B | l-clean / r3 (policy recall) | 1.0 | 0.0 | 0.0 |
| granite3.3 8B | l-clean / r1 (route recall) | 0.75 | 1.0 | 1.0 |

In every case the **first repetition differs from the subsequent two**, which is consistent with runtime/cache state rather than sampling randomness (the first call follows a model load; later calls reuse a warm runtime). This is an observation with a plausible mechanism, **not a proven cause** — isolating it would require controlled model-unload experiments, which is a clean future-work item.

Methodological consequence: the V2 chapter states that identical repetitions demonstrate repeatability under deterministic settings. With 12 models that claim **no longer holds universally** and must be qualified: repeatability was observed for the two originally tested models, but three of twelve models violated it. This strengthens rather than weakens the thesis — non-reproducible output is another argument for deterministic gating — but it must be stated honestly.

## 4. Finding C — no prior-bias "correction" of faulty configurations

In `l-faulty`, three faults were injected (missing route on r1, wrong next hop on r2, removed deny rule on r3). All models scored 100% route recall and precision **against the faulty ground truth**: they reported the configuration as written, including the wrong next hop, and did not silently substitute the plausible-but-absent elements.

Verified separately: **no model claimed an access rule on r3** in any of the 36 `l-faulty` r3 runs, even though the same device carries a deny rule in `l-clean` and a "typical" DMZ/sensor router would have one. Models did not invent the missing policy.

This is a clean negative result: within this benchmark, the models explain the text they are given rather than the configuration they might expect. It bounds the failure mode identified in Finding A — the problem is **omission**, not **fabrication**.

## 5. Known limitation of the current metric

Because the r3 deny rule is *removed* in `l-faulty`, the expected-deny set for that device is empty and `policy_recall` is reported as `null` (skipped) rather than scored. The missing-policy fault is therefore **not** captured by the policy-recall column; it was verified manually (§4) by confirming that no run claimed a phantom rule.

Proposed fix (small, additive, testable): add a `phantom_policy_claims` counter to `evaluate_explain.py` that counts claimed access rules absent from the ground-truth deny set for that device. This converts the manual check in §4 into a reported metric. Not implemented yet — see TASKS.md.

## 6. What this changes in the thesis

1. **Ch6 gains a model-comparison section** with a real, non-saturated discriminator instead of an all-100% table.
2. **The repeatability claim must be qualified** wherever the V2 chapter asserts deterministic repeatability.
3. **The central argument is now empirically supported at breadth**: across twelve independent model families, structural fluency does not imply security-semantic completeness, and 5/12 models would have handed an engineer a materially incomplete picture of the device.
4. **Model-selection guidance** becomes a defensible contribution: for this task class, coder-tuned and larger general models were reliable; three widely used models were not — and the pipeline's safety does not depend on which one is chosen, because the gate is model-independent.
