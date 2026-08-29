# Extended lab — 3 routers, 3 switches, 3 PCs, 2 servers

A larger topology built alongside the thesis-core lab. **The core lab is
untouched**: `topology.clab.yml` and `intent/intended_state.yaml` still match
the 848 preserved runs behind Chapter 6.

```
        pc1   pc2                       srv1  srv2
          \   /                           \   /
           sw1                             sw2
            |                               |
           r1 ─────── 10.0.12.0/30 ───────  r2
             \                             /
        10.0.13.0/30             10.0.23.0/30
               \                       /
                \────────  r3  ───────/
                           |
                          sw3
                           |
                          pc3
```

| | |
|---|---|
| Routers | r1, r2, r3 — meshed as a triangle |
| Switches | sw1, sw2, sw3 — Linux bridges, no IP addresses |
| PCs | pc1 (10.0.1.10), pc2 (10.0.1.11), pc3 (10.0.99.10) |
| Servers | srv1 (10.0.2.10), srv2 (10.0.2.11) |
| Segments | client 10.0.1.0/24 · server 10.0.2.0/24 · management 10.0.99.0/24 |
| Transits | r1–r2 10.0.12.0/30 · r1–r3 10.0.13.0/30 · r2–r3 10.0.23.0/30 |
| Policy | client → management **denied** on r1 |
| Checks | 6 reachability (R1–R6) · 7 config facts (C1–C7) |

## Two things this adds over the core lab

**Layer-2 switching.** Each switch is an Alpine container running a Linux
bridge with all its ports enslaved to `br0`. Because the switch holds no IP
address, the router sees one segment regardless of how many hosts sit behind
it — which is what lets `client` hold two PCs and `server` hold two servers.
Check `R4` (pc1 → pc2) and `R5` (srv1 → srv2) prove the bridges actually
forward.

**Router redundancy.** The three routers form a triangle, so every pair is
directly connected. The core lab is a chain, where losing the single transit
link partitions the network. Note that this is *physical* redundancy only:
routing is still static, so a failed link is not automatically routed around.
Making that automatic would require a dynamic protocol, which is deliberately
out of scope.

## Deploy

```bash
cd labs/topology-xl
sudo clab deploy -t topology-xl.clab.yml
bash apply-policy.sh
```

Container names are prefixed `clab-thesis-net-xl-`.

## Use the tooling against it

The validator and dashboard default to the core lab's container names. Point
them at this lab with an environment variable and the intent flag:

```bash
export CLAB_PREFIX=clab-thesis-net-xl-

python3 validation/dynamic_validate.py --intent labs/topology-xl/intent_xl.yaml
python3 experiments/live_llm_dashboard.py --intent labs/topology-xl/intent_xl.yaml --open
```

The dashboard's **Review topology** panel reads the topology file directly, so
the diagram, the counters and the strengths/limitations all reflect this lab.

Visual topology graph:

```bash
sudo clab graph -t topology-xl.clab.yml     # http://localhost:50080
```

## Tear down

```bash
sudo clab destroy -t topology-xl.clab.yml --cleanup
```

## Verified before deployment

Checked mechanically against the topology and intent files: no duplicate
interface assignments; router interface declarations match the links exactly;
every router has a route to every segment it does not host (full mesh
reachability); every next hop resolves on a directly connected transit; no
overlapping subnets.

## Relationship to the thesis

This lab is **not** part of the measured evidence. All results in Chapter 6
were produced on the two-router core lab, and its intent file is part of that
measurement. Use this one for demonstration, for screenshots, and as the
starting point for the scale work described in §8.3 — not for re-running the
frozen campaigns.
