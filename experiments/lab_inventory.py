#!/usr/bin/env python3
"""
Го составува описот на лабораторијата за барањето до моделот, од самата
датотека со намера.

Зошто вака: описот на мрежата што го добива моделот, ограничувањата што ги
проверува портата и проверките што ги врши валидаторот мора да потекнуваат од
еден извор. Ако описот се пишува рачно, тој може тивко да се разиде од она што
се проверува, па моделот ќе биде оценуван според мрежа што не постои.

Оригиналниот `llm/prompt_template.txt` намерно останува непроменет: со него се
изведени 360-те извршувања на замрзнатата кампања и секоја измена во него би ја
направила таа кампања неповторлива. Новите лаборатории користат
`llm/prompt_template_multi.txt`, кој на местото на инвентарот има ознака.
"""
from __future__ import annotations

import ipaddress
from pathlib import Path

import yaml


def _transit_pairs(intent: dict) -> list[tuple[str, dict]]:
    """(подмрежа, {рутер: адреса}) за секоја транзитна врска.

    Двете лаборатории го запишуваат ова различно и обете облици мораат да се
    поддржат, инаку описот тивко излегува без транзитни врски и без дозволени
    следни скокови — а моделот тогаш се оценува според мрежа што не постои:

        transit:  {subnet: ..., r1: ..., r2: ...}          (една врска)
        transits: [{subnet: ..., ips: {r1: ..., r2: ...}}]  (список)
    """
    pairs: list[tuple[str, dict]] = []
    for item in intent.get("transits", []):
        pairs.append((item["subnet"], item.get("ips", {})))
    single = intent.get("transit")
    if isinstance(single, dict):
        ips = {k: v for k, v in single.items() if k != "subnet"}
        pairs.append((single["subnet"], ips))
    return pairs


def check_describable(intent: dict) -> None:
    """Откажува гласно ако описот би излегол непотполн.

    Тивок непотполн опис е поопасен од грешка: барањето и понатаму се
    испраќа, моделот одговара, а мерењето изгледа исправно.
    """
    if not _transit_pairs(intent):
        raise ValueError(
            "намерата нема ниту 'transit' ниту 'transits'; описот би останал "
            "без транзитни врски и без дозволени следни скокови")
    hops = valid_next_hops(intent)
    empty = [d for d in routers(intent) if not hops.get(d)]
    if empty:
        raise ValueError(
            f"овие рутери немаат ниту еден дозволен следен скок: "
            f"{', '.join(empty)}. Провери ги транзитните врски во намерата")


def valid_next_hops(intent: dict) -> dict[str, set[str]]:
    """За секој рутер: адресите преку кои смее да води рута.

    Тоа се адресите на соседите на транзитните врски на кои тој самиот стои.
    """
    hops: dict[str, set[str]] = {}
    for _, ips in _transit_pairs(intent):
        for router in ips:
            others = {addr for other, addr in ips.items() if other != router}
            hops.setdefault(router, set()).update(others)
    return hops


def connected_networks(intent: dict) -> dict[str, set[str]]:
    """Мрежите што секој рутер ги гледа непосредно, без посредник."""
    nets: dict[str, set[str]] = {}
    for seg in (intent.get("segments") or {}).values():
        router = seg.get("connected_to")
        if router:
            nets.setdefault(router, set()).add(seg["subnet"])
    for subnet, ips in _transit_pairs(intent):
        for router in ips:
            nets.setdefault(router, set()).add(subnet)
    return nets


def routers(intent: dict) -> list[str]:
    names: set[str] = set()
    for seg in (intent.get("segments") or {}).values():
        if seg.get("connected_to"):
            names.add(seg["connected_to"])
    for _, ips in _transit_pairs(intent):
        names.update(ips)
    for fact in intent.get("config_facts", []):
        names.add(fact["node"])
    return sorted(names)


def describe(intent: dict) -> str:
    """Инвентарот и политиката, како текст за вметнување во барањето."""
    check_describable(intent)
    devs = routers(intent)
    hops = valid_next_hops(intent)
    conn = connected_networks(intent)

    lines = ["Laboratory inventory:",
             f"- Devices: {', '.join(devs)} only."]

    lines.append("- Networks:")
    for name, seg in sorted((intent.get("segments") or {}).items()):
        lines.append(f"    {seg['subnet']:<16} ({name}, gateway {seg['gateway']})")
    for subnet, ips in _transit_pairs(intent):
        pairs = ", ".join(f"{r} = {a}" for r, a in sorted(ips.items()))
        lines.append(f"    {subnet:<16} (transit; {pairs})")

    lines.append("- Directly connected networks:")
    for dev in devs:
        nets = ", ".join(sorted(conn.get(dev, ()), key=_net_key))
        lines.append(f"    {dev}: {nets or 'none'}")

    lines.append("- Valid static-route next hops:")
    for dev in devs:
        allowed = sorted(hops.get(dev, ()), key=_addr_key)
        lines.append(f"    {dev}: {', '.join(allowed) if allowed else 'none'}")

    lines.append("- Never propose a static route to a network directly "
                 "connected to that router.")

    rules = (intent.get("policy_rules") or {})
    lines += ["", "Declared access policy:"]
    for rule in rules.get("must_deny", []):
        where = f" (enforced on {rule['node']})" if rule.get("node") else ""
        lines.append(f"- {rule['src']} to {rule['dst']} MUST be denied{where}.")
    for rule in rules.get("must_allow", []):
        lines.append(f"- {rule['src']} to {rule['dst']} MUST be allowed.")
    return "\n".join(lines)


def _net_key(subnet: str):
    return ipaddress.ip_network(subnet)


def _addr_key(addr: str):
    return ipaddress.ip_address(addr)


def build_prompt(template: Path, intent_path: Path, schema: str,
                 requirement: str) -> str:
    intent = yaml.safe_load(Path(intent_path).read_text(encoding="utf-8"))
    text = Path(template).read_text(encoding="utf-8")
    if "{{INVENTORY}}" not in text:
        raise ValueError(
            f"{template} нема ознака {{{{INVENTORY}}}}; за лаборатории со "
            "изведен инвентар користи llm/prompt_template_multi.txt")
    return (text
            .replace("{{INVENTORY}}", describe(intent))
            .replace("{{SCHEMA}}", schema)
            .replace("{{REQUIREMENT}}", requirement))


if __name__ == "__main__":
    import sys
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "intent/intended_state.yaml")
    print(describe(yaml.safe_load(path.read_text(encoding="utf-8"))))
