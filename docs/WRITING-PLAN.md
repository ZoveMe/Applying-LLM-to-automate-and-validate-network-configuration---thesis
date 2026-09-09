# Writing plan — 1 to 15 August 2026

Experiments are finished. **848 live runs, four campaigns, all analysed.** Nothing further needs to be built or measured. This document is the assembly map: what goes where, in what order, and what is still genuinely missing.

---

## Where every piece of written material lives

| Thesis section | Source | Status |
|---|---|---|
| Апстракт (MK + EN) | `abstract-and-conclusion-mk.md` | **Written — was missing** |
| Ch3 Сродни истражувања | `thesis-text-additions.md` block 1 | Written |
| Ch5 Имплементација | `preview.docx` + block 3 (config audit sentence) | Written |
| Ch6 — EXPLAIN methodology | block 4 | Written |
| Ch6 — 12-model EXPLAIN + figure | block 4b | Written |
| Ch6 — gate confusion matrix + cross-task | block 4c | Written |
| Ch6 — live counterfactual ablation | block 4d | Written |
| Ch6 — **three-condition ablation (capstone)** + figure | block 4e | Written |
| Ch7 Заклучок | `abstract-and-conclusion-mk.md` | **Written — was missing** |
| Ch7 Идна работа | block 5 + conclusion §7.4 | Written |
| Novelty claim (Заклучок/Сродни) | block 2 | Written |

---

## Recommended order of assembly

Work outward from the strongest material.

**Day 1–3 — Chapter 6.** Paste blocks 4, 4b, 4c, 4d, 4e in that order. This is the heart of the thesis and the part with the most new content. Insert the three figures where marked. As you go, renumber tables and figures consistently.

**Day 4 — Chapter 7.** Paste the Заклучок (§7.1–7.5). It already answers your three research questions with measured numbers, so check it against your actual ИП wording in Chapter 1 and adjust phrasing to match.

**Day 5 — Chapters 1 and 3.** Paste the Апстракт, then block 1 into Сродни истражувања and block 2 into the positioning/novelty paragraph. The introduction can now be written last because you know exactly what you are introducing.

**Day 6 — the two flagged corrections** (below).

**Day 7–8 — read the whole thing end to end.** Fix table numbering, figure references, terminology drift between chapters. Check that every number quoted in the text matches the evidence files.

**Day 9–10 — send to mentor**, leaving buffer before the 15th.

**Remaining days — defence preparation** (see below).

---

## Two corrections that must be made

**1. Qualify the repeatability claim in the existing Chapter 6 V2 section.**
The current text states that identical repetitions demonstrate repeatability under deterministic settings. That held for the two originally tested models but was violated by three of twelve (gemma3 4B, phi4-mini 3.8B, granite3.3 8B), each with the first repetition differing from the subsequent two. Wording is in `thesis-text-additions.md` block 4b, Result 3. **This strengthens the argument** — non-reproducible output is another reason for deterministic gating — but it must be stated.

**2. Citation format for the three 2026 works.** All verified as arXiv preprints with no formal venue. Cite with version and date:

- Protogeros, I., Asadli, R., Hoffman, B., Vanbever, L. (2026). *Benchmarking LLM-Driven Network Configuration Repair.* arXiv:2604.22513v1 [cs.NI], 24 April 2026. ETH Zürich.
- *Evaluating Agentic Configuration Repair for Computer Networks.* arXiv:2606.06212v1 [cs.NI].
- *A Network Arena for Benchmarking AI Agents on Network Troubleshooting.* arXiv:2512.16381v1 [cs.NI].

---

## Numbers to quote consistently

Use these exact figures; they are the ones in the evidence files.

| Claim | Number |
|---|---|
| Total live runs | 848 |
| Guarded pipeline runs | 360 |
| Guarded unsafe deployments | **0** |
| Gate recall | **1.000** (0 false negatives / 156 gated runs) |
| Gate precision | 0.637 (lower bound) |
| Models producing an intent-violating proposal | 9 of 12 |
| Three-condition breach rate | 0% → 8% → 33% |
| Free-form runs / breaches | 120 / 40 |
| Runs left unrecoverable | 4 of 120 |
| EXPLAIN runs / hallucinated entities | 288 / **0** |
| Models never reporting an access rule | 3 of 12 (24 runs each) |
| Cross-task correlation | \|r\| = 0.329, **not significant** (critical 0.576 at n=12) |

---

## Defence preparation (after the draft is sent)

**The demonstration.** Four steps, roughly ninety seconds:

1. **Ungoverned** + *"let the client subnet reach the management network"* → applies, panel red, policy breached
2. **Reset lab**
3. **Guarded** + the same request → gate rejects it, nothing deployed
4. **Guarded** + a legitimate request → all stages green, SHA-256 shown, approve

Same model, same words, opposite outcomes. Rehearse it three times and screenshot both outcomes as a fallback in case the live demo fails.

**Questions to prepare for.** Each has an honest answer already in the material:

- *Why only three routers?* — Cornetto is cited for the scale dimension; the contribution is the guarded workflow measured end to end, not scale.
- *Is 0 breaches just luck?* — 156 gated runs, 51 caught violations, and the counterfactual shows 32 of the rejections would have broken the network.
- *Is the gate too strict?* — Yes, measurably: precision 0.637, 48 harmless proposals rejected. That trade is deliberate and quantified.
- *Are the repetitions independent?* — No, and the thesis says so; three models even broke determinism.
- *Why not fine-tune a model?* — Future work; the 848-run corpus is already the training set.
- *Would this scale to production?* — Bounded claim: within the declared topology, policy and action space. Stated explicitly in §7.3.

---

## What NOT to do now

Adding another experiment trades against write-up quality, and the write-up is what is graded. Specifically:

- No new models. Twelve across eight families is already broad.
- No larger topology. It would invalidate every frozen evidence file two weeks before submission.
- No fine-tuning. It is a genuine follow-up project, correctly placed in §7.4.
- No re-running condition C with three repetitions. The single-repetition limitation is stated honestly in §7.3, which is worth more than three days of lab breakage.

The experimental work is done. The thesis is now a writing problem.
