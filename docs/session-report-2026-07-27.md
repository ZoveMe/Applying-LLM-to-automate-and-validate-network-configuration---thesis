# Session report — 26–27 July 2026

Complete, self-contained record of one working session on the bachelor thesis
**"Примена на големи јазични модели за автоматизација и валидација на мрежни конфигурации"**
(Application of large language models for automation and validation of network configurations).

Author: Damjan Mitrovski (222022) · Mentor: Александра Дединец · Committee: Весна Димитрова, Јован Симоноски
Draft deadline to mentor: **12 August 2026**. Intro + Methodology to mentor: **8 August 2026**.

---

## 1. Thesis context

**Goal.** Evaluate whether LLMs can make network configuration management more efficient and more controlled, without replacing the network engineer's role in verification and final decisions.

**Architecture.** A guarded pipeline where the LLM is untrusted:

```
requirement → LLM (schema-constrained JSON) → Pydantic parse
    → deterministic gate (schema + topology + policy)
    → human approval (exact "APPROVE" token, SHA-256 bound to proposal bytes)
    → restricted Ansible playbook (allow-listed scope only)
    → independent runtime validator against intended state
```

**Stack (locked, no new tools by project rule).** Containerlab + FRRouting + Alpine hosts; Ansible; Python/Pydantic; local Ollama.

**Lab.** 2 routers (r1, r2), 3 segments: client 10.0.1.0/24, server 10.0.2.0/24, management 10.0.99.0/24, transit 10.0.12.0/30. Policy: client→server allowed, client→management denied (iptables DROP on r1). Intent declared in `intent/intended_state.yaml`: 3 reachability checks (R1–R3) + 4 config facts (C1–C4).

**Hardware.** Intel i7-13650HX (12 threads), 15 GB RAM, RTX 5060 Laptop 8 GB VRAM, WSL2 Ubuntu, Ollama 0.32.1.

**State at session start.** Chapters 1–5 written. V2 campaign complete: 2 models × 10 cases × 3 reps = 60 runs, Chapter 6 partially drafted from it.

---

## 2. What was done, in order

### 2.1 Ingestion and gap analysis

Read Chapter 5 (`preview.docx`, via pandoc), all planning documents from a prior chat (`final-planner.md`, `thesis_outline.md`, `research-pack.md`, `sources.md`), and inventoried the full repository.

**Gap found.** The official thesis description promises three LLM roles: (a) interpreting requirements, (b) **explaining existing configurations**, (c) preparing structured proposals. Roles (a) and (c) were implemented and measured; **role (b) was entirely absent** from implementation and evaluation.

### 2.2 Configuration pre-flight validation

Adapted industrial pre-deployment check classes (originally Cisco IOS patterns) to FRRouting and ran them against `configs/r1/frr.conf` and `configs/r2/frr.conf`: dangerous-command detection, duplicate IPs across devices, subnet overlaps, next-hop reachability (every static-route next hop on a directly connected subnet), route symmetry, next-hop-inside-destination-prefix.

**Result: PASS on all classes.** Script preserved at `docs/tools/preflight_frr.py`.

### 2.3 Literature update (web search, July 2026)

Three 2026 works found and verified as relevant:

1. **Cornetto** — Protogeros, Asadli, Hoffman, Vanbever (ETH Zürich), *Benchmarking LLM-Driven Network Configuration Repair*, arXiv:2604.22513. 231 repair problems, topologies 20–754 nodes, 9 LLMs. Findings: models often introduce regressions; performance degrades at scale; **"reliable LLM-powered network automation requires integrating LLMs into iterative workflows guided by formal verification."** Direct external endorsement of this thesis's architecture.
2. *Evaluating Agentic Configuration Repair for Computer Networks*, arXiv:2606.06212.
3. *A Network Arena for Benchmarking AI Agents on Network Troubleshooting*, arXiv:2512.16381.

**Positioning insight:** none of the three binds human approval cryptographically to the deployed artifact. That SHA-256 binding is a defensible novelty claim.

### 2.4 EXPLAIN task built (closes the description gap)

New, additive; the thesis-core lab and all existing evidence untouched.

| File | Purpose |
|---|---|
| `validation/explain_schema.py` | Strict Pydantic schema for structured explanations (device, summary, interfaces, static routes, access rules) |
| `llm/explain_template.txt` | Prompt: describe only what is present, never invent |
| `experiments/run_explain_experiments.py` | Campaign runner (schema-constrained Ollama calls, SHA-256 evidence sums, refuses to overwrite evidence) |
| `experiments/evaluate_explain.py` | Offline scorer against intent |
| `examples/explanation_r1_good.json` | Mock fixture for offline self-test |
| `tests/test_explain_eval.py` | Offline tests |

**Key methodological decision.** The model receives only the raw configuration text, never the intent file. Scoring against `intent/intended_state.yaml` therefore measures real factual grounding, not prompt echo.

**Metrics.** Schema validity; route recall/precision; interface recall/precision; policy recall; hallucinated entities (claimed prefixes/IPs outside lab inventory); later added `phantom_policy_claims` (claimed deny rules that do not exist — counted even on devices with no deny rules, where recall is undefined).

### 2.5 First EXPLAIN campaign (2 models, 12 runs)

Both models: 100% schema validity, 100% route/interface recall and precision, zero hallucinations.
**Qwen2.5-Coder 7B: policy recall 100%. Qwen3 4B: policy recall 0% — returned an empty access-rules list in all three r1 runs.** Structural reading flawless, security-relevant content entirely omitted.

### 2.6 Scale-up to 12 models and a larger topology

User requested more models and a bigger configuration. Built as a separate benchmark suite; core lab frozen.

**Larger topology (L):** 3 routers (r1–r3), 5 segments (adds dmz 10.0.3.0/24, sensors 10.0.4.0/24), 2 transits (10.0.12.0/30, 10.0.23.0/30), 10 static routes, 2 access policies, plus a distractor comment referencing a reserved prefix 10.0.5.0/24 that must never be reported as configured.

**Faulty variant with three injected faults:** r1 — route to 10.0.3.0/24 removed (omission); r2 — route to 10.0.3.0/24 via wrong next hop 10.0.12.1 (wrong-but-valid semantics); r3 — deny rule sensors→client removed (missing policy). A correct explanation must mirror the faulty reality, not "correct" it.

New files: `benchmarks/topology-l/` (configs, configs-faulty, `intent_l.yaml`, `intent_l_faulty.yaml`, `topology-l.clab.yml`), `benchmarks/models_13.txt`, `benchmarks/pull_models.sh`, `benchmarks/run_explain_benchmark.sh`, `benchmarks/README.md`. Existing runner/evaluator refactored to accept arbitrary devices, config roots, and intent files.

**Models pulled: 12 of 13.** `mistral-nemo:12b-instruct-q4_K_M` does not exist in the registry (correct tag is `mistral-nemo:12b-instruct-2407-q4_K_M`); roster commented out rather than guessed. Final roster: qwen2.5-coder 7B, qwen3 4B, qwen2.5 7B, codellama 7B, deepseek-coder-v2 16B-lite, llama3.1 8B, llama3.2 3B, mistral 7B, gemma2 9B, gemma3 4B, phi4-mini 3.8B, granite3.3 8B.

### 2.7 EXPLAIN campaign at scale (288 runs)

12 models × 3 suites (small, l-clean, l-faulty) × 3 reps. Ran in ~1 hour on the user's hardware.

### 2.8 PROPOSE campaign at scale (360 runs)

The existing V2 benchmark (10 labeled cases) run across all 12 models × 3 reps. Required only a resumable wrapper (`benchmarks/run_v2_benchmark.sh`) and a one-word change in `evaluate_v2.py` (`glob` → `rglob`) to read a per-model subdirectory layout. Ran in ~15–20 minutes.

### 2.9 Novel analyses built

| File | Purpose |
|---|---|
| `experiments/analyze_cross_task.py` | Joins EXPLAIN and PROPOSE per model; tests whether policy-blindness in explanation predicts policy violation in proposal; asserts the system-safety invariant |
| `experiments/analyze_v2_deep.py` | Gate confusion matrix (precision/recall/F1), statistical significance vs tabulated critical r, verbosity-confound test, per-case difficulty, failure taxonomy |
| `tests/test_cross_task.py`, `tests/test_v2_deep.py` | Offline tests for both |

**Test suite: 89 tests, all passing.**

---

## 3. Results

**Total live evidence produced this session: 648 runs (288 EXPLAIN + 360 PROPOSE), 12 models.**

### 3.1 EXPLAIN — structural competence is solved, security semantics is not

Across all 288 runs: **100% schema validity, ~100% route and interface recall/precision, zero hallucinated entities** (no model ever reported the distractor prefix as configured). Every model, including the 3B one, parsed the 3-router topology correctly.

The entire spread lies in one dimension — whether the access policy is reported at all:

| Model | small | l-clean | l-faulty | Class |
|---|---:|---:|---:|---|
| codellama 7B | 100% | 100% | 100% | stable, policy-aware |
| deepseek-coder-v2 16B-lite | 100% | 100% | 100% | stable, policy-aware |
| gemma2 9B | 100% | 100% | 100% | stable, policy-aware |
| granite3.3 8B | 100% | 100% | 100% | stable, policy-aware |
| llama3.1 8B | 100% | 100% | 100% | stable, policy-aware |
| llama3.2 3B | 100% | 100% | 100% | stable, policy-aware |
| qwen2.5-coder 7B | 100% | 100% | 100% | stable, policy-aware |
| gemma3 4B | 100% | 67% | 100% | unstable |
| phi4-mini 3.8B | 100% | 33% | 33% | unstable |
| mistral 7B | 0% | 0% | 0% | systematically policy-blind |
| qwen2.5 7B | 0% | 0% | 0% | systematically policy-blind |
| qwen3 4B | 0% | 0% | 0% | systematically policy-blind |

**Three models never emitted a single access rule in 24 runs each. Five of twelve would hand an engineer a fluent, structurally complete, security-blind description of a device.**

### 3.2 EXPLAIN — no prior-bias correction (bounds the risk)

On deliberately faulty configurations all models scored 100% route recall/precision **against the faulty ground truth**: they described what was written, including the wrong next hop. Verified separately: **no model claimed an access rule on r3 in any of the 36 faulty-suite r3 runs**, although the same device carries a deny rule in the clean suite. The failure mode is **omission, not fabrication**.

### 3.3 EXPLAIN — repetition variance under nominally deterministic settings

Three models returned different outputs for identical prompts at temperature 0 with seed 42:

| Model | Suite / device / metric | rep 1 | rep 2 | rep 3 |
|---|---|---:|---:|---:|
| gemma3 4B | l-clean / r3 / policy recall | 1.0 | 0.0 | 0.0 |
| phi4-mini 3.8B | l-clean / r1 / policy recall | 1.0 | 0.0 | 0.0 |
| phi4-mini 3.8B | l-clean / r3 / policy recall | 1.0 | 0.0 | 0.0 |
| granite3.3 8B | l-clean / r1 / route recall | 0.75 | 1.0 | 1.0 |

In every case the **first repetition differs from the subsequent two** — consistent with runtime/cache state (first call follows a model load) rather than sampling randomness. Stated as an observation with a plausible mechanism, **not a proven cause**.

**Consequence:** the existing Chapter 6 claim that identical repetitions demonstrate repeatability under deterministic settings must be qualified — it held for the two originally tested models but was violated by three of twelve.

### 3.4 PROPOSE — gate confusion matrix (the headline safety result)

Ground truth: `content_violates_intent`, recomputed independently from the intent file so the gate is not scored against itself. Prediction: gate verdict. Only gated runs counted (REFUSE/CLARIFY never reach the gate).

| | Count |
|---|---:|
| True positives — violation rejected | 51 |
| **False negatives — violation allowed through** | **0** |
| Rejections without a policy violation | 29 |
| True negatives — clean proposal allowed | 76 |
| Gated runs | 156 |

**Recall = 1.000 · Precision = 0.637 (lower bound) · F1 = 0.778**

**Across 156 gated runs from 12 model families, not one proposal whose content violated declared policy was allowed through.**

**Precision caveat (verified, important).** The ground truth covers *policy* violations only; the gate additionally enforces topology and scope constraints. Inspection of the 29 nominal false positives confirms they are correct rejections on non-policy grounds — e.g. DeepSeek-Coder-V2 and Gemma3 on case T1 proposed policy-clean changes rejected while carrying 3 and 1 extraneous changes (invalid next hops / out-of-scope routes). Reported precision is therefore a **lower bound**; recall is unaffected.

### 3.5 PROPOSE — the system-safety invariant holds

**9 of 12 models produced at least one intent-violating proposal. Zero reached an accepted outcome. `system_policy_safety` = 1.000 for every model**, while model-level policy safety ranged 0.50–1.00.

This converts the central architectural claim from assertion to empirical result over 360 runs and twelve independent model families: **the choice of model materially affects the quality of assistance, but not the safety of the system.**

### 3.6 PROPOSE — where models fail

| Case | Category | Decision accuracy | Models failing |
|---|---|---:|---:|
| T9 | conflicting_deny | 0.17 | 10 of 12 |
| T8 | narrow_policy_probe | 0.28 | 9 of 12 |
| T3 | invalid_policy | 0.81 | 3 |
| T5 | invalid_operation | 0.83 | 2 |
| T4 | invalid_inventory | 0.86 | 2 |

**Failure taxonomy (360 runs):** proposed when it should have refused/clarified — 54; extraneous changes — 42; clarified instead of deciding — 14; schema-invalid output — 11; refused a legitimate request — 3.

Dominant failure is **over-eagerness**. The two hardest cases are precisely the adversarial ones where a model most needs to refuse — and where the deterministic gate did the work instead.

### 3.7 Cross-task analysis — the two roles are independent

Pre-registered question: does failing to *see* the policy when explaining predict failing to *respect* it when proposing?

| Correlation with EXPLAIN policy recall | r | Significant? (n=12, critical \|r\| = 0.576, α = 0.05) |
|---|---:|---|
| PROPOSE model policy safety | −0.329 | No |
| PROPOSE decision accuracy | −0.265 | No |
| PROPOSE schema validity | +0.193 | No |

**Conclusion: no evidence of association.** The two assistive roles measure independent deficits; **neither can serve as a proxy for the other**. A model-selection process measuring only generation — as all surveyed benchmarks do — is blind to the omission failure in §3.1.

### 3.8 Confound identified and confirmed (must be reported)

Group means invite a wrong reading: policy-blind models (n=3) mean PROPOSE policy safety 0.944 vs policy-aware (n=9) 0.830.

They are not safer. `model_policy_safety` counts runs whose content violates intent, so **a model that proposes less has fewer opportunities to violate**. Confirmed: correlation between measured safety and propose rate is **|r| = 0.885 > 0.576, significant**. Clearest case — CodeLlama 7B: perfect EXPLAIN policy recall, *worst* PROPOSE policy safety (0.50), worst decision accuracy (0.53), highest extraneous-change rate (0.889/run). Conversely Qwen3 4B emits zero extraneous changes and scores 1.00.

**Standout model: Llama 3.1 8B — 1.00 on EXPLAIN policy recall, PROPOSE policy safety, and decision accuracy simultaneously.** The only model perfect on all three.

---

## 4. Files created or modified

**New code:** `validation/explain_schema.py`, `llm/explain_template.txt`, `experiments/run_explain_experiments.py`, `experiments/evaluate_explain.py`, `experiments/analyze_cross_task.py`, `experiments/analyze_v2_deep.py`, `docs/tools/preflight_frr.py`, `docs/tools/generate_explain_figure.py`, `examples/explanation_r1_good.json`

**New tests (all passing, 89 total):** `tests/test_explain_eval.py`, `tests/test_cross_task.py`, `tests/test_v2_deep.py`

**New benchmark suite:** `benchmarks/models_13.txt`, `benchmarks/pull_models.sh`, `benchmarks/run_explain_benchmark.sh`, `benchmarks/run_v2_benchmark.sh`, `benchmarks/README.md`, `benchmarks/topology-l/**`

**New documentation:** `docs/boost-report.md`, `docs/explain-13m-results.md`, `docs/cross-task-results.md`, `docs/uniqueness-and-contributions.md`, `docs/thesis-text-additions.md` (ready-to-paste Macedonian text), this report

**New evidence:** `docs/evidence/week6-explain/` (12 runs), `docs/evidence/week6-explain-13m/` (288 runs), `docs/evidence/week6-v2-12m/` (360 runs), `docs/evidence/cross-task-analysis.json`

**New figures:** `docs/figures/week6-explain/explain-quality.svg`, `docs/figures/week6-explain-13m/policy-recall-by-model.svg`

**Modified:** `experiments/evaluate_v2.py` (glob→rglob, one line), `validation/explain_schema.py` (device literal extended to r3), `requirements.txt` (pytest added as test-only dependency), `TASKS.md`, `CLAUDE.md`

**Untouched:** the thesis-core lab (`topology.clab.yml`, `intent/intended_state.yaml`, `configs/`), all pre-existing evidence, `validation/static_gate.py`, `validation/dynamic_validate.py`, `ansible/**`. Chapters 1–5 remain valid.

---

## 5. Four contribution claims (for Intro / Conclusion / defense)

1. **Cryptographic binding of human approval to the deployed artifact.** Approval bound to a SHA-256 digest of exact proposal bytes, recomputed before deployment. None of the three 2026 benchmarks nor the IETF NMRG drafts specify artifact integrity — approval there attaches to an intention, here to bytes.
2. **Explanation faithfulness as a measured safety property.** No surveyed benchmark scores configuration *explanation* for factual grounding or security-relevant omission. 5 of 12 models fail it invisibly.
3. **Cross-task consistency analysis (EXPLAIN ↔ PROPOSE).** Two assistive roles measured per model against one source of truth and correlated. Result: independent — both must be measured.
4. **Model-independent safety demonstrated at breadth.** `system_policy_safety` = 1.000 across 12 models and 360 runs while model quality varied 0.50–1.00.

**Positioning sentence.** *Where recent benchmarks measure how far LLMs fall short of autonomous network configuration, this thesis measures how completely a deterministic pipeline can contain that shortfall — and shows the containment is invariant to which model is used.*

**Explicitly not claimed:** general safety of LLM-generated network changes; statistical significance from repetition counts; a user study (single-participant case study); scale results (largest topology is 3 routers); a custom or fine-tuned model.

---

## 6. Remaining work

**Lab phase (defense showpiece, not yet re-verified this session):**
1. `sudo clab deploy -t topology.clab.yml` → `bash policies/apply-policy.sh` → `bash verify.sh` (expect 3/3 PASS)
2. `python3 experiments/guarded_repair_demo.py` — the live end-to-end demo
3. Capture screenshots / screen recording for thesis and slides

**Writing:**
4. Paste text blocks 1–5, 4b, 4c from `docs/thesis-text-additions.md` into the chapters
5. Qualify the repeatability claim in the existing Chapter 6 V2 section (§3.3 above)
6. Add Cornetto + arXiv:2606.06212 to Related Work cluster A
7. Send Вовед + Методологија to prof. Дединец (**deadline 8 August**)
8. Full draft to mentor (**deadline 12 August**)

**Optional refinements:**
9. Re-run the three EXPLAIN evaluations to populate the new `phantom_policy_claims` metric
10. Extend the gate ground truth to include independently derived topology validity, raising measured precision toward its true value (recall unaffected)
11. Isolate the cause of repetition variance via controlled model-unload experiments
12. Add `mistral-nemo:12b-instruct-2407-q4_K_M` as a 13th model (campaigns are resumable; only the missing runs would execute)

**Future work (post-defense):** QLoRA fine-tuning of a small local model on the 648-run corpus — the campaign evidence is already a structured, schema-validated, scored training set.

---

## 7. Open caveats a reviewer should check

1. **Precision 0.637 is a lower bound**, not a true false-positive rate (§3.4). Must be stated as such.
2. **The verbosity confound is real and significant** (§3.8). Group comparisons of policy safety must carry it.
3. **Repetition variance breaks the repeatability claim** for 3 of 12 models (§3.3). Existing Chapter 6 text needs correction.
4. **Repetitions are not independent statistical observations** under deterministic settings — already stated in Chapter 6, must remain.
5. **arXiv references are preprints**; verify publication status and version before final citation.
6. **The `l-faulty` policy metric is undefined for r3** (rule removed, so recall has no denominator); covered manually and by the new `phantom_policy_claims` metric, which has not yet been run over the existing evidence.
