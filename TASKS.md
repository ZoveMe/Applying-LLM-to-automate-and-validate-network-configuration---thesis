# Tasks

## Active

### LAB PHASE (defense showpiece — do this next)

- [ ] **Deploy the lab** - `sudo clab deploy -t topology.clab.yml` then `bash policies/apply-policy.sh` then `bash verify.sh` (expect 3/3 PASS)
- [ ] **Run the live guarded repair demo** - `python3 experiments/guarded_repair_demo.py` - this is the piece you demo at the defense
- [ ] **Run the Week 7 larger-topology Ansible experiment** - follow `docs/week7-ansible-multifault.md`; do not report success until the new evidence directory is reviewed
- [ ] **Capture screenshots/recording** of the demo for the thesis and slides

### WRITING

- [ ] **Paste text blocks 1-5, 4b, 4c** from docs/thesis-text-additions.md into the chapters
- [ ] **After the Week 7 live run, fill block 6a only from the reviewed evidence** - replace every `[ПОПОЛНИ]`; report any failure honestly
- [ ] **Qualify the repeatability claim in Ch6 (V2 section)** - 3 of 12 models broke determinism
- [ ] **Re-run explain evaluations** to pick up the new phantom-policy metric:
      `python3 experiments/evaluate_explain.py --evidence docs/evidence/week6-explain-13m/small --out docs/evidence/week6-explain-13m/small-evaluation.json`
      (repeat for l-clean with `--intent benchmarks/topology-l/intent_l.yaml` and l-faulty with `--intent benchmarks/topology-l/intent_l_faulty.yaml`)

- [ ] **Qualify the repeatability claim in Ch6 (V2 section)** - 3 of 12 models broke determinism; wherever the V2 text says identical repetitions prove repeatability, add the qualification from thesis-text-additions block 4b, Result 3
- [ ] **Optional: add phantom_policy_claims metric** to evaluate_explain.py (converts the manual check in explain-13m-results.md §4 into a reported metric; needs a re-run of evaluate only, not the campaign)

- [ ] **Paste thesis text additions** - docs/thesis-text-additions.md → Сродни истражувања, Заклучок, Ch5/6/7 (~1h; block 4 now includes real EXPLAIN table + figure + interpretation)
- [ ] **Commit new files** - `git add -A && git commit` after review (EXPLAIN infra + evidence + figure + docs)
- [ ] **Send Вовед + Методологија to prof. Дединец** - deadline 8 August per final-planner
- [ ] **Prepare defense presentation** - Slides + talking points for bachelor thesis defense

## Done (26 July)

- [x] ~~Boost report: gap analysis vs official description~~ (docs/boost-report.md)
- [x] ~~Pre-flight config validation - all PASS~~ (docs/tools/preflight_frr.py)
- [x] ~~2026 related work found: Cornetto, agentic repair, Network Arena~~
- [x] ~~EXPLAIN task infrastructure built + tested offline (63/63 tests pass)~~
- [x] ~~EXPLAIN campaign run live: 12/12 OK, evaluated~~ (docs/evidence/week6-explain/)
- [x] ~~Key finding: Qwen3 4B omitted the security policy in all 3 r1 runs (policy recall 0%) while structure was perfect - written up in text-additions block 4~~
- [x] ~~Ch6-style figure generated~~ (docs/figures/week6-explain/explain-quality.svg)

## Done (27 July, latest)

- [x] ~~All original 89 tests pass~~
- [x] ~~**GATE CONFUSION MATRIX: recall = 1.000** (51 TP, 0 FN over 156 gated runs) - the headline safety number~~
- [x] ~~Precision 0.637 investigated: the 29 "false positives" are correct topology/scope rejections not covered by the policy-only ground truth; reported as a lower bound~~
- [x] ~~Verbosity confound CONFIRMED significant (|r|=0.885 > 0.576): the safety metric partly measures caution~~
- [x] ~~Failure taxonomy: over-eagerness dominates (54 wrong proposals + 42 extraneous); only 3 refusals of legitimate requests~~
- [x] ~~Hardest cases: T9 conflicting_deny (0.17, 10/12 failed), T8 narrow_policy_probe (0.28, 9/12 failed)~~
- [x] ~~Macedonian Ch6 section 4c written with all of the above~~

## Done (27 July, later)

- [x] ~~PROPOSE campaign across 12 models: 360 live runs~~ (docs/evidence/week6-v2-12m/)
- [x] ~~Cross-task analysis: 648 runs joined, 12 models~~ (docs/cross-task-results.md)
- [x] ~~**KEY RESULT: system_policy_safety = 1.000 for all 12 models.** 9 of 12 produced intent-violating proposals; zero reached ACCEPTED~~
- [x] ~~Cross-task correlation NOT significant (|r|=0.329 < 0.576 critical at n=12) -> tasks are independent, both must be measured~~
- [x] ~~Confound identified and documented: policy-safety metric rewards models that propose less (CodeLlama over-proposes, Qwen3 proposes nothing extra)~~
- [x] ~~Uniqueness/contribution map written~~ (docs/uniqueness-and-contributions.md)

## Done (27 July)

- [x] ~~Scaled benchmark: 12 models x 3 suites x 3 reps = 288 live runs~~ (docs/evidence/week6-explain-13m/)
- [x] ~~Full analysis written~~ (docs/explain-13m-results.md)
- [x] ~~Ch6 12-model section in Macedonian + table + figure~~ (thesis-text-additions.md block 4b)
- [x] ~~Findings: 3 models never report policy (0% over 24 runs each); 2 unstable; 7 perfect. Zero hallucinations in 288 runs. 3 models broke determinism despite temp 0 + seed 42~~

## Done (28 July)

- [x] ~~Week 7 larger-topology Ansible multi-fault experiment implemented~~
- [x] ~~Checksum-bound intent bundle, explicit twelve-action human review, exact approval, serial reconciliation, verified stage timing, runtime validation, idempotency check, emergency recovery, pinned controller and container profiles, and independent evidence verification covered by 31 new offline tests~~
- [x] ~~Safety-layer ablation completed over all 360 preserved PROPOSE runs, with 10 dedicated tests and independently replayed gate reports~~
- [x] ~~Full offline suite passes: 130/130 tests (including 31 Week 7-specific tests)~~
- [x] ~~Single guarded live-run command added with isolated dependency setup, post-deploy clean-tree gate, independent verification, and automatic lab cleanup~~
- [x] ~~Macedonian Chapter 5 methodology text and guarded Chapter 6 results template prepared in `docs/thesis-text-additions.md`~~
- [x] ~~Final clean Week 7 live run independently verified: 3/3 faults detected, post-repair validation passed, and `changed=0` on all routers~~
- [x] ~~Measured Week 7 timings and results promoted into the Macedonian Chapter 6 text with the verified evidence SHA-256~~

## Waiting On

- [ ] Advisor feedback on Chapter 5 draft

## Someday

- [ ] Test with additional LLM models beyond Qwen
- [ ] Document lessons for future network automation + LLM work

## Done
