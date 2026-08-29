#!/usr/bin/env python3
"""experiments/live_llm_dashboard.py — browser dashboard for the live lab.

A local web console for talking to a model that can configure the running
laboratory. You type a request, the model always answers in plain language,
and if it proposes commands you decide whether to run them. The independent
runtime validator reports what the network state became.

This is the ungoverned condition with a human as the only control — the
workflow this thesis argues is insufficient — and it doubles as the live
demonstration piece: type a plausible request, watch a model produce something
confident and wrong, watch the validator catch it.

Standard library only. Binds to localhost.

CONTAINMENT (laboratory safety)
-------------------------------
  * only vtysh, ip and iptables may execute; anything else is refused
  * config-persisting commands refused (router configs are host bind-mounted)
  * commands run as argument lists, never through a shell
  * Reset restores the intended state and verifies it

USAGE
-----
    python3 experiments/live_llm_dashboard.py
    python3 experiments/live_llm_dashboard.py --port 8080 --open
"""
import argparse
import hashlib
import json
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments"))

from live_counterfactual_ablation import apply_proposal_ungoverned  # noqa: E402
from live_freeform_ablation import (  # noqa: E402
    OLLAMA_URL,
    PREFIX,
    call_ollama,
    classify,
    load_intent,
    parse_console_response,
    reset_and_verify,
    run_in,
    run_validator,
    screen_command,
)

sys.path.insert(0, str(REPO_ROOT))
from llm.ollama_client import run_pipeline  # noqa: E402

TEMPLATE = REPO_ROOT / "llm" / "console_template.txt"
DEFAULT_INTENT = REPO_ROOT / "intent" / "intended_state.yaml"
DEFAULT_MODEL = "qwen2.5-coder:7b-instruct-q4_K_M"
SCRATCH = REPO_ROOT / "docs" / "evidence" / "_dashboard-scratch"

STATE = {"intent_path": DEFAULT_INTENT, "intent": None,
         "topology_path": None, "transcript": [],
         # Секоја промена применета преку интерфејсот, со состојбата пред и по
         # неа. Ова е доказот што се прикажува во панелот со историја.
         "history": []}


def find_topology_for(intent_path: Path) -> Path:
    """Locate the .clab.yml that belongs with an intent file.

    The topology and intent describe the same lab, so they live together.
    Look beside the intent file first; fall back to the repository default.
    """
    candidates = sorted(intent_path.resolve().parent.glob("*.clab.yml"))
    return candidates[0] if candidates else REPO_ROOT / "topology.clab.yml"
LOCK = threading.Lock()


def installed_models() -> list[str]:
    try:
        request = urllib.request.Request(OLLAMA_URL.rstrip("/") + "/api/tags")
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
        return sorted(m.get("name", m.get("model", "")) for m in data.get("models", []))
    except Exception:  # noqa: BLE001
        return []


TOPOLOGY_FILE = REPO_ROOT / "topology.clab.yml"


def read_topology(path: Path = TOPOLOGY_FILE) -> dict:
    """Nodes and links straight from the Containerlab definition."""
    data = yaml.safe_load(path.read_text())
    topo = data.get("topology", {})
    nodes = {}
    for name, spec in (topo.get("nodes") or {}).items():
        image = str(spec.get("image", ""))
        exec_lines = " ".join(str(x) for x in (spec.get("exec") or []))
        # A switch is an Alpine container whose exec builds a Linux bridge and
        # enslaves its ports; it carries no IP address of its own.
        if "type bridge" in exec_lines or "master br" in exec_lines:
            kind = "switch"
        elif "frr" in image:
            kind = "router"
        elif "alpine" in image:
            kind = "host"
        else:
            kind = "node"
        nodes[name] = {"kind": kind, "image": image, "interfaces": []}
    links = []
    for link in (topo.get("links") or []):
        endpoints = link.get("endpoints") or []
        if len(endpoints) != 2:
            continue
        pairs = []
        for ep in endpoints:
            node, _, iface = ep.partition(":")
            pairs.append((node, iface))
            if node in nodes:
                nodes[node]["interfaces"].append(iface)
        links.append({"a": pairs[0][0], "a_if": pairs[0][1],
                      "b": pairs[1][0], "b_if": pairs[1][1]})
    return {"lab": data.get("name", "lab"), "nodes": nodes, "links": links}


def topology_svg(topo: dict, state: dict) -> str:
    """Diagram of the lab.

    Layout is layered outward from the routers: routers on the centre line,
    whatever attaches to them one step out, and whatever attaches to those a
    further step out. This handles router -> switch -> host chains, not just
    hosts wired straight into a router port.
    """
    kinds = {n: v["kind"] for n, v in topo["nodes"].items()}
    routers = sorted(n for n, k in kinds.items() if k == "router")

    adj = {n: set() for n in topo["nodes"]}
    for link in topo["links"]:
        if link["a"] in adj and link["b"] in adj:
            adj[link["a"]].add(link["b"])
            adj[link["b"]].add(link["a"])

    # Breadth-first layering from the routers.
    layer = {r: 0 for r in routers}
    parent = {}
    frontier = list(routers)
    while frontier:
        nxt = []
        for node in frontier:
            for peer in sorted(adj[node]):
                if peer not in layer:
                    layer[peer] = layer[node] + 1
                    parent[peer] = node
                    nxt.append(peer)
        frontier = nxt
    for node in topo["nodes"]:            # anything unreachable
        layer.setdefault(node, 1)

    depth = max(layer.values()) if layer else 0
    W = max(760, 200 * max(1, len(routers)))
    H = 200 + depth * 110
    pos = {}
    gap = W / (len(routers) + 1)
    for i, r in enumerate(routers, start=1):
        pos[r] = (gap * i, H / 2)

    # Place each subsequent layer around its parent, alternating up and down.
    for lvl in range(1, depth + 1):
        members = sorted(n for n, l in layer.items() if l == lvl)
        by_parent = {}
        for n in members:
            by_parent.setdefault(parent.get(n), []).append(n)
        for par, group in by_parent.items():
            if par not in pos:
                continue
            px, py = pos[par]
            up = layer.get(par, 0) == 0 or py < H / 2
            for j, n in enumerate(sorted(group)):
                spread = (j - (len(group) - 1) / 2) * 108
                dy = -100 if (up if lvl == 1 else py < H / 2) else 100
                pos[n] = (px + spread, py + dy)

    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;height:auto">']
    for link in topo["links"]:
        a, b = link["a"], link["b"]
        if a not in pos or b not in pos:
            continue
        (x1, y1), (x2, y2) = pos[a], pos[b]
        both_routers = a in routers and b in routers
        parts.append(
            f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
            f'stroke="{"#4c8dff" if both_routers else "#39414f"}" '
            f'stroke-width="{3 if both_routers else 2}"/>')
    STYLE = {
        "router": ("#4c8dff", "#4c8dff", "#ffffff", 74, 34, 8),
        "switch": ("#1f6f4a", "#35c67a", "#d6ffe8", 74, 26, 4),
        "host":   ("#2a3140", "#4c8dff", "#c8d0dd", 82, 26, 6),
        "node":   ("#2a3140", "#6b7280", "#c8d0dd", 82, 26, 6),
    }
    for name, (x, y) in pos.items():
        fill, stroke, text, rw, rh, rr = STYLE.get(kinds.get(name, "node"),
                                                   STYLE["node"])
        # Секој јазол е и копче: кликнувањето го отвора панелот за уредот.
        # `data-node` е името што панелот го бара, па дијаграмот и списокот
        # со уреди зборуваат за иста работа.
        parts.append(f'<g class="tnode" data-node="{name}" tabindex="0">')
        parts.append(
            f'<rect x="{x - rw/2:.0f}" y="{y - rh/2:.0f}" width="{rw}" '
            f'height="{rh}" rx="{rr}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="1"/>')
        parts.append(
            f'<text x="{x:.0f}" y="{y + 4:.0f}" text-anchor="middle" '
            f'font-family="SF Mono,Menlo,monospace" font-size="11.5" '
            f'fill="{text}">{name}</text>')
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)


def review_topology(topo: dict, state: dict, intent: dict) -> dict:
    """Deterministic assessment. Facts first; the model narrates them after.

    Every claim below is derived either from the topology definition, from
    the intent file, or from what the routers reported in `state`. Nothing
    is asserted about a design the laboratory may not have: when the live
    tables cannot be read, the routing claims are left out rather than
    guessed.
    """
    routers = [n for n, v in topo["nodes"].items() if v["kind"] == "router"]
    switches = [n for n, v in topo["nodes"].items() if v["kind"] == "switch"]
    hosts = [n for n, v in topo["nodes"].items()
             if v["kind"] not in ("router", "switch")]
    segments = intent.get("segments", {})
    routes = [f for f in intent.get("config_facts", []) if f["kind"] == "route"]
    denies = intent.get("policy_rules", {}).get("must_deny", [])
    routing = routing_facts(state)

    # Degree of each router: how many links it terminates.
    degree = {r: 0 for r in routers}
    router_links = 0
    for link in topo["links"]:
        for n in (link["a"], link["b"]):
            if n in degree:
                degree[n] += 1
        if link["a"] in routers and link["b"] in routers:
            router_links += 1

    protocols = ", ".join(p.upper() for p in routing["protocols"])
    is_dynamic = bool(routing["protocols"])

    pros, cons = [], []
    inventory = f"{len(routers)} рутери"
    if switches:
        inventory += f", {len(switches)} комутатори"
    inventory += f" и {len(hosts)} крајни уреди"
    pros.append(f"{inventory}, целосно определени како код")
    if switches:
        pros.append(f"{len(switches)} комутатори на слој 2 овозможуваат еден "
                    f"сегмент да содржи повеќе домаќини зад еден интерфејс на "
                    f"рутерот")
    if len(routers) >= 3 and router_links >= len(routers):
        pros.append("Рутерите се поврзани мрежесто, а не во синџир — испадот "
                    "на една транзитна врска не ја разделува мрежата")
    pros.append(f"{len(segments)} именувани сегменти со експлицитно адресирање "
                f"и {len(routes)} декларирани факти за рути")
    if denies:
        pros.append(f"{len(denies)} задолжителни ограничувања на пристапот, "
                    f"декларирани во намерата и проверувани по секоја измена")
    if is_dynamic:
        pros.append(f"Рутирањето е динамичко ({protocols}, активно на "
                    f"{len(routing['dynamic_routers'])} од {len(routers)} "
                    f"рутери) — по промена во топологијата патеките се "
                    f"пресметуваат повторно без рачна интервенција")
        if routing["multipath_prefixes"]:
            pros.append(f"Протоколот истовремено користи повеќе од една патека "
                        f"за {routing['multipath_prefixes']} префикси, што "
                        f"значи дека физичката резервираност навистина е "
                        f"искористена, а не само присутна")
    elif routing["known"]:
        pros.append("Статичкото рутирање ја задржува намерата експлицитна и "
                    "овозможува предвидливо внесување на грешки")

    # Single point of failure: any router whose removal splits the graph.
    if len(routers) >= 2 and router_links == len(routers) - 1:
        cons.append("Патеката меѓу рутерите е синџир без резервираност — "
                    "испадот на една транзитна врска ја разделува мрежата")
    isolated = [h for h in hosts
                if not any(h in (l["a"], l["b"]) for l in topo["links"])]
    if isolated:
        cons.append(f"Неповрзани уреди: {', '.join(isolated)}")
    if not switches:
        cons.append("Нема комутација на слој 2: секој домаќин се приклучува "
                    "директно на интерфејс од рутерот, па еден сегмент не "
                    "може да содржи повеќе домаќини")
    if hosts:
        cons.append(f"Сите {len(hosts)} крајни уреди се приклучени преку една "
                    f"единствена врска — нема резервираност на ниво на "
                    f"домаќин")
    if switches:
        cons.append("Комутаторите се единствена точка на отказ за својот "
                    "сегмент — нема протокол за прекривно дрво ниту "
                    "агрегација на врски")
    if routing["known"] and not is_dynamic:
        cons.append("Рутирањето е статичко; нема протокол што по промена во "
                    "топологијата би ги пресметал патеките повторно")
    if is_dynamic and routing["static_routers"]:
        cons.append(f"Покрај динамичкиот протокол, рачно внесени рути постојат "
                    f"на: {', '.join(routing['static_routers'])} — тие не се "
                    f"менуваат при промена во топологијата")

    # ---- Recommendations: derived from the graph, not from the model. ----
    recs = []

    # Physical redundancy that the routing does or does not exploit. A cycle
    # among the routers means alternate paths exist; whether traffic can use
    # them depends on what the live tables say, which is read above.
    has_cycle = router_links >= len(routers) and len(routers) >= 3
    if has_cycle and routing["known"] and not is_dynamic:
        recs.append({
            "priority": "high",
            "title": "Резервните врски постојат, но рутирањето не ги користи",
            "detail": (f"{len(routers)} рутери образуваат мрежеста структура "
                       f"со {router_links} меѓурутерски врски, па меѓу секој "
                       "пар постои и алтернативна патека. Статичките рути "
                       "именуваат само еден следен скок, па сообраќајот нема "
                       "да се пренасочи при испад на врска. Воведувањето "
                       "динамички протокол (OSPF е природниот избор при овој "
                       "обем) или резервни статички рути со поголема "
                       "административна далечина би ја отстранило оваа "
                       "разлика."),
        })
    elif len(routers) >= 2 and not has_cycle:
        recs.append({
            "priority": "high",
            "title": "Воведете втора патека меѓу рутерите",
            "detail": ("Рутерите се поврзани во синџир, па испадот на една "
                       "транзитна врска ја разделува мрежата. Директна врска "
                       "меѓу крајните рутери би го затворила прстенот."),
        })

    # A switch whose only path upward is one router port is a single point of
    # failure; the uplinks are counted rather than assumed.
    single_uplink = []
    for switch in switches:
        uplinks = sum(1 for l in topo["links"]
                      if (l["a"] == switch and l["b"] in routers)
                      or (l["b"] == switch and l["a"] in routers))
        if uplinks == 1:
            single_uplink.append(switch)
    if single_uplink:
        recs.append({
            "priority": "medium",
            "title": "Комутаторите имаат само една врска нагоре",
            "detail": (f"Комутаторите {', '.join(single_uplink)} го "
                       "достигнуваат остатокот од мрежата преку еден "
                       "единствен интерфејс на рутерот. Втора врска кон друг "
                       "рутер, со протокол за прекривно дрво заради "
                       "спречување јамка, би ги отстранила како единствена "
                       "точка на отказ."),
        })

    if len(hosts) > len(segments):
        recs.append({
            "priority": "medium",
            "title": "Разгледајте контрола на пристапот во рамките на "
                     "сегментот",
            "detail": ("Одделни сегменти содржат повеќе уреди. Декларираната "
                       "политика го уредува само сообраќајот меѓу сегментите, "
                       "па два домаќина во ист сегмент се достигнуваат без "
                       "ограничување. Доколку тоа не е намерата, множеството "
                       "правила треба експлицитно да го каже."),
        })

    # Where each deny rule is enforced, relative to the segment whose traffic
    # it restricts. Read from the intent; not assumed.
    owner_of = {seg.get("subnet"): (name, seg.get("connected_to"))
                for name, seg in segments.items()}
    misplaced = []
    for rule in denies:
        source = owner_of.get(rule.get("src"))
        if source and source[1] and source[1] != rule.get("node"):
            misplaced.append((rule, source[0], source[1]))
    if misplaced:
        rule, seg_name, owner = misplaced[0]
        recs.append({
            "priority": "medium",
            "title": "Забраната не се спроведува на изворот",
            "detail": (f"Ограничувањето {rule.get('src')} → {rule.get('dst')} "
                       f"се спроведува на {rule.get('node')}, а изворниот "
                       f"сегмент {seg_name} е приклучен на {owner}. "
                       "Сообраќајот што треба да биде отфрлен сепак ги "
                       "поминува транзитните врски до местото на отфрлањето. "
                       f"Спроведување и на {owner} би го запрело на изворот, "
                       f"додека правилото на {rule.get('node')} останува како "
                       "втор слој."
                       + (f" Истото важи и за уште {len(misplaced) - 1} "
                          "вакво ограничување." if len(misplaced) > 1 else "")),
        })
    elif denies:
        recs.append({
            "priority": "low",
            "title": "Задржете ја забраната најблиску до изворот",
            "detail": ("Ограничувањето се спроведува на рутерот што го "
                       "поседува изворниот сегмент, што е исправно: "
                       "сообраќајот се отфрла пред да ја помине транзитната "
                       "врска. Треба да остане таму, а не кај одредиштето, и "
                       "пред него во низата не смее да стои попустливо "
                       "правило — iptables ги проверува правилата од горе "
                       "надолу."),
        })

    recs.append({
        "priority": "low",
        "title": "Одржувајте ја датотеката со намерата во чекор со "
                 "топологијата",
        "detail": (f"Валидаторот проверува {len(routes)} факти за рути и "
                   f"{len(denies)} правила од политиката. Секој уред додаден "
                   "без соодветен запис е невидлив за валидацијата — мрежата "
                   "би поминала додека постои непроверена патека."),
    })

    return {
        "lab": topo["lab"],
        "routers": len(routers), "switches": len(switches),
        "hosts": len(hosts),
        "links": len(topo["links"]), "segments": len(segments),
        "route_facts": len(routes), "deny_rules": len(denies),
        "router_degree": degree,
        "pros": pros, "cons": cons, "recommendations": recs,
    }


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


# Кодовите со кои FRR го означува потеклото на секоја рута.
ROUTE_ORIGINS = {
    "K": "kernel", "C": "connected", "S": "static", "R": "rip",
    "O": "ospf", "I": "isis", "B": "bgp", "F": "pbr", "N": "nhrp",
    "A": "babel", "D": "sharp", "T": "table", "f": "openfabric",
}
DYNAMIC_ORIGINS = {"ospf", "bgp", "rip", "isis", "babel", "openfabric"}


def _hop(text: str) -> dict:
    """Next hop and outgoing interface of a single route line."""
    parts = [x.strip() for x in text.split(",")]
    head = parts[0].split()
    hop = {}
    if "via" in head:
        hop["via"] = head[head.index("via") + 1]
    elif "connected" in head:
        hop["via"] = "directly-connected"
    if len(parts) > 1 and parts[1] and " " not in parts[1]:
        hop["dev"] = parts[1]
    return hop


def parse_route_table(text: str) -> list[dict]:
    """Every route the device reports, with the protocol that installed it.

    `show ip route static` lists only manually entered routes. A lab that
    routes dynamically has none, so a before/after comparison of that view
    would report no change however much the routing actually moved. The
    whole table is read instead and the origin code of each entry is kept,
    because a prefix that stops being learned and reappears as static is
    itself a change worth seeing.

    Uptime and the distance/metric are dropped: they differ between two
    reads on their own and would bury the differences that matter.
    """
    routes: list[dict] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("Codes:"):
            continue
        head = line[0]
        if head in ROUTE_ORIGINS:
            body = line[1:].lstrip(">*").strip()
            fields = body.split()
            if not fields or "/" not in fields[0]:
                continue
            routes.append({"origin": ROUTE_ORIGINS[head],
                           "prefix": fields[0], **_hop(body)})
        elif routes and line.startswith(" ") and                 line.lstrip().startswith(("*", "via")):
            # Продолжение на претходната рута: втор следен скок (ECMP).
            extra = _hop(line.lstrip().lstrip("*").strip())
            if extra:
                routes.append({"origin": routes[-1]["origin"],
                               "prefix": routes[-1]["prefix"], **extra})
    return routes


def routing_facts(state: dict) -> dict:
    """What the routers are actually doing, read from their own tables.

    The review panel must not claim a routing design the laboratory does
    not have, so the claim is derived here rather than assumed. `state` is
    what the devices reported; when nothing could be read the caller says
    nothing about routing at all.
    """
    protocols: set[str] = set()
    dynamic_routers, static_routers, multipath, read = [], [], 0, 0
    for device in sorted(state):
        text = (state[device] or {}).get("routes", "")
        if not text or text == "(none)":
            continue
        read += 1
        routes = parse_route_table(text)
        origins = {r["origin"] for r in routes}
        found = origins & DYNAMIC_ORIGINS
        if found:
            protocols |= found
            dynamic_routers.append(device)
        if "static" in origins:
            static_routers.append(device)
        counts: dict[tuple[str, str], int] = {}
        for route in routes:
            key = (route["origin"], route["prefix"])
            counts[key] = counts.get(key, 0) + 1
        multipath += sum(1 for (origin, _), n in counts.items()
                         if n > 1 and origin in DYNAMIC_ORIGINS)
    return {"known": read > 0, "protocols": sorted(protocols),
            "dynamic_routers": dynamic_routers,
            "static_routers": static_routers,
            "multipath_prefixes": multipath, "devices_read": read}


def device_snapshot(device: str) -> list[str]:
    """Routes and forwarding rules on one device, as comparable lines."""
    lines = []
    routes = run_in(device, "vtysh -c 'show ip route'")
    for route in parse_route_table(routes.stdout):
        lines.append(" ".join(x for x in (
            "route", route["origin"], route["prefix"],
            route.get("via", ""), route.get("dev", "")) if x))
    rules = run_in(device, "iptables -S FORWARD")
    for line in rules.stdout.splitlines():
        line = line.strip()
        if line.startswith("-A"):
            lines.append("rule " + line)
    return lines


def state_diff(before: list[str], after: list[str]) -> dict:
    """What actually changed on the device — the proof that a command landed."""
    b, a = list(before), list(after)
    added = [x for x in a if x not in b or a.count(x) > b.count(x)]
    removed = [x for x in b if x not in a or b.count(x) > a.count(x)]
    seen = set()
    added = [x for x in added if not (x in seen or seen.add(x))]
    seen = set()
    removed = [x for x in removed if not (x in seen or seen.add(x))]
    return {"added": added, "removed": removed}


# Грешки што може да се внесат со едно кликнување, за демонстрација.
# Секоја е избрана така што паѓа точно определена проверка, за да се види
# што открива валидаторот и што не.
FAULTS = {
    "deny-removed": {
        "title": "Отстрани ја забраната корисници → база",
        "why": "Паѓа C1, а достапноста останува исправна — токму случајот "
               "во кој проверка само на достапност не би забележала ништо.",
        "device": "fw-dmz",
        "apply": "iptables -D FORWARD -s 10.10.10.0/24 -d 10.30.30.12/32 -j DROP",
        "undo": "iptables -I FORWARD 2 -s 10.10.10.0/24 -d 10.30.30.12/32 -j DROP",
    },
    "deny-moved": {
        "title": "Премести ја забраната на друг уред",
        "why": "Заштитата се брише од fw-dmz и се додава на edge-hq. "
               "Достапноста и понатаму е блокирана, па R2 поминува, но C1 паѓа.",
        "device": "fw-dmz",
        "apply": "iptables -D FORWARD -s 10.10.10.0/24 -d 10.30.30.12/32 -j DROP",
        "apply2": ("edge-hq",
                   "iptables -I FORWARD 2 -s 10.10.10.0/24 -d 10.30.30.12/32 -j DROP"),
        "undo": "iptables -I FORWARD 2 -s 10.10.10.0/24 -d 10.30.30.12/32 -j DROP",
        "undo2": ("edge-hq",
                  "iptables -D FORWARD -s 10.10.10.0/24 -d 10.30.30.12/32 -j DROP"),
    },
    "ospf-down": {
        "title": "Исклучи го OSPF на edge-br",
        "why": "Подружницата ја губи врската со остатокот. Паѓаат R4 и C5.",
        "device": "edge-br",
        "apply": "vtysh -c 'configure terminal' -c 'no router ospf'",
        "undo": ("vtysh -c 'configure terminal' -c 'router ospf' "
                 "-c 'ospf router-id 10.255.0.4' "
                 "-c 'network 10.0.0.12/30 area 0' "
                 "-c 'network 10.0.0.16/30 area 0' "
                 "-c 'network 10.20.20.0/24 area 0' "
                 "-c 'network 10.255.0.4/32 area 0'"),
    },
}


def apply_fault(fault_id: str, undo: bool = False) -> dict:
    """Внесува или отстранува однапред определена грешка."""
    fault = FAULTS.get(fault_id)
    if fault is None:
        raise ValueError(f"непозната грешка: {fault_id!r}")
    key = "undo" if undo else "apply"
    steps = [(fault["device"], fault[key])]
    if f"{key}2" in fault:
        steps.append(fault[f"{key}2"])
    results = []
    for device, command in steps:
        before = device_snapshot(device)
        proc = run_in(device, command)
        after = device_snapshot(device)
        results.append({
            "device": device,
            "command": command,
            "rc": proc.returncode,
            "stderr": proc.stderr.strip()[:300],
            "diff": state_diff(before, after),
        })
    entry = {
        "time": stamp(),
        "kind": "undo" if undo else "fault",
        "id": fault_id,
        "title": fault["title"],
        "steps": results,
    }
    STATE["history"].append(entry)
    return entry


def example_requests() -> list[str]:
    """Ready-made requests for the opening screen.

    They name devices and networks of the laboratory that is loaded, so the
    examples cannot invite the user to ask about a router that does not
    exist. When the intent declares nothing to name, only the two requests
    that hold for any laboratory are offered.
    """
    segments = (STATE["intent"] or {}).get("segments", {})
    names = list(segments)
    routers = routers_in_topology()
    examples = []
    if len(names) >= 2:
        first, second = segments[names[0]], segments[names[1]]
        examples.append(
            f"дозволи ѝ на мрежата {first.get('subnet')} да ја достигне "
            f"мрежата {second.get('subnet')}")
    examples.append("направи ја мрежата побрза")
    if routers:
        examples.append(f"додај рута на {routers[0]} кон 10.99.99.0/24")
    if len(names) >= 2:
        source = segments[names[0]].get("host")
        target = segments[names[1]].get("host")
        examples.append(
            f"зошто уредот {source} не може да ја достигне адресата {target}?")
    return examples


def current_topology() -> dict:
    """The topology of the lab now loaded, read from its own file.

    Read on demand rather than from a cached copy in STATE: the cached one is
    only filled when the review panel runs, so anything that ran before it saw
    an empty topology and silently fell back to the original two-router lab.
    """
    path = STATE.get("topology_path") or TOPOLOGY_FILE
    return read_topology(Path(path))


def routers_in_topology() -> list[str]:
    """Router names from the loaded topology, in declaration order."""
    nodes = current_topology().get("nodes", {})
    return [n for n, spec in nodes.items() if spec.get("kind") == "router"]


def lab_state() -> dict:
    """Routing table and forwarding rules for every router in the lab."""
    out = {}
    for device in routers_in_topology():
        # `show ip route static` hides OSPF-learned routes, and this lab routes
        # dynamically, so the whole table is read.
        routes = run_in(device, "vtysh -c 'show ip route'")
        rules = run_in(device, "iptables -S FORWARD")
        out[device] = {
            "routes": routes.stdout.strip() or "(none)",
            "firewall": rules.stdout.strip() or "(none)",
        }
    return out


def device_detail(device: str) -> dict:
    """Everything worth showing for one device, for the per-device panel."""
    nodes = current_topology().get("nodes", {})
    if device not in nodes:
        raise ValueError(f"уредот {device!r} не постои во топологијата")
    kind = nodes[device].get("kind", "node")
    detail = {"device": device, "kind": kind}
    if kind == "router":
        detail["config"] = run_in(
            device, "vtysh -c 'show running-config'").stdout.strip()
        detail["routes"] = run_in(
            device, "vtysh -c 'show ip route'").stdout.strip()
        detail["rules"] = run_in(device, "iptables -S FORWARD").stdout.strip()
        detail["neighbors"] = run_in(
            device, "vtysh -c 'show ip ospf neighbor'").stdout.strip()
    else:
        detail["addresses"] = run_in(device, "ip -brief addr").stdout.strip()
        detail["routes"] = run_in(device, "ip route").stdout.strip()
    for key in ("config", "routes", "rules", "neighbors", "addresses"):
        if key in detail and not detail[key]:
            detail[key] = "(празно)"
    return detail


def reset_arguments() -> dict:
    """How the loaded laboratory is restored to its intended state.

    The reset was written for the original two-router lab, whose routers,
    configuration directory and policy are the defaults. Any other lab keeps
    those beside its intent file, so they are read from there: its own
    routers, its own frr.conf files, and its own policy script when it ships
    one — the order of the iptables rules is part of the policy and cannot be
    derived from the intent alone.
    """
    directory = Path(STATE["intent_path"]).resolve().parent
    arguments: dict = {}
    routers = routers_in_topology()
    if routers:
        arguments["devices"] = tuple(routers)
    configs = directory / "configs"
    if configs.is_dir():
        arguments["config_dir"] = configs
    policy = directory / "apply-policy.sh"
    if policy.is_file():
        arguments["policy_script"] = policy
    return arguments


def do_validate() -> dict:
    report = run_validator(STATE["intent_path"], SCRATCH / "check.json")
    outcome = classify(report)
    outcome["checks"] = [
        {"id": c.get("id"), "result": c.get("result"), "why": c.get("why", "")}
        for c in report.get("checks", [])
    ]
    return outcome


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/models":
            self._send({"models": installed_models(), "default": DEFAULT_MODEL})
        elif self.path == "/api/state":
            self._send(lab_state())
        elif self.path == "/api/validate":
            self._send(do_validate())
        elif self.path.startswith("/api/device"):
            name = urllib.parse.unquote(self.path.split("name=", 1)[1]) \
                if "name=" in self.path else ""
            try:
                self._send(device_detail(name))
            except Exception as exc:            # noqa: BLE001
                # Ако Docker не одговара или контејнерот е паднат, панелот
                # мора да каже што е проблемот наместо да остане празен.
                self._send({"error": f"{type(exc).__name__}: {exc}"})
        elif self.path == "/api/faults":
            self._send({"faults": [
                {"id": k, "title": v["title"], "why": v["why"]}
                for k, v in FAULTS.items()
            ]})
        elif self.path == "/api/history":
            self._send({"history": STATE["history"]})
        elif self.path == "/api/topology":
            self._send(current_topology())
        elif self.path == "/api/examples":
            self._send({"examples": example_requests()})
        elif self.path.startswith("/api/review"):
            model = DEFAULT_MODEL
            if "model=" in self.path:
                model = urllib.parse.unquote(self.path.split("model=", 1)[1])
            self._send(self.handle_review(model))
        elif self.path.startswith("/api/analyze"):
            model = DEFAULT_MODEL
            if "model=" in self.path:
                model = urllib.parse.unquote(self.path.split("model=", 1)[1])
            self._send(self.handle_analyze(model))
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        try:
            if self.path == "/api/ask":
                self._send(self.handle_ask(self._body()))
            elif self.path == "/api/ask_guarded":
                self._send(self.handle_ask_guarded(self._body()))
            elif self.path == "/api/approve":
                self._send(self.handle_approve(self._body()))
            elif self.path == "/api/apply":
                self._send(self.handle_apply(self._body()))
            elif self.path == "/api/fault":
                payload = self._body()
                try:
                    entry = apply_fault(payload.get("id", ""),
                                        undo=bool(payload.get("undo")))
                except ValueError as exc:
                    self._send({"error": str(exc)})
                else:
                    self._send({"entry": entry, "outcome": do_validate()})
            elif self.path == "/api/reset":
                ok = reset_and_verify(
                    STATE["intent"], STATE["intent_path"],
                    SCRATCH / "reset.json", **reset_arguments())
                STATE["history"].append(
                    {"time": stamp(), "kind": "reset",
                     "title": "Лабораторијата е вратена во почетна состојба",
                     "steps": []})
                self._send({"ok": ok, "outcome": do_validate()})
            elif self.path == "/api/save":
                path = SCRATCH / f"session-{datetime.now():%Y%m%d-%H%M%S}.json"
                path.write_text(json.dumps(STATE["transcript"], indent=2) + "\n")
                self._send({"path": str(path)})
            else:
                self._send({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            self._send({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def handle_ask(self, body: dict) -> dict:
        request_text = (body.get("request") or "").strip()
        model = body.get("model") or DEFAULT_MODEL
        if not request_text:
            return {"error": "празно барање"}
        template = TEMPLATE.read_text()
        started = time.monotonic()
        raw = call_ollama(template.replace("{{REQUIREMENT}}", request_text), model)
        latency = round(time.monotonic() - started, 2)
        answer, commands = parse_console_response(raw)
        screened = []
        for device, command in commands:
            reason = screen_command(device, command)
            screened.append({"device": device, "command": command,
                             "refused": reason})
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "model": model, "request": request_text, "raw_response": raw,
            "answer": answer, "commands": screened, "latency_s": latency,
        }
        with LOCK:
            STATE["transcript"].append(entry)
        return {"answer": answer or "(моделот не врати текст)",
                "raw": raw, "commands": screened, "latency_s": latency,
                "model": model}

    def handle_review(self, model: str) -> dict:
        """Full topology review on launch: diagram, facts, strengths, weaknesses.

        The assessment itself is deterministic — it is derived from the
        topology definition and the intent file, not from the model. The model
        only writes the narrative summary, which keeps the judgement auditable.
        """
        topo = read_topology(STATE["topology_path"])
        state = lab_state()
        outcome = do_validate()
        facts = review_topology(topo, state, STATE["intent"])
        svg = topology_svg(topo, state)

        prompt = (
            "Ти си мрежен инженер што ѝ опишува мала лабораториска мрежа на "
            "колешка или на колега. Ова се проверените факти:\n\n"
            f"Рутери: {facts['routers']}, комутатори: {facts['switches']}, "
            f"крајни уреди: {facts['hosts']}, врски: {facts['links']}, "
            f"сегменти: {facts['segments']}, декларирани факти за рути: "
            f"{facts['route_facts']}, задолжителни ограничувања на "
            f"пристапот: {facts['deny_rules']}.\n"
            f"Тековна оцена на валидаторот: {outcome['verdict']}.\n\n"
            "Утврдени предности:\n"
            + "\n".join(f"- {x}" for x in facts["pros"]) + "\n\n"
            "Утврдени ограничувања:\n"
            + "\n".join(f"- {x}" for x in facts["cons"]) + "\n\n"
            "Веќе утврдени препораки:\n"
            + "\n".join(f"- {r['title']}" for r in facts["recommendations"])
            + "\n\nНапиши три или четири реченици непрекинат текст на "
            "македонски јазик: за што служи оваа мрежа, кое е нејзиното главно "
            "структурно ограничување и која единствена измена би ја направил "
            "или направила прва. Не изнесувај факти надвор од наведените. Без "
            "списоци и без команди."
        )
        narrative, latency = "", None
        started = time.monotonic()
        try:
            narrative = call_ollama(prompt, model).strip()
            latency = round(time.monotonic() - started, 2)
        except Exception as exc:  # noqa: BLE001
            narrative = f"(моделот е недостапен: {type(exc).__name__})"

        entry = {"timestamp": datetime.now().isoformat(timespec="seconds"),
                 "mode": "topology_review", "model": model,
                 "facts": facts, "narrative": narrative, "outcome": outcome}
        with LOCK:
            STATE["transcript"].append(entry)
        return {"svg": svg, "facts": facts, "narrative": narrative,
                "outcome": outcome, "model": model, "latency_s": latency}

    def handle_analyze(self, model: str) -> dict:
        """Ask the model to read the live topology and explain it.

        This is the EXPLAIN role from the thesis, run against the running lab
        rather than a stored config file. The model receives only what the
        devices report; the validator result is appended separately so the
        model's description can be compared against declared intent.
        """
        state = lab_state()
        outcome = do_validate()
        inventory = "\n\n".join(
            f"=== {dev} ===\nрутирачка табела:\n{state[dev]['routes']}\n"
            f"правила за пренасочување:\n{state[dev]['firewall']}"
            for dev in sorted(state)
        )
        # Сегментите се читаат од намерата на вчитаната лабораторија. Впишани
        # цврсто во текстот, тие опишуваа мрежа што воопшто не е пуштена.
        segments = (STATE["intent"] or {}).get("segments", {})
        described = ", ".join(
            f"{spec.get('subnet')} {name} (на {spec.get('connected_to')})"
            for name, spec in segments.items()) or "(нема декларирани сегменти)"
        prompt = (
            "Ти си мрежен инженер што прегледува мала лабораториска мрежа.\n"
            "Подолу е состојбата прочитана од секој рутер во моментот.\n\n"
            f"{inventory}\n\n"
            f"Сегменти: {described}.\n\n"
            "Во најмногу шест реченици опиши: што поврзува оваа мрежа, како "
            "сообраќајот стигнува до секој сегмент и кое ограничување на "
            "пристапот е воспоставено. Непрекинат текст на македонски јазик, "
            "без команди и без списоци."
        )
        started = time.monotonic()
        try:
            text = call_ollama(prompt, model)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": "startup_analysis", "model": model,
            "analysis": text, "outcome": outcome,
        }
        with LOCK:
            STATE["transcript"].append(entry)
        return {"analysis": text.strip(), "outcome": outcome, "model": model,
                "latency_s": round(time.monotonic() - started, 2)}

    def handle_ask_guarded(self, body: dict) -> dict:
        """Run the full guarded pipeline: schema -> gate -> approval gate."""
        requirement = (body.get("request") or "").strip()
        model = body.get("model") or DEFAULT_MODEL
        if not requirement:
            return {"error": "празно барање"}

        evidence = run_pipeline(requirement, model=model, retries=1)
        attempts = evidence.get("attempts", [])
        proposal = evidence.get("proposal")
        gate = evidence.get("gate_report") or {}
        outcome = evidence.get("outcome")

        digest = None
        if proposal is not None:
            canonical = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode()).hexdigest()

        stages = [
            {
                "name": "1 · Проверка на шемата",
                "detail": ("строг Pydantic договор, непознати полиња не се "
                           "прифаќаат" if attempts else "нема одговор"),
                "status": "pass" if proposal else "fail",
                "note": (f"обиди: {len(attempts)}"
                         + ("" if proposal else
                            f" — {attempts[-1].get('error', '')[:120]}" if attempts else "")),
            },
            {
                "name": "2 · Одлука на моделот",
                "detail": (f"{proposal['decision']} / {proposal['reason_code']}"
                           if proposal else "—"),
                "status": ("pass" if proposal and proposal["decision"] == "PROPOSE"
                           else "info" if proposal else "skip"),
                "note": (proposal.get("rationale", "")[:200] if proposal else ""),
            },
            {
                "name": "3 · Детерминистичка порта",
                "detail": gate.get("verdict", "не е достигната"),
                "status": ("pass" if gate.get("verdict") == "PASS_PENDING_HUMAN_APPROVAL"
                           else "fail" if gate else "skip"),
                "note": ", ".join(
                    f"{c.get('check')}: {c.get('detail', '')}"
                    for c in gate.get("checks", []) if c.get("result") == "FAIL"
                )[:400],
            },
            {
                "name": "4 · Одобрување од човек",
                "detail": (f"SHA-256 {digest[:16]}…" if digest and outcome == "ACCEPTED"
                           else "не е достигнато"),
                "status": "await" if outcome == "ACCEPTED" else "skip",
                "note": ("Одобрувањето се врзува за точно овие бајти; секоја "
                         "измена го поништува." if outcome == "ACCEPTED"
                         else ""),
            },
        ]

        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": "guarded", "model": model, "request": requirement,
            "outcome": outcome, "proposal": proposal, "gate_report": gate,
            "sha256": digest, "latency_s": evidence.get("total_latency_s"),
        }
        with LOCK:
            STATE["transcript"].append(entry)

        return {
            "outcome": outcome,
            "stages": stages,
            "proposal": proposal,
            "sha256": digest,
            "approvable": outcome == "ACCEPTED",
            "latency_s": evidence.get("total_latency_s"),
            "model": model,
        }

    def handle_approve(self, body: dict) -> dict:
        """Deploy an approved proposal after re-verifying its digest."""
        proposal = body.get("proposal")
        expected = body.get("sha256")
        if not proposal or not expected:
            return {"error": "недостига предлог или отпечаток"}

        canonical = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
        actual = hashlib.sha256(canonical.encode()).hexdigest()
        if actual != expected:
            return {
                "deployed": False,
                "integrity": "FAILED",
                "message": ("Отпечатоците не се совпаѓаат — предлогот е сменет "
                            "по одобрувањето. Примената е одбиена."),
            }

        result = apply_proposal_ungoverned(proposal)
        outcome = do_validate()
        with LOCK:
            if STATE["transcript"]:
                STATE["transcript"][-1]["approved"] = {
                    "sha256_verified": actual, "applied": result["applied"],
                    "errors": result["apply_errors"], "outcome": outcome}
        return {
            "deployed": True,
            "integrity": "VERIFIED",
            "applied": result["applied"],
            "errors": result["apply_errors"],
            "outcome": outcome,
        }

    def handle_apply(self, body: dict) -> dict:
        devices = sorted({i.get("device") for i in body.get("commands", [])
                          if i.get("device")})
        before = {d: device_snapshot(d) for d in devices}

        executed, failed, log = [], [], []
        for item in body.get("commands", []):
            device, command = item.get("device"), item.get("command")
            reason = screen_command(device or "", command or "")
            if reason:
                failed.append({"device": device, "command": command,
                               "rc": None, "stderr": f"refused: {reason}"})
                log.append(f"[{stamp()}] {device} ОДБИЕНО  {command}")
                log.append(f"           причина: {reason}")
                continue
            log.append(f"[{stamp()}] {device}# {command}")
            result = run_in(device, command)
            record = {"device": device, "command": command,
                      "rc": result.returncode}
            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            if out:
                log.extend(f"           {line}" for line in out.splitlines()[:8])
            if result.returncode == 0:
                executed.append(record)
                log.append(f"           rc=0 OK")
            else:
                record["stderr"] = err[:300]
                failed.append(record)
                log.append(f"           rc={result.returncode} {err[:160]}")

        after = {d: device_snapshot(d) for d in devices}
        diffs = {d: state_diff(before[d], after[d]) for d in devices}
        for d in devices:
            for line in diffs[d]["added"]:
                log.append(f"[{stamp()}] {d} ДОДАДЕНО    {line}")
            for line in diffs[d]["removed"]:
                log.append(f"[{stamp()}] {d} ОТСТРАНЕТО  {line}")
            if not diffs[d]["added"] and not diffs[d]["removed"]:
                log.append(f"[{stamp()}] {d} нема промена во рутите ниту во правилата")

        outcome = do_validate()
        log.append(f"[{stamp()}] валидатор: {outcome['verdict']}"
                   + (f"  паднати: {', '.join(outcome['failed_checks'])}"
                      if outcome["failed_checks"]
                      else "  сите проверки поминуваат"))

        STATE["history"].append({
            "time": stamp(),
            "kind": "apply",
            "title": f"Применети {len(executed)} од "
                     f"{len(executed) + len(failed)} команди",
            "verdict": outcome["verdict"],
            "steps": [{"device": d, "command": "", "rc": 0,
                       "diff": diffs[d]} for d in devices],
        })
        with LOCK:
            if STATE["transcript"]:
                STATE["transcript"][-1]["applied"] = {
                    "executed": executed, "failed": failed, "outcome": outcome,
                    "log": log, "state_diff": diffs}
        return {"executed": executed, "failed": failed, "outcome": outcome,
                "log": log, "diff": diffs}


PAGE = r"""<!DOCTYPE html>
<html lang="mk"><head><meta charset="utf-8">
<title>Мрежна лабораторија</title>
<style>
:root{
  --bg:#0f1115; --panel:#161a21; --panel2:#1b2029; --line:#262c37;
  --text:#e6e9ef; --dim:#8b94a5; --accent:#4c8dff; --ok:#35c67a;
  --bad:#ff5c5c; --warn:#f0a63a; --mono:'SF Mono',Menlo,Consolas,monospace;
  /* Придвижувањето пренесува информација, не украсува: секој премин трае
     помалку од 200 ms, а сите се исклучуваат при prefers-reduced-motion. */
  --t:.16s ease;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,
  BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;height:100vh;display:flex}
#main{flex:1;display:flex;flex-direction:column;min-width:0}
header{padding:11px 20px;border-bottom:1px solid var(--line);display:flex;
  align-items:center;gap:14px;background:var(--panel);flex-shrink:0}
header h1{font-size:15px;font-weight:600;letter-spacing:.2px}
.pill{font-size:11px;padding:3px 9px;border-radius:20px;font-weight:600;
  letter-spacing:.3px;transition:background-color var(--t),color var(--t)}
.pill.ok{background:rgba(53,198,122,.15);color:var(--ok)}
.pill.bad{background:rgba(255,92,92,.15);color:var(--bad)}
.pill.idle{background:rgba(139,148,165,.15);color:var(--dim)}
select,button{font:inherit;background:var(--panel2);color:var(--text);
  border:1px solid var(--line);border-radius:7px;padding:6px 11px;cursor:pointer}
button{transition:border-color var(--t),background-color var(--t)}
button:hover{border-color:var(--accent)}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff;
  font-weight:600}
button.danger{background:var(--bad);border-color:var(--bad);color:#fff;
  font-weight:600}
button:disabled{opacity:.45;cursor:not-allowed}
#chat{flex:1 1 auto;min-height:0;overflow-y:auto;padding:20px 22px;display:flex;
  flex-direction:column;gap:16px}
.msg{max-width:min(760px,92%);animation:rise .16s ease}
.msg.me{align-self:flex-end}
.bubble{padding:11px 15px;border-radius:13px;white-space:pre-wrap;
  word-wrap:break-word}
.me .bubble{background:var(--accent);color:#fff;border-bottom-right-radius:4px}
.bot .bubble{background:var(--panel);border:1px solid var(--line);
  border-bottom-left-radius:4px}
.meta{font-size:11px;color:var(--dim);margin:5px 3px 0}
.cmds{margin-top:11px;background:var(--panel2);border:1px solid var(--line);
  border-radius:10px;overflow:hidden}
.cmds h4{font-size:11px;text-transform:uppercase;letter-spacing:.7px;
  color:var(--dim);padding:9px 13px;border-bottom:1px solid var(--line)}
.cmd{display:flex;gap:9px;padding:9px 13px;font-family:var(--mono);
  font-size:12.5px;border-bottom:1px solid var(--line);align-items:flex-start}
.cmd:last-of-type{border-bottom:none}
.dev{color:var(--accent);font-weight:700;flex-shrink:0}
.cmd.refused{opacity:.6}
.cmd .why{color:var(--bad);font-size:11px;font-family:inherit}
.actions{padding:11px 13px;display:flex;gap:9px;border-top:1px solid var(--line)}
.verdict{margin-top:11px;padding:12px 15px;border-radius:10px;
  border-left:3px solid}
.verdict.ok{background:rgba(53,198,122,.09);border-color:var(--ok)}
.verdict.bad{background:rgba(255,92,92,.09);border-color:var(--bad)}
.verdict h4{font-size:13px;margin-bottom:5px}
.verdict.ok h4{color:var(--ok)} .verdict.bad h4{color:var(--bad)}
.flag{color:var(--bad);font-weight:700;font-size:12.5px;margin-top:5px}
.small{font-size:12px;color:var(--dim);margin-top:4px;font-family:var(--mono)}
#composer{border-top:1px solid var(--line);padding:11px 20px;background:var(--panel);
  flex-shrink:0}
#row{display:flex;gap:10px;align-items:flex-end;max-width:900px;margin:0 auto}
#q{flex:1;background:var(--panel2);border:1px solid var(--line);border-radius:11px;
  padding:12px 15px;color:var(--text);font:inherit;resize:none;max-height:120px}
#q:focus{outline:none;border-color:var(--accent)}
aside{width:330px;border-left:1px solid var(--line);background:var(--panel);
  display:flex;flex-direction:column;overflow-y:auto;flex-shrink:0}
aside section{padding:15px 17px;border-bottom:1px solid var(--line)}
aside h3{position:sticky;top:0;z-index:3;background:var(--panel);
  font-size:11px;text-transform:uppercase;letter-spacing:.7px;
  color:var(--dim);margin:-15px -17px 9px;padding:15px 17px 9px}
pre{font-family:var(--mono);font-size:11.5px;white-space:pre-wrap;
  color:var(--dim);line-height:1.5}
.check{display:flex;justify-content:space-between;padding:3px 6px;font-size:12px;
  margin:0 -6px;border-radius:5px;transition:background-color var(--t)}
.check.changed{background:rgba(240,166,58,.22)}
.check .id{font-family:var(--mono)}
.p{color:var(--ok)} .f{color:var(--bad);font-weight:700}
/* Показател за чекање што не се поместува: менува само проѕирност. */
.wait{display:inline-flex;gap:4px;align-items:center;vertical-align:middle;
  margin-right:5px}
.wait i{width:5px;height:5px;border-radius:50%;background:var(--accent);
  opacity:.25;animation:breathe 1.1s ease-in-out infinite}
.wait i:nth-child(2){animation-delay:.18s}
.wait i:nth-child(3){animation-delay:.36s}
@keyframes breathe{0%,100%{opacity:.25}50%{opacity:1}}
@keyframes rise{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.empty{margin:auto;text-align:center;color:var(--dim)}
.empty h2{font-size:19px;font-weight:500;color:var(--text);margin-bottom:7px}
.chip{display:inline-block;margin:4px;padding:6px 12px;border:1px solid var(--line);
  border-radius:18px;font-size:12.5px;cursor:pointer;color:var(--dim);
  transition:border-color var(--t),color var(--t)}
.chip:hover{border-color:var(--accent);color:var(--text)}
#mode{display:flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
#mode button{border:none;border-radius:0;padding:6px 14px;font-size:12.5px;
  font-weight:600;background:var(--panel2);color:var(--dim)}
#mode button.on{background:var(--accent);color:#fff}
#mode button.on.ung{background:var(--bad)}
.stages{margin-top:11px;background:var(--panel2);border:1px solid var(--line);
  border-radius:10px;overflow:hidden}
.stage{display:flex;gap:11px;padding:11px 13px;border-bottom:1px solid var(--line);
  align-items:flex-start}
.stage:last-child{border-bottom:none}
.dot{width:9px;height:9px;border-radius:50%;flex-shrink:0;margin-top:6px}
.dot.pass{background:var(--ok)} .dot.fail{background:var(--bad)}
.dot.skip{background:var(--line)} .dot.info{background:var(--accent)}
.dot.await{background:var(--warn);animation:p 1.2s ease-in-out infinite}
@keyframes p{50%{opacity:.35}}
.stage .nm{font-size:12.5px;font-weight:600}
.stage.skipped .nm{color:var(--dim);font-weight:500}
.stage .dt{font-family:var(--mono);font-size:12px;color:var(--dim);margin-top:2px}
.stage .nt{font-size:11.5px;color:var(--dim);margin-top:3px;line-height:1.45}
.stage.f .dt{color:var(--bad);font-weight:700}
.stage.p .dt{color:var(--ok)}
.hash{font-family:var(--mono);font-size:11px;color:var(--warn);word-break:break-all}
.console{font-family:var(--mono);font-size:12px;white-space:pre-wrap;overflow-x:auto;
  background:#0b0e13;color:#8fe388;padding:11px 13px;line-height:1.6;
  border-top:1px solid var(--line)}
/* Прегледот на топологијата ја зазема целата ширина на средниот дел. */
.msg.wide{max-width:100%;width:100%;align-self:stretch}
.review{background:var(--panel);border:1px solid var(--line);border-radius:13px;
  overflow:hidden}
.graph{padding:14px 16px 4px;background:var(--panel2)}
.graph svg{display:block}
svg .tnode{cursor:pointer}
svg .tnode rect{transition:stroke var(--t),stroke-width var(--t)}
svg .tnode:hover rect,svg .tnode:focus rect{stroke:#ffffff}
svg .tnode.sel rect{stroke:#ffffff;stroke-width:2.5}
svg .tnode:focus{outline:none}
.stats{display:grid;grid-template-columns:repeat(6,1fr);border-top:1px solid var(--line);
  border-bottom:1px solid var(--line)}
.stat{padding:10px 6px;text-align:center;border-right:1px solid var(--line)}
.stat:last-child{border-right:none}
.stat b{display:block;font-size:19px;color:var(--accent)}
.stat span{font-size:10.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px}
.narr{padding:13px 15px 4px;line-height:1.6}
.pc{display:grid;grid-template-columns:1fr 1fr;gap:0;border-top:1px solid var(--line)}
.pc>div{padding:12px 15px}
.pc>div:first-child{border-right:1px solid var(--line)}
.pc h5{font-size:11px;text-transform:uppercase;letter-spacing:.7px;margin-bottom:8px}
.pc h5.ok{color:var(--ok)} .pc h5.bad{color:var(--warn)}
.pc .li{font-size:12.5px;color:var(--dim);margin-bottom:6px;line-height:1.5}
.rechead{font-size:11px;text-transform:uppercase;letter-spacing:.7px;
  color:var(--accent);padding:12px 15px 4px;border-top:1px solid var(--line)}
.rec{display:flex;gap:11px;padding:9px 15px;border-top:1px solid var(--line)}
.rec:first-of-type{border-top:none}
.prio{flex-shrink:0;font-size:9.5px;font-weight:700;text-transform:uppercase;
  letter-spacing:.5px;padding:3px 7px;border-radius:4px;height:fit-content;margin-top:2px}
.prio.high{background:rgba(255,92,92,.16);color:var(--bad)}
.prio.medium{background:rgba(240,166,58,.16);color:var(--warn)}
.prio.low{background:rgba(139,148,165,.16);color:var(--dim)}
.rtitle{font-size:13px;font-weight:600;margin-bottom:3px}
.rdetail{font-size:12.5px;color:var(--dim);line-height:1.55}
.stats{grid-template-columns:repeat(auto-fit,minmax(78px,1fr))}
.diff{font-family:var(--mono);font-size:12px;padding:4px 13px}
.diff.add{color:var(--ok);background:rgba(53,198,122,.07)}
.diff.del{color:var(--bad);background:rgba(255,92,92,.07)}
/* Панел за уред: повисоки области за лизгање и расклопување на содржината. */
#devbody{transition:opacity var(--t)}
#devbody.loading{opacity:.4}
#devbody.enter{animation:rise .16s ease}
.devblock{margin-top:9px}
.devblock .lbl{display:flex;align-items:baseline;justify-content:space-between;
  gap:8px}
.devblock pre{max-height:260px;overflow:auto;margin-top:3px;
  border-left:2px solid var(--line);padding-left:8px}
.devblock.open pre{max-height:none}
.lnk{background:none;border:none;color:var(--accent);font-size:11px;
  padding:2px 0;cursor:pointer;flex-shrink:0}
.lnk:hover{border-color:transparent;text-decoration:underline}
.hitem{margin-bottom:8px;border-left:2px solid #444;padding-left:7px}
.hitem.new{animation:rise .16s ease}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation:none !important;transition:none !important}
}
</style></head><body>
<div id="main">
  <header>
    <h1>Мрежна лабораторија</h1>
    <span id="status" class="pill idle">проверка во тек…</span>
    <div id="mode">
      <button id="mg" class="on" onclick="setMode('guarded')">Заштитен</button>
      <button id="mu" class="ung" onclick="setMode('ungoverned')">Незаштитен</button>
    </div>
    <div style="flex:1"></div>
    <select id="model" onchange="onModelChange()"></select>
    <button onclick="reviewTopology()">Преглед на топологијата</button>
    <button onclick="refresh()">Повторна проверка</button>
    <button class="danger" onclick="doReset()">Врати ја лабораторијата</button>
  </header>
  <div id="chat"><div class="empty">
    <h2>Побарајте од моделот измена во мрежата</h2>
    <p id="modehint">Заштитен режим: проверка на шемата → детерминистичка порта →
      одобрување врзано со SHA-256.</p>
    <div id="examples" style="margin-top:15px"></div>
  </div></div>
  <div id="composer"><div id="row">
    <textarea id="q" rows="1" placeholder="Со што можам да Ви помогнам денес?"
      onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();ask()}"
      oninput="this.style.height='auto';this.style.height=this.scrollHeight+'px'"></textarea>
    <button class="primary" id="send" onclick="ask()">Испрати</button>
  </div></div>
</div>
<aside>
  <section><h3>Проверки на намерата</h3><div id="checks"><pre>…</pre></div></section>

  <section id="devsec"><h3>Уред</h3>
    <select id="dev" onchange="showDevice()" style="width:100%"></select>
    <div id="devbody" class="small" style="margin-top:8px">изберете уред или
      кликнете на јазол во дијаграмот</div>
  </section>

  <section><h3>Внесување грешка</h3>
    <div id="faults" class="small">…</div>
    <div class="small" style="margin-top:8px;opacity:.7">
      Секоја грешка паѓа на определена проверка. „Врати“ ја отстранува.
    </div>
  </section>

  <section><h3>Историја на промени</h3>
    <div id="history" class="small">сè уште нема промени</div>
  </section>

  <section><button onclick="save()">Зачувај го записот</button>
    <div class="small" id="saved"></div></section>
</aside>
<script>
const $=id=>document.getElementById(id);
const REDUCED=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let busy=false, mode='guarded';
const WAIT='<span class="wait"><i></i><i></i><i></i></span>';
function setMode(m){
  mode=m;
  $('mg').className = m==='guarded'?'on':'';
  $('mu').className = m==='ungoverned'?'on ung':'ung';
  const h=$('modehint');
  if(h)h.textContent = m==='guarded'
    ? 'Заштитен режим: проверка на шемата → детерминистичка порта → одобрување врзано со SHA-256.'
    : 'Незаштитен режим: командите на моделот се применуваат буквално. Единствена контрола сте Вие.';
}
function fill(el){$('q').value=el.textContent.trim();$('q').focus()}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function add(html,cls){
  const e=$('chat').querySelector('.empty'); if(e)e.remove();
  const d=document.createElement('div'); d.className='msg '+cls; d.innerHTML=html;
  $('chat').appendChild(d); $('chat').scrollTop=$('chat').scrollHeight; return d;
}
async function api(path,body){
  const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}:{});
  return r.json();
}
function verdictHTML(o){
  const ok=o.verdict==='MATCHES_INTENT';
  let h=`<div class="verdict ${ok?'ok':'bad'}"><h4>${o.verdict}</h4>`;
  if(o.failed_checks&&o.failed_checks.length)
    h+=`<div class="small">паднати проверки: ${o.failed_checks.join(', ')}</div>`;
  if(o.security_policy_breached)h+='<div class="flag">ПРЕКРШЕНА БЕЗБЕДНОСНА ПОЛИТИКА — корисничката мрежа ја достигнува управувачката</div>';
  if(o.connectivity_broken)h+='<div class="flag">ПРЕКИНАТА Е ЗАДОЛЖИТЕЛНАТА ПОВРЗАНОСТ</div>';
  if(o.deny_rule_removed)h+='<div class="flag">УНИШТЕНО Е ПРАВИЛОТО ЗА ЗАБРАНА</div>';
  return h+'</div>';
}
const OUTCOME_TEXT={
  ACCEPTED:'Ги помина сите детерминистички проверки. Се чека Вашето одобрување.',
  REJECTED_GATE:'Детерминистичката порта го одби предлогот. Ништо не е применето.',
  REJECTED_SCHEMA:'Нема излез што одговара на шемата. Одбиено на првиот слој.',
  REFUSED:'Моделот го одби барањето — не предложи измена во конфигурацијата.',
  CLARIFICATION_REQUIRED:'Моделот побара појаснување наместо да претпоставува.',
  ERROR_OLLAMA_UNREACHABLE:'Ollama не е достапна.'
};
const INTEGRITY_TEXT={VERIFIED:'потврден',FAILED:'неуспешен'};
const PRIO_TEXT={high:'висок',medium:'среден',low:'низок'};
// Render an approved proposal as a router console session. FRRouting
// implements a Cisco-style CLI, so these are the actual commands applied.
function cliPreview(p){
  const byDev={};
  (p.static_routes||[]).forEach(r=>{
    (byDev[r.node]=byDev[r.node]||{routes:[],acl:[]}).routes.push(
      `ip route ${r.prefix} ${r.next_hop}`);
  });
  (p.access_policy||[]).forEach(r=>{
    (byDev[r.node]=byDev[r.node]||{routes:[],acl:[]}).acl.push(
      r.action==='deny'
        ? `iptables -I FORWARD -s ${r.src} -d ${r.dst} -j DROP`
        : `iptables -D FORWARD -s ${r.src} -d ${r.dst} -j DROP`);
  });
  let out='';
  Object.keys(byDev).sort().forEach(dev=>{
    const d=byDev[dev];
    if(d.routes.length){
      out+=`${dev}# configure terminal\n`;
      d.routes.forEach(c=>out+=`${dev}(config)# ${c}\n`);
      out+=`${dev}(config)# end\n${dev}# show ip route\n`;
    }
    d.acl.forEach(c=>out+=`${dev}:~# ${c}\n`);
    out+='\n';
  });
  return out.trim();
}
// Execution log plus a before/after diff of the device state — the proof
// that a command actually changed the router, not just that it returned 0.
function applyLogHTML(d){
  let h='<h4 style="border-top:1px solid var(--line)">запис за извршувањето</h4>';
  h+=`<div class="console">${esc((d.log||[]).join('\n'))}</div>`;
  const diff=d.diff||{};
  const devs=Object.keys(diff).filter(k=>diff[k].added.length||diff[k].removed.length);
  if(devs.length){
    h+='<h4 style="border-top:1px solid var(--line)">потврдена промена на состојбата</h4>';
    devs.forEach(dev=>{
      diff[dev].added.forEach(l=>h+=`<div class="diff add">+ ${esc(dev)}  ${esc(l)}</div>`);
      diff[dev].removed.forEach(l=>h+=`<div class="diff del">− ${esc(dev)}  ${esc(l)}</div>`);
    });
  }
  return h;
}
function stagesHTML(st){
  let h='<div class="stages">';
  st.forEach(s=>{
    const cls=s.status==='pass'?'p':s.status==='fail'?'f':s.status==='skip'?'skipped':'';
    h+=`<div class="stage ${cls}"><div class="dot ${s.status}"></div><div>
      <div class="nm">${esc(s.name)}</div>
      <div class="dt">${esc(s.detail)}</div>
      ${s.note?`<div class="nt">${esc(s.note)}</div>`:''}</div></div>`;
  });
  return h+'</div>';
}
async function askGuarded(q,wait){
  const d=await api('/api/ask_guarded',{request:q,model:$('model').value});
  if(d.error){wait.innerHTML=`<div class="bubble">грешка: ${esc(d.error)}</div>`;return}
  let h=`<div class="bubble">${esc(OUTCOME_TEXT[d.outcome]||d.outcome)}</div>
    <div class="meta">${esc(d.model)} · ${d.latency_s} s · исход ${esc(d.outcome)}</div>`;
  h+=stagesHTML(d.stages);
  if(d.proposal&&(d.proposal.static_routes.length||d.proposal.access_policy.length)){
    h+='<div class="cmds"><h4>предложена измена</h4>';
    d.proposal.static_routes.forEach(r=>h+=`<div class="cmd"><span class="dev">${esc(r.node)}</span>
      <span>рута ${esc(r.prefix)} преку ${esc(r.next_hop)}</span></div>`);
    d.proposal.access_policy.forEach(r=>h+=`<div class="cmd"><span class="dev">${esc(r.node)}</span>
      <span>${esc(r.action)} ${esc(r.src)} → ${esc(r.dst)}</span></div>`);
    h+=`<h4 style="border-top:1px solid var(--line)">команди што ќе бидат применети на рутерот</h4>
        <div class="console">${esc(cliPreview(d.proposal))}</div>`;
    if(d.approvable){
      h+=`<div class="actions" style="flex-direction:column;align-items:stretch;gap:7px">
        <div class="hash">SHA-256 ${esc(d.sha256)}</div>
        <div style="display:flex;gap:9px">
          <button class="primary" onclick='approve(this,${JSON.stringify(d.proposal)},"${d.sha256}")'>ОДОБРИ и примени</button>
          <button onclick="this.closest('.cmds').remove()">Одбиј</button>
        </div></div>`;
    }
    h+='</div>';
  }
  wait.innerHTML=h;
}
async function approve(btn,proposal,sha){
  btn.disabled=true; btn.textContent='проверка на отпечатокот…';
  const d=await api('/api/approve',{proposal,sha256:sha});
  const box=btn.closest('.cmds'); box.querySelector('.actions').remove();
  let h=`<div class="small" style="padding:11px 13px;color:${d.integrity==='VERIFIED'?'var(--ok)':'var(--bad)'}">
    интегритет SHA-256: ${esc(INTEGRITY_TEXT[d.integrity]||d.integrity)}</div>`;
  if(!d.deployed){
    h+=`<div class="small" style="padding:0 13px 11px;color:var(--bad)">${esc(d.message||'')}</div>`;
  }else{
    h+='<h4 style="border-top:1px solid var(--line)">запис за извршувањето</h4><div class="console">';
    (d.applied||[]).forEach(a=>h+=esc('ok   '+a)+'\n');
    (d.errors||[]).forEach(a=>h+=esc('ERR  '+a)+'\n');
    h+='</div>';
  }
  box.insertAdjacentHTML('beforeend', h);
  if(d.outcome)box.insertAdjacentHTML('afterend',verdictHTML(d.outcome));
  refresh(); $('chat').scrollTop=$('chat').scrollHeight;
}
async function ask(){
  if(busy)return; const q=$('q').value.trim(); if(!q)return;
  busy=true; $('send').disabled=true; $('q').value=''; $('q').style.height='auto';
  add(`<div class="bubble">${esc(q)}</div>`,'me');
  const wait=add(`<div class="bubble">${WAIT} размислување…</div>`,'bot');
  try{
    if(mode==='guarded'){await askGuarded(q,wait);return}
    const d=await api('/api/ask',{request:q,model:$('model').value});
    if(d.error){wait.innerHTML=`<div class="bubble">грешка: ${esc(d.error)}</div>`;return}
    let h=`<div class="bubble">${esc(d.answer)}</div>
      <div class="meta">${esc(d.model)} · ${d.latency_s} s</div>`;
    if(d.commands.length){
      h+='<div class="cmds"><h4>предложени команди</h4>';
      d.commands.forEach(c=>{
        h+=`<div class="cmd ${c.refused?'refused':''}"><span class="dev">${esc(c.device)}</span>
          <span>${esc(c.command)}${c.refused?`<div class="why">одбиено: ${esc(c.refused)}</div>`:''}</span></div>`;
      });
      const runnable=d.commands.filter(c=>!c.refused);
      if(runnable.length)h+=`<div class="actions">
        <button class="primary" onclick='apply(this,${JSON.stringify(runnable)})'>Примени ${runnable.length} команди</button>
        <button onclick="this.closest('.cmds').remove()">Отфрли</button></div>`;
      h+='</div>';
    }
    wait.innerHTML=h;
  }catch(e){wait.innerHTML=`<div class="bubble">барањето не успеа: ${esc(e.message)}</div>`}
  finally{busy=false;$('send').disabled=false;$('chat').scrollTop=$('chat').scrollHeight}
}
async function apply(btn,cmds){
  btn.disabled=true; btn.textContent='примена…';
  const d=await api('/api/apply',{commands:cmds});
  const box=btn.closest('.cmds'); box.querySelector('.actions').remove();
  box.insertAdjacentHTML('beforeend', applyLogHTML(d));
  box.insertAdjacentHTML('afterend',verdictHTML(d.outcome));
  refresh(); $('chat').scrollTop=$('chat').scrollHeight;
}
async function doReset(){
  const d=await api('/api/reset',{});
  add(`<div class="bubble">Лабораторијата е вратена — ${d.ok?'состојбата повторно одговара на намерата':'ВРАЌАЊЕТО НЕ УСПЕА, проверете ги рутерите'}</div>`,'bot');
  refresh();
}
async function save(){const d=await api('/api/save',{}); $('saved').textContent=d.path||''}
// Проверките што ја смениле состојбата се обележуваат кратко, за да се види
// што се променило по последната измена, а не само какво е сегашното мнение.
let lastChecks={};
async function refresh(){
  const o=await api('/api/validate');
  const s=$('status'); const ok=o.verdict==='MATCHES_INTENT';
  s.className='pill '+(ok?'ok':'bad');
  s.textContent=ok?'СОГЛАСНО СО НАМЕРАТА':'НЕ Е СОГЛАСНО СО НАМЕРАТА';
  const checks=o.checks||[], previous=lastChecks;
  let changed=0;
  lastChecks={};
  $('checks').innerHTML=checks.map(c=>{
    lastChecks[c.id]=c.result;
    // Обележувањето се исцртува веднаш со редот, а се повлекува со премин:
    // ослонување на requestAnimationFrame би пропаднало кога прозорецот не
    // се исцртува, а токму тогаш промената е најлесно да се пропушти.
    const moved=previous[c.id]&&previous[c.id]!==c.result;
    if(moved)changed++;
    return `<div class="check${moved?' changed':''}" data-id="${esc(c.id)}">`
      +`<span class="id">${esc(c.id)}</span>`
      +`<span class="${c.result==='PASS'?'p':'f'}">${esc(c.result)}</span></div>`;
  }).join('');
  if(changed){
    setTimeout(()=>document.querySelectorAll('.check.changed')
      .forEach(el=>el.classList.remove('changed')),1200);
  }
  await showDevice();
  await showHistory();
}

// ---- панел по уред -------------------------------------------------------
async function fillDeviceList(){
  const t=await api('/api/topology');
  const names=Object.keys(t.nodes||{});
  const cur=$('dev').value;
  $('dev').innerHTML=names.map(n=>
    `<option ${n===cur?'selected':''}>${n} · ${t.nodes[n].kind}</option>`).join('');
}
function toggleBlock(btn){
  const block=btn.closest('.devblock');
  block.classList.toggle('open');
  btn.textContent=block.classList.contains('open')?'собери':'прикажи сè';
}
async function showDevice(){
  const raw=$('dev').value; if(!raw) return;
  const name=raw.split(' · ')[0];
  $('devbody').classList.add('loading');
  const d=await api('/api/device?name='+encodeURIComponent(name));
  $('devbody').classList.remove('loading');
  if(d.error){$('devbody').textContent=d.error; return}
  const block=(t,v)=>v?`<div class="devblock"><div class="lbl">
      <span class="meta">${t}</span>
      <button class="lnk" onclick="toggleBlock(this)">прикажи сè</button></div>
    <pre>${esc(v)}</pre></div>`:'';
  $('devbody').innerHTML =
    block('конфигурација', d.config) + block('адреси', d.addresses) +
    block('рутирачка табела', d.routes) + block('правила FORWARD', d.rules) +
    block('OSPF соседи', d.neighbors);
}
// Кликнување на јазол во дијаграмот го отвора истиот панел: дијаграмот и
// списокот со уреди покажуваат на иста работа.
function selectNode(name){
  document.querySelectorAll('svg .tnode.sel').forEach(g=>g.classList.remove('sel'));
  document.querySelectorAll(`svg .tnode[data-node="${name}"]`)
    .forEach(g=>g.classList.add('sel'));
  const select=$('dev');
  for(const option of select.options){
    if(option.value.split(' · ')[0]===name){select.value=option.value;break}
  }
  const body=$('devbody');
  body.classList.remove('enter'); void body.offsetWidth; body.classList.add('enter');
  $('devsec').scrollIntoView({block:'nearest',behavior:REDUCED?'auto':'smooth'});
  showDevice();
}
document.addEventListener('click',e=>{
  const node=e.target.closest?e.target.closest('.tnode'):null;
  if(node&&node.dataset.node)selectNode(node.dataset.node);
});
document.addEventListener('keydown',e=>{
  if(e.key!=='Enter'&&e.key!==' ')return;
  const node=e.target.closest?e.target.closest('.tnode'):null;
  if(node&&node.dataset.node){e.preventDefault();selectNode(node.dataset.node)}
});

// ---- внесување грешки ----------------------------------------------------
async function fillFaults(){
  const f=await api('/api/faults');
  $('faults').innerHTML=(f.faults||[]).map(x=>
    `<div style="margin-bottom:9px">
       <div><b>${esc(x.title)}</b></div>
       <div style="opacity:.75;margin:2px 0 4px">${esc(x.why)}</div>
       <button onclick="doFault('${x.id}',false)">Внеси</button>
       <button onclick="doFault('${x.id}',true)">Врати</button>
     </div>`).join('');
}
async function doFault(id,undo){
  const wait=add(`<div class="bubble">${WAIT} ${undo?'враќање':'внесување'}…</div>`,'bot');
  try{
    const d=await api('/api/fault',{id:id,undo:undo});
    if(d.error){wait.innerHTML=`<div class="bubble">${esc(d.error)}</div>`;return}
    const steps=d.entry.steps.map(s=>{
      const add=(s.diff.added||[]).map(x=>'+ '+x).join('\n');
      const rem=(s.diff.removed||[]).map(x=>'- '+x).join('\n');
      return `${s.device}# ${s.command}\nrc=${s.rc}\n${add}\n${rem}`.trim();
    }).join('\n\n');
    wait.innerHTML=`<div class="meta" style="margin:0 0 6px 3px">${undo?'ГРЕШКАТА Е ВРАТЕНА':'ВНЕСЕНА Е ГРЕШКА'} · ${esc(d.entry.title)}</div>
      <pre>${esc(steps)}</pre>${verdictHTML(d.outcome)}`;
  }catch(e){wait.innerHTML=`<div class="bubble">не успеа: ${esc(e.message)}</div>`}
  await refresh();
  $('chat').scrollTop=$('chat').scrollHeight;
}

// ---- историја ------------------------------------------------------------
let historyCount=0;
async function showHistory(){
  const h=await api('/api/history');
  const items=h.history||[];
  if(!items.length){$('history').textContent='сè уште нема промени';return}
  const fresh=Math.max(0, items.length-historyCount);
  historyCount=items.length;
  $('history').innerHTML=items.slice().reverse().map((e,i)=>{
    const changes=(e.steps||[]).flatMap(s=>
      (s.diff?(s.diff.added||[]).map(x=>'+ '+x).concat((s.diff.removed||[]).map(x=>'- '+x)):[]));
    return `<div class="hitem${i<fresh?' new':''}">
      <div><b>${esc(e.title)}</b></div>
      <div style="opacity:.7">${esc(e.time)} · ${esc(e.kind)}${e.verdict?' · '+esc(e.verdict):''}</div>
      ${changes.length?`<pre style="margin-top:4px">${esc(changes.join('\n'))}</pre>`:''}
    </div>`;
  }).join('');
}
function reviewHTML(d){
  const f=d.facts;
  let h=`<div class="meta" style="margin:0 0 6px 3px">ПРЕГЛЕД НА ТОПОЛОГИЈАТА · ${esc(f.lab)}</div>
    <div class="review">
      <div class="graph">${d.svg}</div>
      <div class="stats">
        <div class="stat"><b>${f.routers}</b><span>рутери</span></div>
        <div class="stat"><b>${f.switches||0}</b><span>комутатори</span></div>
        <div class="stat"><b>${f.hosts}</b><span>крајни уреди</span></div>
        <div class="stat"><b>${f.links}</b><span>врски</span></div>
        <div class="stat"><b>${f.segments}</b><span>сегменти</span></div>
        <div class="stat"><b>${f.route_facts}</b><span>факти за рути</span></div>
        <div class="stat"><b>${f.deny_rules}</b><span>правила за забрана</span></div>
      </div>`;
  if(d.narrative)h+=`<div class="narr">${esc(d.narrative)}</div>
    <div class="meta" style="padding:0 15px 10px">опис од моделот ${esc(d.model)}${d.latency_s?' · '+d.latency_s+' s':''}</div>`;
  h+=`<div class="pc">
        <div><h5 class="ok">Предности</h5>${f.pros.map(x=>`<div class="li">+ ${esc(x)}</div>`).join('')}</div>
        <div><h5 class="bad">Ограничувања</h5>${f.cons.map(x=>`<div class="li">− ${esc(x)}</div>`).join('')}</div>
      </div>`;
  const recs=f.recommendations||[];
  if(recs.length){
    h+='<h5 class="rechead">Препорачани измени во конфигурацијата</h5>';
    recs.forEach(r=>{
      h+=`<div class="rec">
        <span class="prio ${esc(r.priority)}">${esc(PRIO_TEXT[r.priority]||r.priority)}</span>
        <div><div class="rtitle">${esc(r.title)}</div>
          <div class="rdetail">${esc(r.detail)}</div></div></div>`;
    });
  }
  h+='</div>';
  h+=verdictHTML(d.outcome);
  return h;
}
// Switching model re-runs the review, so the panel always reflects the model
// currently selected rather than whichever one happened to run at startup.
async function onModelChange(){
  const m=$('model').value;
  add(`<div class="bubble">Моделот е сменет на <b>${esc(m)}</b> — прегледот на
    топологијата се извршува повторно. Првото барање по промената трае подолго
    додека моделот се вчитува.</div>`,'bot');
  await reviewTopology();
}
async function reviewTopology(){
  if(busy)return;
  busy=true; $('send').disabled=true;
  const e=$('chat').querySelector('.empty'); if(e)e.remove();
  const wait=add(`<div class="bubble">${WAIT}
    <b>${esc($('model').value)}</b> ја прегледува топологијата…</div>`,'bot');
  try{
    const d=await api('/api/review?model='+encodeURIComponent($('model').value));
    if(d.error){wait.innerHTML=`<div class="bubble">прегледот не успеа: ${esc(d.error)}</div>`;return}
    wait.classList.add('wide');
    wait.innerHTML=reviewHTML(d);
  }catch(err){wait.innerHTML=`<div class="bubble">прегледот не успеа: ${esc(err.message)}</div>`}
  finally{busy=false;$('send').disabled=false}
  $('chat').scrollTop=$('chat').scrollHeight;
}
async function analyzeTopology(){
  const wait=add(`<div class="bubble">${WAIT} читање на топологијата во моментот…</div>`,'bot');
  try{
    const d=await api('/api/analyze?model='+encodeURIComponent($('model').value));
    if(d.error){wait.innerHTML=`<div class="bubble">анализата на топологијата е недостапна: ${esc(d.error)}</div>`;return}
    wait.innerHTML=`<div class="meta" style="margin:0 0 6px 3px">АНАЛИЗА НА ТОПОЛОГИЈАТА ПРИ ПУШТАЊЕ</div>
      <div class="bubble">${esc(d.analysis)}</div>
      <div class="meta">${esc(d.model)} · ${d.latency_s} s · прочитано од рутерите во моментот</div>
      ${verdictHTML(d.outcome)}`;
  }catch(e){wait.innerHTML=`<div class="bubble">анализата на топологијата не успеа: ${esc(e.message)}</div>`}
  $('chat').scrollTop=$('chat').scrollHeight;
}
// Примерите ги именуваат мрежите и уредите на вчитаната лабораторија, за да
// не поканат на прашање за уред што не постои.
async function fillExamples(){
  const box=$('examples'); if(!box)return;
  const d=await api('/api/examples');
  box.innerHTML=(d.examples||[]).map(x=>
    `<span class="chip" onclick="fill(this)">${esc(x)}</span>`).join('');
}
(async()=>{
  const m=await api('/api/models');
  $('model').innerHTML=m.models.map(n=>`<option ${n===m.default?'selected':''}>${n}</option>`).join('')
    ||`<option>${m.default}</option>`;
  await fillExamples();
  await fillDeviceList();
  await fillFaults();
  await refresh(); $('q').focus();
  reviewTopology();
})();
</script></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--intent", default=str(DEFAULT_INTENT))
    parser.add_argument("--topology",
                        help="lab .clab.yml; defaults to the one beside --intent")
    parser.add_argument("--open", action="store_true", help="open a browser")
    args = parser.parse_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    STATE["intent_path"] = Path(args.intent)
    STATE["intent"] = load_intent(STATE["intent_path"])
    STATE["topology_path"] = (Path(args.topology) if args.topology
                              else find_topology_for(STATE["intent_path"]))

    # A previous dashboard may still hold the port; step forward until one
    # is free rather than failing with "Address already in use".
    server, port = None, args.port
    for candidate in range(args.port, args.port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        print(f"error: no free port between {args.port} and {args.port + 19}",
              file=sys.stderr)
        return 1
    if port != args.port:
        print(f"port {args.port} was busy — using {port} instead")
    url = f"http://127.0.0.1:{port}/"
    print(f"Network Lab dashboard -> {url}")
    print(f"ollama:   {OLLAMA_URL}")
    print(f"intent:   {STATE['intent_path']}")
    print(f"topology: {STATE['topology_path']}")
    print(f"prefix:   {PREFIX}")
    print("Ctrl+C to stop.")
    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
