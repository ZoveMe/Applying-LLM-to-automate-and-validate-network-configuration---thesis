# Scaled EXPLAIN benchmark (13 models × 3 suites)

Additive benchmark suite. The thesis-core lab (`topology.clab.yml`,
`intent/intended_state.yaml`, all existing evidence) is **frozen and unchanged** —
Ch5/Ch6 remain valid. This directory extends the EXPLAIN task only.

## Suites

| Suite | Configs | Devices | Ground truth | Tests |
|---|---|---|---|---|
| `small` | `configs/` (thesis core) | r1, r2 | `intent/intended_state.yaml` | baseline, comparable to first EXPLAIN run |
| `l-clean` | `topology-l/configs/` | r1, r2, r3 | `topology-l/intent_l.yaml` | scale: 5 segments, 2 transits, 10 routes, 2 policies, distractor comments |
| `l-faulty` | `topology-l/configs-faulty/` | r1, r2, r3 | `topology-l/intent_l_faulty.yaml` | robustness: does the explanation mirror the *actual* (faulty) config, or "correct" it from prior bias? |

## Injected faults (l-faulty)

| Device | Fault | Class |
|---|---|---|
| r1 | route to 10.0.3.0/24 removed | omission |
| r2 | route to 10.0.3.0/24 via 10.0.12.1 (valid IP, wrong direction) | wrong-but-valid semantics |
| r3 | deny rule sensors→client removed | missing policy |

A perfect explainer scores 100% against the faulty intent: it reports exactly
what the config contains. Score drops reveal bias toward "typical" configs —
the model explaining what it *expects* instead of what it *reads*. The
distractor comment (reserved prefix 10.0.5.0/24) tests hallucination
discipline in all suites: mentioning it in structured output counts as a
hallucinated entity.

## Interpretation of the metric direction

- `l-clean` recall drop vs `small`  → performance degrades with scale (Cornetto's finding, in miniature)
- `l-faulty` precision drop vs `l-clean` → prior-bias errors ("corrected" the fault)
- hallucinations > 0 in any suite → invented entities (worst class)

## Run order

```bash
bash benchmarks/pull_models.sh              # ~55-65 GB total, verify failed tags
bash benchmarks/run_explain_benchmark.sh    # 312 runs, resumable per model
```

Evidence lands in `docs/evidence/week6-explain-13m/{small,l-clean,l-faulty}/<model>/`,
evaluations in the three `*-evaluation.json` files next to them. The script
skips model+suite combinations whose output directory already exists, so a
crashed or interrupted campaign resumes by re-running the same command.

Models >8 GB (deepseek-coder-v2, mistral-nemo) partially offload to CPU on the
RTX 5060 (8 GB VRAM) — slower runs are expected, not errors. If a model cannot
produce schema-constrained JSON at all, its `schema_validity` will show it;
that is a *result*, not a failure of the harness.

## Optional live deployment

`topology-l/topology-l.clab.yml` deploys the larger lab (8 containers) if you
want screenshots or reachability demos. The benchmark itself is fully offline —
it reads config files and never touches the network.
