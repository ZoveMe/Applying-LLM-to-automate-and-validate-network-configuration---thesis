# Main Thesis: Guarded LLM-Assisted Network Automation

**Full Title:** Guarded LLM-Assisted Network Automation and Validation

**Status:** Active - Finalizing for defense
**Author:** Damjan Mitrovski
**Degree:** Bachelor Thesis
**Date Started:** Early 2026 (estimated)
**Expected Completion:** July 2026

## Problem Statement
Can a local LLM assist with network configuration while remaining separated from direct control? How do we keep the LLM untrusted but useful?

## Core Contribution
A reusable architectural pattern where:
1. **LLM proposes** structured configuration changes (JSON)
2. **Deterministic gates** validate topology, policy, schema
3. **Humans authorize** with cryptographically bound approval
4. **Ansible deploys** within restricted scope
5. **Independent validator** confirms network matches intent

## Five Design Principles
1. LLM is untrusted (output is data, never executable)
2. Intent is source of truth (machine-readable YAML)
3. Every proposal is constrained (schema, topology, policy checks)
4. Deployment requires human authorization (explicit APPROVE token)
5. Success measured from network (runtime validation, not automation success)

## Thesis Chapters
- **Ch1-4:** Background, design, related work (✓ DONE)
- **Ch5:** Implementation (✓ DONE - see preview.docx)
- **Ch6:** Evaluation (🔄 IN PROGRESS - analyzing 60 live outputs)
- **Ch7:** Discussion & future work (TODO)

## Key Experiments
### V2 Campaign
- **Models:** Qwen 2.5-coder 7B, Qwen 3 4B (local Ollama)
- **Cases:** 10 benchmark scenarios (different failure types)
- **Repetitions:** 3 per case
- **Total outputs:** 60 live model responses
- **Status:** ✓ Preserved in docs/evidence/week5-v2/

### Evidence Preservation
- Raw outputs protected by SHA256SUMS.txt
- Evaluation done offline from preserved data (no re-running models)
- Reproducibility: experiments/generate_thesis_results.py regenerates Chapter 6 tables

## Lab Environment
- **Containerlab** on Docker (topology.clab.yml)
- **FRRouting** routers (r1, r2) + Alpine hosts (h-client, h-server, h-mgmt)
- **7 validation checks** (3 reachability R1-R3, 4 configuration C1-C4)
- **Deterministic:** Same input always produces same output

## Remaining Work
1. Analyze V2 campaign outputs (60 responses)
2. Compute metrics: proposal quality, gate rejection rates, validator success
3. Generate Chapter 6 tables & figures
4. Write discussion & lessons learned (Ch7)
5. Prepare defense presentation
6. Final code review & polish

## Key Files
| File | Purpose |
|------|---------|
| preview.docx | Chapter 5 implementation (already read) |
| intent/intended_state.yaml | Network intent declaration |
| llm/ollama_client.py | Local model interface |
| validation/schema.py | Pydantic proposal contract |
| validation/static_gate.py | Pre-deployment deterministic validator |
| validation/dynamic_validate.py | Independent runtime validator |
| ansible/repair_route.yml | Restricted deployment playbook |
| experiments/guarded_repair_demo.py | End-to-end workflow |
| docs/evidence/week5-v2/ | Preserved experimental outputs |

## Success Criteria
- ✓ Implementation complete and working
- ✓ Evidence preserved and reproducible
- ✓ Safety properties demonstrated (no uncontrolled LLM execution)
- 🔄 Evaluation analysis complete (IN PROGRESS)
- TODO Defense presentation ready

## Defense Talking Points
1. **Problem:** LLMs useful for network tasks but risky if given direct control
2. **Solution:** Guard with deterministic validation + human approval + independent verification
3. **Evidence:** 60 live experiments showing LLM proposals integrate safely into pipeline
4. **Impact:** Reusable pattern for probabilistic systems in safety-critical domains
5. **Limitations:** Demonstrated on small lab network, single operation (route repair), requires careful scoping
