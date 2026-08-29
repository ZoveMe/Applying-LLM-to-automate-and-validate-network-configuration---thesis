# Claim–evidence audit of the thesis draft

**Date:** 12 August 2026 · **Document audited:** `Дипломска-работа-Митровски-222022.docx`
**Method:** every headline number recomputed from `docs/evidence/`, then checked against the
document text. Direction of trust is one-way — figures are derived from the evidence and the
prose is checked against them, never the reverse.

Reproduce with:

```bash
python3 tools/verify_thesis_claims.py     # exits non-zero if prose and evidence disagree
```

## Result

14 headline claims audited. **10 supported, 2 partly supported, 2 unsupported.** All four
misaligned claims have been corrected in the document; the verifier now passes on all
11 machine-checkable figures.

## Corrections applied

| ID | Location | Was | Evidence | Now |
|----|----------|-----|----------|-----|
| CLAIM-009 | Ch8.2 | „дванаесет различни фамилии модели" | 12 models, **8** families | „дванаесет модели од осум различни фамилии" |
| CLAIM-007 | Ch6.5 | „модели од седум различни фамилии" | T9 covers 8 models, **6** families | „осум различни модели од шест фамилии" |
| CLAIM-006 | Ch6.5 | „Во шест случаи била отстранета заштитата" | rule removed in **8**, breach realised in **6** | both figures stated, distinction explained |
| CLAIM-008 | Ch6.3 | „четири извршувања" unrecoverable | reset records show **2** (`reset-030`, `reset-056`) | „две извршувања" |

CLAIM-009 was the most serious: it contradicted the abstract of the same document, which
correctly states eight families. CLAIM-008 was introduced during figure insertion earlier the
same day and never held evidential support.

CLAIM-006 is now more informative than the original. The gap between eight rules removed and
six realised breaches is the T8 phenomenon the thesis already describes — a deny rule deleted
on one router while an equivalent block appears on another. Stating both numbers makes the
dual-validator argument concrete rather than incidental.

## Verified without change

| Claim | Derived value |
|-------|---------------|
| Total live runs | 848 = 360 guarded + 288 EXPLAIN + 80 ablation + 120 free-form |
| Gate recall | 1.000, 0 false negatives across 156 gated runs (precision 0.637) |
| Three-condition breach rate | 0% (360) / 8% (80) / 33% (120) |
| Ablation | 80 replayed, 32 not matching intent, 24 connectivity broken |
| T9 | broken in 24 of 24 repetitions |
| Free-form | 53 not matching, 40 breached, 29 rule removed, 14 connectivity broken |
| Free-form commands | 199 executed, 55 rejected by the device itself |
| EXPLAIN | 288 runs, 100% schema validity, zero hallucinated entities |

## Manuscript lint

`lint_manuscript.py` reports **0 errors** across 1,921 lines. One placeholder — `[РЕФЕРЕНЦА]`
in §2.3 — was found and resolved to `[1, 2]` before this audit. No remaining placeholders,
unresolved markers, or risky declarations.

## Open items for the mentor

1. **Precision is worth discussing.** Gate recall is 1.000, but precision is 0.637 — 29 clean
   proposals were rejected. The thesis presents this as a conservative-by-design trade-off. It
   would be worth confirming that framing is acceptable rather than a weakness to address.
2. **Repetitions are not independent observations.** Already stated as a limitation in §7.3;
   worth confirming the level of statistical caution is appropriate for a bachelor thesis.
3. **T9 dominates the damage statistics.** One adversarial case accounts for 24 of the 32
   ablation failures. The per-case table makes this visible, but a reader skimming only the
   summary figure could over-generalise.
