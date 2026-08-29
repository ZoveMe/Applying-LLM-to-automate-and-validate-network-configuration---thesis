# Extending the lab — routers, switches, servers, PCs

How to grow `topology.clab.yml` beyond two routers and three hosts. Every
addition touches **three** files, and skipping the third is the usual cause of
"I added a device and now the validator fails."

| File | What it holds |
|---|---|
| `topology.clab.yml` | nodes and links — the physical layout |
| `configs/<device>/frr.conf` | routing and interface addressing |
| `intent/intended_state.yaml` | what the validator expects to be true |

The dashboard reads all three: the diagram comes from the topology file, the
statistics from the intent file, and the live state from the running devices.

---

## The addressing plan

Keep the scheme so the existing checks stay meaningful.

| Purpose | Range | In use |
|---|---|---|
| Access segments | `10.0.<n>.0/24` | 1 client · 2 server · 99 management |
| Transit links | `10.0.<a><b>.0/30` | 12 (r1–r2) · 23 (r2–r3) |
| Free for new segments | `10.0.3.0/24` … `10.0.9.0/24` | — |

Convention: gateway is `.1`, first host is `.10`.

---

## Adding a PC or server

The simplest change. A host is an Alpine container with one interface.

**1. `topology.clab.yml` — add the node:**

```yaml
    h-dev:
      kind: linux
      image: alpine:latest
      exec:
        - ip addr add 10.0.5.10/24 dev eth1
        - ip route add 10.0.0.0/16 via 10.0.5.1
```

The second command is the host's default route back to its gateway. Without
it the host answers pings from its own subnet and nothing else.

**2. Add the link**, using the next free interface on the router:

```yaml
    - endpoints: ["r2:eth4", "h-dev:eth1"]
```

**3. `configs/r2/frr.conf` — give the router that interface:**

```
interface eth4
 description dev-segment
 ip address 10.0.5.1/24
!
```

**4. Every *other* router needs a route to the new segment:**

```
! in configs/r1/frr.conf
ip route 10.0.5.0/24 10.0.12.2
```

**5. `intent/intended_state.yaml` — declare it:**

```yaml
segments:
  dev:
    subnet: 10.0.5.0/24
    connected_to: r2
    gateway: 10.0.5.1
    host: 10.0.5.10

config_facts:
  - {id: C5, node: r1, kind: route, prefix: 10.0.5.0/24, via: 10.0.12.2,
     why: "r1 reaches the dev segment via r2"}

reachability:
  - {id: R4, src: h-client, dst: 10.0.5.10, expect: reachable, method: ping,
     why: "client -> dev is allowed"}
```

**6. Redeploy and verify:**

```bash
sudo clab deploy -t topology.clab.yml --reconfigure
bash policies/apply-policy.sh && bash verify.sh
python3 experiments/reset_lab.py --check
```

---

## Adding a router

More involved, because routing must be symmetric — every router needs a path
to every segment, in both directions.

**1. Node and transit link:**

```yaml
    r3:
      kind: linux
      image: frrouting/frr:latest
      binds:
        - configs/r3/frr.conf:/etc/frr/frr.conf
        - configs/daemons:/etc/frr/daemons
```
```yaml
    - endpoints: ["r2:eth4", "r3:eth1"]
```

**2. `configs/r3/frr.conf`:**

```
frr version 9.1
frr defaults traditional
hostname r3
service integrated-vtysh-config
!
interface eth1
 description link-to-r2
 ip address 10.0.23.2/30
!
interface eth2
 description dmz-segment
 ip address 10.0.3.1/24
!
ip forwarding
!
ip route 10.0.1.0/24 10.0.23.1
ip route 10.0.2.0/24 10.0.23.1
ip route 10.0.99.0/24 10.0.23.1
!
line vty
!
```

**3. Return routes on the existing routers** — this is the step most often
missed. r1 and r2 both need to know how to reach anything behind r3:

```
! configs/r1/frr.conf
ip route 10.0.3.0/24 10.0.12.2

! configs/r2/frr.conf   (r2 is adjacent to r3)
ip route 10.0.3.0/24 10.0.23.2
```

**4. Declare every new route fact in the intent file**, one `config_facts`
entry per router per destination. A three-router topology needs roughly
`routers × segments` route facts.

A worked example already exists: `benchmarks/topology-l/` is a complete
three-router, five-segment lab with its own intent file
(`intent_l.yaml`) and a faulty variant. Copy its structure rather than
starting from scratch.

---

## Adding a switch

Containerlab has no built-in switch node, so use a **Linux bridge container**.
This is what lets several PCs share one segment instead of each needing its
own router port.

```yaml
    sw1:
      kind: linux
      image: alpine:latest
      exec:
        - apk add --no-cache bridge-utils
        - ip link add name br0 type bridge
        - ip link set br0 up
        - ip link set eth1 master br0
        - ip link set eth2 master br0
        - ip link set eth3 master br0
        - ip link set eth1 up
        - ip link set eth2 up
        - ip link set eth3 up
```

```yaml
    - endpoints: ["r2:eth2", "sw1:eth1"]
    - endpoints: ["sw1:eth2", "h-server:eth1"]
    - endpoints: ["sw1:eth3", "h-server2:eth1"]
```

Both hosts now sit in `10.0.2.0/24` behind one router interface. The router
config does not change — from r2's point of view it is still one segment.

Note that the switch is transparent to the intent file: it carries no IP
address and appears in no `config_facts`. It will show in the dashboard
diagram as a node, and `review_topology` will stop reporting "no layer-2
switching" as a limitation.

---

## After any change — the checklist

1. `sudo clab deploy -t topology.clab.yml --reconfigure`
2. `bash policies/apply-policy.sh && bash verify.sh`
3. `python3 experiments/reset_lab.py --check` — should report `MATCHES_INTENT`
4. Restart the dashboard and press **Review topology** — the new devices
   appear in the diagram and the counters update.

If the validator fails after adding a device, the cause is almost always one
of three things: a missing return route on a distant router, a host without a
default route, or a `config_facts` entry declared in intent but not actually
configured.

---

## A caution for the thesis

Extending the lab changes what the frozen evidence describes. The 848 recorded
runs were measured against the **two-router topology**, and the intent file is
part of that measurement. If you grow the lab, do it in a copy — as
`benchmarks/topology-l/` does — so `topology.clab.yml` and
`intent/intended_state.yaml` continue to match the results in Chapter 6.
