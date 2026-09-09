#!/usr/bin/env python3
"""
Динамички инвентар за Ansible, изведен од датотеката со намера.

Рачно напишаниот инвентар е уште еден опис на истата мрежа, а секој дополнителен
опис може тивко да се разиде од останатите. Тука инвентарот се пресметува во
моментот на извршување од `intent/*.yaml`, па Ansible гледа точно исти уреди и
сегменти како барањето до моделот, детерминистичката порта и валидаторот.

Со тоа датотеката со намера има четири потрошувачи, а не еден:
  1. описот на мрежата во барањето до моделот   (experiments/lab_inventory.py)
  2. ограничувањата на портата пред примена     (validation/static_gate.py)
  3. проверките по примената                    (validation/dynamic_validate.py)
  4. инвентарот и променливите на Ansible       (оваа датотека)

Употреба:
    export THESIS_INTENT=labs/topology-ent/intent_ent.yaml
    export CLAB_PREFIX=clab-thesis-net-ent-
    ansible-playbook -i ansible/inventory_from_intent.py ansible/validate_ent.yml

Проверка на самиот инвентар:
    ansible-inventory -i ansible/inventory_from_intent.py --list
"""
import json
import os
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
DEFAULT_INTENT = REPO / "intent" / "intended_state.yaml"


def load_intent() -> tuple[dict, Path]:
    raw = os.environ.get("THESIS_INTENT")
    path = Path(raw) if raw else DEFAULT_INTENT
    if not path.is_absolute():
        path = REPO / path
    if not path.exists():
        raise SystemExit(f"нема таква датотека со намера: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")), path


def routers(intent: dict) -> set[str]:
    names: set[str] = set()
    for seg in (intent.get("segments") or {}).values():
        if seg.get("connected_to"):
            names.add(seg["connected_to"])
    for item in intent.get("transits", []):
        names.update(item.get("ips", {}))
    single = intent.get("transit")
    if isinstance(single, dict):
        names.update(k for k in single if k != "subnet")
    for fact in intent.get("config_facts", []):
        if fact.get("node"):
            names.add(fact["node"])
    return names


def endpoints(intent: dict) -> set[str]:
    """Крајните уреди се појавуваат како извор на проверките за достапност."""
    return {item["src"] for item in intent.get("reachability", [])
            if item.get("src")}


def build() -> dict:
    intent, path = load_intent()
    prefix = os.environ.get("CLAB_PREFIX", "clab-thesis-net-")
    rtrs, hosts = routers(intent), endpoints(intent) - routers(intent)

    hostvars: dict[str, dict] = {}
    for name in sorted(rtrs | hosts):
        hostvars[name] = {
            "ansible_host": f"{prefix}{name}",
            "node_role": "router" if name in rtrs else "endpoint",
        }

    # Секој рутер ги носи своите забрани и очекувани рути, за playbook-от да не
    # ги бара сам — филтрирањето овде се прави еднаш, на едно место.
    for name in rtrs:
        hostvars[name]["deny_rules"] = [
            {"src": r["src"], "dst": r["dst"]}
            for r in (intent.get("policy_rules") or {}).get("must_deny", [])
            if r.get("node") == name
        ]
        hostvars[name]["route_facts"] = [
            f for f in intent.get("config_facts", [])
            if f.get("node") == name and f.get("kind") == "route"
        ]
        hostvars[name]["rule_facts"] = [
            f for f in intent.get("config_facts", [])
            if f.get("node") == name and f.get("kind") == "iptables_rule"
        ]

    return {
        "_meta": {"hostvars": hostvars},
        "all": {
            "children": ["routers", "endpoints"],
            "vars": {
                "ansible_connection": "community.docker.docker",
                "ansible_user": "root",
                "lab_name": intent.get("lab", "lab"),
                "intent_file": str(path),
                "clab_prefix": prefix,
                "reachability_checks": intent.get("reachability", []),
                "must_allow": (intent.get("policy_rules") or {}).get(
                    "must_allow", []),
            },
        },
        "routers": {"hosts": sorted(rtrs)},
        "endpoints": {"hosts": sorted(hosts)},
    }


def main() -> int:
    if "--host" in sys.argv:
        # Целата состојба е во _meta, па поединечно прашање враќа празно.
        print(json.dumps({}))
        return 0
    if "--list" not in sys.argv:
        print(__doc__)
        return 2
    print(json.dumps(build(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
