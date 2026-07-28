# Week 7 — Larger-Topology Ansible Multi-Fault Recovery

## Status

Implementation is complete and all 120 offline tests pass, including 31 tests
specific to this extension. Live results must not be
reported until the Containerlab experiment is executed and its new evidence
directory is reviewed.

This experiment is additive. It does not regenerate, overwrite, or reinterpret
the frozen Week 4, Week 5, or Week 6 model evidence.

## Research purpose

The earlier guarded repair demonstration intentionally repaired one missing
route on a two-router topology. This experiment tests whether the same
deterministic-control philosophy scales to a more difficult configuration:

- three routers;
- five endpoint segments;
- two transit networks;
- ten static-route facts;
- two security-policy facts; and
- three simultaneous faults spanning routing and access control.

The LLM is not given execution authority. Week 7 isolates and evaluates the
Ansible execution and validation layers. This distinction is important: it
tests whether the deterministic part of the architecture can safely reconcile a
larger approved intent bundle even when the model campaign remains frozen.

## Injected fault profile

| Fault | Device | Injected condition | Expected effect |
|---|---|---|---|
| F1 | `r1` | Remove `10.0.3.0/24 via 10.0.12.2` | Client loses the intended DMZ path |
| F2 | `r2` | Replace `10.0.3.0/24 via 10.0.23.2` with wrong next hop `10.0.12.1` | Control-plane route contradicts topology |
| F3 | `r3` | Remove the sensors-to-client `DROP` rule | A prohibited flow becomes reachable |

The fault-injection playbook requires both the exact profile identifier and the
literal confirmation `INJECT_THESIS_FAULTS`. It targets only the fixed
Containerlab inventory.

## Guarded reconciliation

The approved source of truth is:

`examples/topology_l_intent_bundle.json`

It contains exactly ten routes and two deny policies derived from
`benchmarks/topology-l/intent_l.yaml`. Before Ansible changes a router:

1. the operator approves the exact bundle SHA-256;
2. the orchestrator verifies the bundle has not changed;
3. the playbook independently recalculates the same SHA-256;
4. the playbook rejects an incorrect profile, checksum, inventory, route count,
   policy count, device, or action;
5. routers are reconciled serially with `any_errors_fatal`;
6. every route and policy is verified after application; and
7. the complete playbook is run again and must report `changed=0` for all three
   routers.

If the experiment fails or is interrupted after fault injection, the
orchestrator runs the fixed reconciliation as an emergency recovery path.

## Hypotheses and acceptance criteria

### H1 — Multi-fault detection

All three injected faults must be independently verified, and the full intent
validation must fail before repair.

### H2 — Guarded recovery

After exact human approval, the checksum-bound Ansible reconciliation must
restore all ten routes, both security policies, and the tested reachability
matrix.

### H3 — Idempotency

A second reconciliation must complete successfully with:

```text
r1 changed=0
r2 changed=0
r3 changed=0
```

### H4 — Fail-closed behavior

An incorrect approval token, profile, checksum, bundle shape, or inventory must
prevent the experimental repair. A partial failure must activate emergency
reconciliation.

## Live protocol

Keep the existing Week 6 evidence untouched. From the repository root:

```bash
python3 -m venv .venv-ansible
source .venv-ansible/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-ansible.txt
ansible-galaxy collection install -r ansible/requirements.yml

git status --short

cd benchmarks/topology-l
sudo clab deploy -t topology-l.clab.yml
cd ../..

python3 experiments/topology_l_ansible_demo.py
```

The experiment refuses to start unless `git status --short` is empty. This
ensures the approval record names a commit that contains the exact experimental
code rather than an uncommitted working copy.

The controller profile is frozen to `ansible-core 2.17.14` and
`community.docker 5.2.1`, matching the existing Python 3.10 WSL environment.
The preflight and independent verifier reject other versions. Ansible Core 2.17
is end-of-life, so this compatibility profile is limited to the isolated,
non-production thesis lab; future continuation should move to a supported
Python and Ansible controller together.

The topology uses fixed images rather than mutable `latest` tags:
`quay.io/frrouting/frr:9.1.1` for the routers and `alpine:3.20.10` for the
endpoints. Preflight records the configured tag and immutable image ID for all
eight containers and refuses a mismatch before fault injection.

At the approval prompt, review the printed SHA-256 and enter:

```text
APPROVE_TOPOLOGY_L_REPAIR
```

The prompt first prints the exact twelve checksum-bound actions: ten routes and
two deny policies. The approval record stores the same ordered action list,
action count, source commit, and bundle SHA-256. The independent verifier
rejects a missing, shortened, reordered, or modified action list.

Every command record contains microsecond-resolution start and completion
timestamps plus a monotonic `duration_seconds`. The verifier rejects negative,
non-finite, reversed, or internally inconsistent timing and produces measured
durations for the approved reconciliation, post-repair validation, idempotency
rerun, and all recorded automated stages.

After the experiment:

```bash
sudo clab destroy -t benchmarks/topology-l/topology-l.clab.yml --cleanup
```

The orchestrator writes a new timestamped evidence directory under:

`docs/evidence/week7-ansible-multifault-runs/`

Independently verify the completed run and write only derived outputs outside
the raw evidence directory:

```bash
RUN_DIR="$(find docs/evidence/week7-ansible-multifault-runs \
  -mindepth 1 -maxdepth 1 -type d | sort | tail -n1)"
RUN_ID="$(basename "$RUN_DIR")"

python3 experiments/verify_topology_l_ansible_evidence.py "$RUN_DIR" \
  --json-out "docs/derived/week7-ansible-multifault/${RUN_ID}-verification.json" \
  --markdown-out "docs/derived/week7-ansible-multifault/${RUN_ID}-verification-mk.md"
```

The independent verifier checks the full stage sequence, clean source commit,
Containerlab version, exact Ansible and collection versions, pinned container
tags and image IDs, exact commands and playbooks, approval checksum, expected
failed validation after fault injection, successful post-repair validation,
absence of emergency recovery, and `changed=0` on all three routers. It refuses
to write derived outputs inside the raw evidence directory.

## Evidence sequence

1. `00-preflight.json`
2. `01-baseline-reconciliation.json`
3. `02-baseline-validation.json`
4. `03-fault-injection.json`
5. `04-fault-profile-verified.json`
6. `05-intent-validation-failed.json`
7. `06-human-approval.json`
8. `07-approved-reconciliation.json`
9. `08-post-repair-validation.json`
10. `09-idempotency.json`

`99-emergency-recovery.json` is written only if the normal experiment does not
reach a fully validated and idempotent result.

## Reporting rule

Until a live evidence directory passes the independent verifier and manual
review, the thesis may state only that the experiment was designed and
implemented. It must not claim successful multi-fault recovery, live
validation, or idempotency as a measured result.
