# thesis-net — LLM-assisted network configuration & validation (lab)

Bachelor thesis practical environment for:
**„Примена на големи јазични модели за автоматизација и валидација на мрежни конфигурации".**

This repo holds the reproducible lab. The whole topology is code, so anyone
(including your committee) can rebuild it from these files.

## What's here

```
thesis-net/
├── topology.clab.yml        # Containerlab topology: 2 routers, 3 segments, 3 hosts
├── configs/
│   ├── daemons              # FRR daemons file (shared by r1 and r2)
│   ├── r1/frr.conf          # r1 routing config
│   └── r2/frr.conf          # r2 routing config
├── policies/
│   ├── apply-policy.sh      # apply intended ACL (client -> management DENY)
│   └── remove-policy.sh     # remove it (for fault-injection experiments)
├── verify.sh                # reachability check vs intended policy (validation preview)
├── ansible/
│   └── inventory.yml        # inventory stub (built out in Week 3)
└── docs/
    ├── thesis_outline.md    # research questions, scope, chapter outline (Macedonian)
    └── sources.md           # starter bibliography
```

## The lab

```
  client 10.0.1.0/24                         server 10.0.2.0/24
  h-client(.10) ── r1 ──[10.0.12.0/30]── r2 ── h-server(.10)
                                           └─── h-mgmt(.10)
                                         management 10.0.99.0/24
```

Intended access policy:
- client → server: **allowed** (web)
- client → management: **denied**
- server → client: allowed (return path)

## Prerequisites

- Docker
- Containerlab — install: `bash -c "$(curl -sL https://get.containerlab.dev)"`
- Ansible and Ollama — only needed for their respective live experiments

## Run it

From inside the `thesis-net/` folder:

```bash
# 1. deploy the lab
sudo clab deploy -t topology.clab.yml

# 2. apply the intended access policy
bash policies/apply-policy.sh

# 3. validate the network against the policy
bash verify.sh
```

Expected result: all three checks PASS → "network MATCHES the intended policy."

Try the fault-injection idea right away:

```bash
bash policies/remove-policy.sh   # break the policy
bash verify.sh                   # the client->management check should now FAIL
bash policies/apply-policy.sh    # restore it
```

Tear down when done: `sudo clab destroy -t topology.clab.yml`

## Guarded repair demonstration

After the lab is deployed, the intended policy is applied, and the baseline
validator passes, run the reusable end-to-end demonstration:

```bash
python3 experiments/guarded_repair_demo.py
```

The script removes the allow-listed `r1` route to `10.0.2.0/24`, proves that
the runtime validator detects the fault, asks Ollama for a structured repair,
and enforces the schema and deterministic policy gate. After the operator
types `APPROVE`, the orchestrator passes only the exact route
`10.0.2.0/24 via 10.0.12.2` to `ansible/repair_route.yml`. The playbook checks
the allow-list again and applies the route idempotently. Failure, rejection,
or interruption triggers an independent recovery path.

The responsibility boundary is deliberate:

- **Ollama/LLM:** proposes a structured repair and rationale; it cannot execute.
- **Deterministic gate:** validates schema, topology, scope, and safety rules.
- **Human operator:** approves the exact proposal and its recorded checksum.
- **Ansible:** applies the approved change predictably and idempotently.
- **Runtime validator:** independently proves whether all seven intent checks
  pass after deployment.

For a model-independent rehearsal, use the checked offline fixture. This still
requires the live Containerlab, but it does not call Ollama:

```bash
python3 experiments/guarded_repair_demo.py \
  --mock examples/suggestion_repair_route.json
```

Every run writes to a new timestamped directory under
`docs/evidence/guarded-repair-runs/`; existing evidence is never overwritten.

## Larger-topology Ansible multi-fault experiment

The Week 7 extension raises the configuration difficulty without changing the
frozen LLM campaigns. It uses the optional three-router topology and injects
three simultaneous faults: a missing route on `r1`, a wrong next hop on `r2`,
and a missing deny policy on `r3`.

The repair is limited to the fixed intent bundle in
`examples/topology_l_intent_bundle.json`. Human approval is bound to that
bundle's SHA-256, Ansible independently checks the same hash, routers are
reconciled serially, runtime behavior is validated, and a second run must
report `changed=0` on all three routers. A failure after fault injection
activates an emergency deterministic reconciliation.

Before the approval token is accepted, the runner prints all ten route actions
and both deny-policy actions. The evidence record stores that exact reviewed
list, its count, and the same bundle checksum.

Each recorded command also includes monotonic duration in seconds plus
microsecond-resolution start and completion timestamps. The independent
verifier rejects invalid timing and exposes reconciliation, validation,
idempotency, and total recorded automation time for Chapter 6.

The live runner also refuses a dirty Git worktree. After a completed run,
`experiments/verify_topology_l_ansible_evidence.py` independently checks the
recorded commands, approval checksum, fault-time failure, post-repair success,
and idempotency before producing any thesis-ready derived summary.

The complete live protocol is available as one guarded command:

```bash
bash scripts/run_topology_l_live.sh --setup
```

It creates an isolated pinned Ansible environment, deploys Topology L, runs the
interactive experiment, invokes the independent verifier, and destroys the lab
even when a stage fails. Generated `clab-*` runtime directories are ignored at
any repository depth so deployment cannot invalidate the clean-commit gate.

For reproducibility on the existing Python 3.10 WSL controller, the experiment
pins `ansible-core 2.17.14` in `requirements-ansible.txt` and
`community.docker 5.2.1` in `ansible/requirements.yml`. The runner and
independent verifier both reject a different version before accepting evidence.
The topology also pins `quay.io/frrouting/frr:9.1.1` and `alpine:3.20.10`;
preflight records and verifies the immutable Docker image ID of every node.

See `docs/week7-ansible-multifault.md` for the hypotheses, acceptance criteria,
and exact live protocol. Until a new live evidence directory has been reviewed,
the repository claims only that this experiment is implemented and passes its
offline safety tests.

## First-deploy notes (read if something doesn't work)

Containerlab + container images vary slightly between versions, so the first
deploy sometimes needs a small nudge. Common ones:

- **Host IPs/routes didn't apply** (a host `exec` raced the interface creation):
  re-run `sudo clab deploy -t topology.clab.yml`, or set them by hand, e.g.
  `docker exec clab-thesis-net-h-client ip addr add 10.0.1.10/24 dev eth1`.
- **Router didn't pick up its config**: check it with
  `docker exec -it clab-thesis-net-r1 vtysh -c "show ip route"` and
  `... -c "show interface brief"`.
- **`apply-policy.sh` can't install iptables**: the lab's management network
  needs outbound internet for `apk add`. If it's blocked, tell me your setup
  and we'll switch to an image that ships iptables.

Paste any error output and I'll help you fix it.
