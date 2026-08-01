# Thesis Boost Report — 26 July 2026

Scope: alignment check against the official title/description, config validation run, new related work (July 2026), and ranked improvement actions. Sources ingested: files.zip (final-planner, research-pack, thesis_outline, sources, all code), preview.docx (Ch5), thesis-net repo (Ch6 results, evidence, tests).

---

## 1. Alignment with the official thesis description

Official title: **Примена на големи јазични модели за автоматизација и валидација на мрежни конфигурации**

The official description promises three LLM roles:

| Promised role | Status in implementation |
|---|---|
| Толкување на мрежни барања (interpret requirements) | ✅ Covered — V2 campaign, PROPOSE/CLARIFY/REFUSE |
| **Објаснување на постоечки конфигурации (explain existing configs)** | ⚠️ **GAP — not implemented, not measured** |
| Подготовка на структурирани предлози (structured proposals) | ✅ Covered — Pydantic contract + gate |
| Ansible за автоматизирано управување и проверка | ✅ Covered — restricted playbooks, idempotency evidence |

**The one real gap:** config *explanation* is in the official description but nowhere in the pipeline or evaluation. Cheapest fix (½ day, no new tools): add an EXPLAIN task — feed r1/r2 `frr.conf` + the iptables policy to both models, ask for a structured explanation (per-line → purpose), have the gate cross-check claimed facts against `intended_state.yaml` (a factuality check: does the explanation mention all routes/ACL? does it hallucinate entities?). Even n=5 runs per model gives you a table and closes the gap. Alternative (zero work): explicitly scope it out in the Вовед — but adding it is stronger, and it reuses the existing schema/gate machinery.

## 2. Config validation run (network-config-validation skill, adapted to FRR)

Ran a pre-flight validation of `configs/r1/frr.conf` and `configs/r2/frr.conf` with check classes from industrial pre-deployment practice, adapted from Cisco IOS patterns:

| Check class | Result |
|---|---|
| Dangerous commands (reload, no interface, no ip forwarding, shutdown) | PASS — none present |
| Duplicate IPs across devices | PASS |
| Subnet overlaps across devices | PASS |
| Next-hop reachability (every static route NH on a connected subnet) | PASS |
| Route symmetry (r1→server has return route on r2) | PASS |
| Next hop inside destination prefix | PASS |

**Verdict: PASS.** Two uses for the thesis: (a) one paragraph in Ch5/Ch6 noting the baseline configs themselves were audited with industry-style pre-flight checks; (b) these six check classes are the natural "future gate extensions" list for Ch7 — your current gate checks schema/topology/policy; industrial gates additionally check dangerous-command patterns, overlap/duplication, and symmetry. Script preserved at `docs/tools/preflight_frr.py` (copy from /tmp if needed — ask Claude to re-save).

## 3. New related work found (verify before citing; all 2026)

1. **Cornetto** — Protogeros, Asadli, Hoffman, Vanbever (ETH Zürich): *Benchmarking LLM-Driven Network Configuration Repair* (arXiv:2604.22513). 231 repair problems, 20–754 node topologies, 9 LLMs. Findings: models "often introduce regressions", performance "degrades at scale", and — headline — **"reliable LLM-powered network automation requires integrating LLMs into iterative workflows guided by formal verification."** This is the strongest possible external validation of your architecture, from Vanbever's group. Add to Related Work cluster A; one positioning sentence: *Cornetto reaches at scale the same conclusion this thesis demonstrates end-to-end in miniature: LLM output must be contained by deterministic verification before deployment.*
2. **Evaluating Agentic Configuration Repair for Computer Networks** (arXiv:2606.06212) — agentic repair evaluation; contrast: agents iterate autonomously, your pipeline holds action at human approval (Parasuraman "management by consent" — you already have E1).
3. **A Network Arena for Benchmarking AI Agents on Network Troubleshooting** (arXiv:2512.16381) — further evidence the field is racing to evaluate agents; the missing piece in all three is your contribution: a **measured human-approval gate with cryptographic artifact binding** (SHA-256). None of them bind approval to proposal bytes.

Your positioning sentence just got stronger: benchmarks (NetConfEval, NetAgentBench, Cornetto), standards (IETF NMRG drafts), and HCI theory (Parasuraman) all converge on validate-then-approve — and your thesis is the small, working, *measured* instance with artifact integrity none of the benchmarks implement.

## 4. Ranked boost actions

| # | Action | Effort | Impact |
|---|---|---|---|
| 1 | Add EXPLAIN task (closes official-description gap) | ½ day | **High** — description compliance |
| 2 | Add Cornetto + 2606.06212 to Related Work (cluster A) | 1 h | **High** — 2026 currency, direct endorsement |
| 3 | Ch7 future-work paragraph: six industrial pre-flight check classes as gate extensions | 1 h | Medium — shows industrial awareness |
| 4 | Ch5/Ch6 sentence: baseline configs audited with pre-flight checks (PASS) | 15 min | Low-medium — rigor signal |
| 5 | SHA-256 approval binding as explicit *novelty claim* vs the three 2026 benchmarks | 30 min | Medium — sharpens contribution |

Items 2–5 are pure writing. Item 1 is the only code work and reuses existing machinery (ollama_client, schema, gate).

## 5. What I did NOT change

No code, configs, or thesis chapters were modified. Repo state untouched except this report. Per your rule: no new tools, no scope creep — every action above fits the locked stack (Containerlab/FRR, Ansible, Pydantic, Ollama).
