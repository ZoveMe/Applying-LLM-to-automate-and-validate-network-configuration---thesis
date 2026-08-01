# Glossary

## Thesis-Specific Terms
| Term | Meaning | Context |
|------|---------|---------|
| Intent | Machine-readable YAML declaration of network's expected state | intended_state.yaml - source of truth |
| Proposal | LLM's structured JSON response (PROPOSE/CLARIFY/REFUSE) | llm/prompt_template.txt generates schema |
| Gate | Deterministic pre-deployment validator | validation/static_gate.py - checks schema, topology, policy |
| Validator | Independent runtime checker against intent | validation/dynamic_validate.py - confirms actual network state |
| Evidence | Timestamped output directory with proposal, approval, Ansible results | docs/evidence/week5-v2/ |
| V2 Campaign | 60 live model outputs from 2 models × 10 cases × 3 reps | Controlled benchmark for evaluation |
| SHA-256 Binding | Checksum linking approval to specific proposal bytes | Prevents proposal tampering post-approval |
| Repair Scope | Exact list of changes the Ansible playbook is allowed to make | Single route: r1, 10.0.2.0/24, via 10.0.12.2 |
| Idempotent | Operation produces same result whether run once or multiple times | Repair playbook checks existing route before action |

## Lab Components
| Component | Function |
|-----------|----------|
| r1 | Client-side router (10.0.1.0/24), policy enforcement (iptables DROP) |
| r2 | Server/management router (10.0.2.0/24, 10.0.99.0/24) |
| h-client | Client endpoint (10.0.1.10) |
| h-server | Server endpoint (10.0.2.10) |
| h-mgmt | Management endpoint (10.0.99.10) |
| Transit | 10.0.12.0/30 connecting r1↔r2 |

## Safety Properties
| Property | Implementation |
|----------|----------------|
| No direct LLM execution | JSON proposal, no command interface |
| Structural restriction | Pydantic strict schema, unknown fields forbidden |
| Topology restriction | Known networks, per-router next hops validated |
| Policy protection | Mandatory allow/deny overlap checks |
| Human control | APPROVE token required before Ansible |
| Artifact integrity | SHA-256 digest of proposal |
| Deployment restriction | Exact-scope checks in orchestrator + Ansible |
| Independent verification | 7 intent-derived runtime checks |

## File Paths (Key Directories)
| Path | Purpose |
|------|---------|
| llm/ | LLM adapter (Ollama client), prompt template |
| validation/ | Schema (Pydantic), gate (static checks), validator (runtime checks) |
| ansible/ | Restricted playbooks (repair_route.yml) |
| intent/ | intended_state.yaml - source of truth |
| experiments/ | Demo workflows, evaluation scripts |
| docs/evidence/week5-v2/ | Preserved outputs: live-matrix/, e2e-live/ |
