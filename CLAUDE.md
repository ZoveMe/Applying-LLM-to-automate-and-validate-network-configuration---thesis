# Memory

## Me
Damjan Mitrovski, Bachelor thesis on guarded LLM-assisted network automation. Building a safety-first system where local LLMs propose network changes, deterministic validators gate them, humans approve, and independent validation confirms the network matches intent.

## Projects
| Name | What | Status |
|------|------|--------|
| **Main Thesis** | Guarded LLM network automation with safety properties | Active, finalizing |
| **V2 Campaign** | 60 live outputs (2 models, 10 cases, 3 reps each) | Complete, analyzing |
| **Lab Setup** | Containerlab with FRRouting + Alpine hosts | Running |

## Key Concepts
| Term | Meaning |
|------|---------|
| **Intent** | Machine-readable YAML source of truth for network state |
| **Proposal** | Structured JSON from LLM (PROPOSE/CLARIFY/REFUSE) |
| **Gate** | Deterministic validator (schema, topology, policy checks) |
| **Ansible** | Restricted playbook that applies only pre-approved changes |
| **Validator** | Independent runtime check against intent (7 checks total) |

## Implementation Stack
- **Models**: Qwen 2.5-coder 7B, Qwen 3 4B (local Ollama)
- **Validation**: Python (Pydantic + custom checks)
- **Deployment**: Ansible (idempotent, restricted scope)
- **Lab**: Containerlab + Docker (r1, r2, 3 hosts)
- **Evidence**: Timestamped JSON + SHA-256 approval bindings

## Thesis Structure
- Ch1-4: Background, design, related work (done)
- **Ch5: Implementation** (preview.docx - read)
- **Ch6: Evaluation** (IN PROGRESS - 60 live outputs to analyze)
- Ch7: Discussion, future work

## Preferences
- Concise, direct communication
- Focus on evidence and reproducibility
- Code quality over speed for final submission

---

**Full glossary**: memory/glossary.md
**Project details**: memory/projects/
