#!/usr/bin/env python3
"""
Изработува лабораторија со градба каква што се среќава во претпријатие.

Разликите во однос на постојните лаборатории се суштински, не само во бројот
јазли: рутирањето е динамичко (OSPF наместо статички рути), јадрото е
удвоено, мрежата е поделена на повеќе кориснички сегменти, а пристапната
политика е распоредена на два уреди наместо на еден.

Тоа значи и нов вид проверка. Кај статичките рути се проверува дали записот е
внесен; тука се проверува дали протоколот навистина ја научил мрежата, што е
поблиску до она што се проверува во вистинска мрежа.

Извршување:  python3 tools/build_enterprise_lab.py
"""
import ipaddress
import textwrap
from pathlib import Path

LAB = Path("/sessions/ecstatic-pensive-volta/mnt/thesis-net/labs/topology-ent")

# ---------------------------------------------------------------- адресирање
#
#              srv-web .11   srv-db .12          (DMZ  10.30.30.0/24)
#                     \       /
#                      sw-dmz
#                        |
#                     fw-dmz  ── .22 ─┐
#                                     │ 10.0.0.20/30
#   pc-hq1 .11  pc-hq2 .12            │
#          \      /                   │
#          sw-hq ── edge-hq ── .6 ── core1 ── .1 ─┬─ 10.0.0.0/30
#            |                                    │
#       pc-mgmt .11 (VLAN 99)                   core2
#                                                 │
#   pc-br1 .11 ── sw-br ── edge-br ── .14 ────────┘
#
ROUTERS = {
    "core1": {
        "loopback": "10.255.0.1",
        "ifaces": [
            ("eth1", "10.0.0.1/30", "кон core2"),
            ("eth2", "10.0.0.5/30", "кон edge-hq"),
            ("eth3", "10.0.0.13/30", "кон edge-br"),
            ("eth4", "10.0.0.21/30", "кон fw-dmz"),
        ],
    },
    "core2": {
        "loopback": "10.255.0.2",
        "ifaces": [
            ("eth1", "10.0.0.2/30", "кон core1"),
            ("eth2", "10.0.0.9/30", "кон edge-hq"),
            ("eth3", "10.0.0.17/30", "кон edge-br"),
        ],
    },
    "edge-hq": {
        "loopback": "10.255.0.3",
        "ifaces": [
            ("eth1", "10.0.0.6/30", "кон core1"),
            ("eth2", "10.0.0.10/30", "кон core2"),
            ("eth3", "10.10.10.1/24", "VLAN 10 — корисници во седиштето"),
            ("eth4", "10.10.99.1/24", "VLAN 99 — управувачка мрежа"),
        ],
    },
    "edge-br": {
        "loopback": "10.255.0.4",
        "ifaces": [
            ("eth1", "10.0.0.14/30", "кон core1"),
            ("eth2", "10.0.0.18/30", "кон core2"),
            ("eth3", "10.20.20.1/24", "VLAN 20 — корисници во подружницата"),
        ],
    },
    "fw-dmz": {
        "loopback": "10.255.0.5",
        "ifaces": [
            ("eth1", "10.0.0.22/30", "кон core1"),
            ("eth2", "10.30.30.1/24", "VLAN 30 — DMZ"),
        ],
    },
}

HOSTS = {
    "pc-hq1":  ("10.10.10.11/24", "10.10.10.1", "sw-hq"),
    "pc-hq2":  ("10.10.10.12/24", "10.10.10.1", "sw-hq"),
    "pc-mgmt": ("10.10.99.11/24", "10.10.99.1", None),      # директно на edge-hq
    "pc-br1":  ("10.20.20.11/24", "10.20.20.1", "sw-br"),
    "srv-web": ("10.30.30.11/24", "10.30.30.1", "sw-dmz"),
    "srv-db":  ("10.30.30.12/24", "10.30.30.1", "sw-dmz"),
}

SWITCHES = ["sw-hq", "sw-br", "sw-dmz"]

LINKS = [
    # јадро и рабни рутери
    ("core1:eth1", "core2:eth1"),
    ("core1:eth2", "edge-hq:eth1"),
    ("core2:eth2", "edge-hq:eth2"),
    ("core1:eth3", "edge-br:eth1"),
    ("core2:eth3", "edge-br:eth2"),
    ("core1:eth4", "fw-dmz:eth1"),
    # пристапен слој
    ("edge-hq:eth3", "sw-hq:eth1"),
    ("edge-hq:eth4", "pc-mgmt:eth1"),
    ("edge-br:eth3", "sw-br:eth1"),
    ("fw-dmz:eth2", "sw-dmz:eth1"),
    ("sw-hq:eth2", "pc-hq1:eth1"),
    ("sw-hq:eth3", "pc-hq2:eth1"),
    ("sw-br:eth2", "pc-br1:eth1"),
    ("sw-dmz:eth2", "srv-web:eth1"),
    ("sw-dmz:eth3", "srv-db:eth1"),
]

# Политиката е распоредена на два уреди — токму тоа е причината зошто
# проверката мора да гледа каде е правилото, а не само дали сообраќајот тече.
POLICY = [
    ("fw-dmz", "10.10.10.0/24", "10.30.30.12/32",
     "корисниците немаат право директно до базата"),
    ("fw-dmz", "10.20.20.0/24", "10.30.30.12/32",
     "истото важи и за подружницата"),
    ("fw-dmz", "10.30.30.0/24", "10.10.10.0/24",
     "DMZ не смее да иницира кон внатрешната мрежа"),
    ("edge-hq", "10.10.10.0/24", "10.10.99.0/24",
     "корисниците немаат пристап до управувачката мрежа"),
]


def frr_conf(name, spec):
    body = [
        f"! {name} - enterprise lab",
        "frr version 9.1",
        "frr defaults traditional",
        f"hostname {name}",
        # Без оваа линија FRR не чита една заедничка frr.conf, туку бара
        # одделна датотека по демон. Тогаш ништо не се вчитува: интерфејсите
        # остануваат без адреси, OSPF не се крева, а контејнерот се рестартира.
        "service integrated-vtysh-config",
        "!",
        "ip forwarding",          # без ова рутерот не пренесува сообраќај
        "!",
        "interface lo",
        f" ip address {spec['loopback']}/32",
        "!",
    ]
    for iface, addr, opis in spec["ifaces"]:
        # Описите се пишуваат со латиница: кирилицата во frr.conf може да ја
        # прекине обработката на конфигурацијата.
        body += [f"interface {iface}", f" description {ascii_opis(opis)}",
                 f" ip address {addr}", "!"]
    body += ["router ospf", f" ospf router-id {spec['loopback']}"]
    # Мрежата мора да се пресмета од адресата и маската. Отсекување на
    # последниот октет дава погрешен резултат за /30: 10.0.0.5/30 припаѓа на
    # 10.0.0.4/30, а не на 10.0.0.0/30.
    for _, addr, _ in spec["ifaces"]:
        net = ipaddress.ip_interface(addr).network
        body.append(f" network {net} area 0")
    body += [f" network {spec['loopback']}/32 area 0", "!", "line vty", "!"]
    return "\n".join(body) + "\n"


ASCII_MAP = {
    "кон core2": "to core2", "кон core1": "to core1",
    "кон edge-hq": "to edge-hq", "кон edge-br": "to edge-br",
    "кон fw-dmz": "to fw-dmz",
    "VLAN 10 — корисници во седиштето": "VLAN 10 - HQ users",
    "VLAN 99 — управувачка мрежа": "VLAN 99 - management",
    "VLAN 20 — корисници во подружницата": "VLAN 20 - branch users",
    "VLAN 30 — DMZ": "VLAN 30 - DMZ",
}


def ascii_opis(text):
    return ASCII_MAP.get(text, text.encode("ascii", "ignore").decode() or "link")


def clab_yaml():
    lines = [
        "# Лабораторија со градба каква што се среќава во претпријатие.",
        "# Пет рутери со OSPF, удвоено јадро, три кориснички сегменти и DMZ.",
        "#",
        "# Распоредување:",
        "#   sudo clab deploy -t topology-ent.clab.yml",
        "#   bash apply-policy.sh",
        "",
        "name: thesis-net-ent",
        "",
        "topology:",
        "  nodes:",
    ]
    for name in ROUTERS:
        lines += [
            f"    {name}:",
            "      kind: linux",
            "      image: frrouting/frr:latest",
            "      binds:",
            f"        - configs/{name}/frr.conf:/etc/frr/frr.conf",
            "        - configs/daemons:/etc/frr/daemons",
        ]
    for sw in SWITCHES:
        ports = [e.split(":")[1] for pair in LINKS for e in pair
                 if e.startswith(sw + ":")]
        cmds = ["ip link add br0 type bridge", "ip link set br0 up"]
        for p in sorted(set(ports)):
            cmds += [f"ip link set {p} master br0", f"ip link set {p} up"]
        lines += [f"    {sw}:", "      kind: linux", "      image: alpine:latest",
                  "      exec:"]
        lines += [f"        - {c}" for c in cmds]
    for host, (addr, gw, _) in HOSTS.items():
        lines += [
            f"    {host}:", "      kind: linux", "      image: alpine:latest",
            "      exec:",
            f"        - ip addr add {addr} dev eth1",
            # Не се поставува стандардна рута: Containerlab веќе постави една
            # преку управувачката мрежа на eth0, а `ip route add default` не
            # заменува постоечка — тивко откажува и сообраќајот излегува преку
            # eth0 наместо во лабораторијата. Затоа се насочува само опсегот
            # што ѝ припаѓа на лабораторијата.
            f"        - ip route replace 10.0.0.0/8 via {gw} dev eth1",
        ]
    lines += ["", "  links:"]
    for a, b in LINKS:
        lines.append(f'    - endpoints: ["{a}", "{b}"]')
    return "\n".join(lines) + "\n"


def intent_yaml():
    L = [
        "# Намера за лабораторијата со градба на претпријатие.",
        "# Истата структура како кај другите лаборатории, за да можат",
        "# проверката, валидаторот и контролната табла да ја користат без измени.",
        "",
        "lab: thesis-net-ent",
        "",
        "segments:",
        "  hq-users:    {subnet: 10.10.10.0/24, connected_to: edge-hq, "
        "gateway: 10.10.10.1, host: 10.10.10.11}",
        "  hq-mgmt:     {subnet: 10.10.99.0/24, connected_to: edge-hq, "
        "gateway: 10.10.99.1, host: 10.10.99.11}",
        "  br-users:    {subnet: 10.20.20.0/24, connected_to: edge-br, "
        "gateway: 10.20.20.1, host: 10.20.20.11}",
        "  dmz:         {subnet: 10.30.30.0/24, connected_to: fw-dmz, "
        "gateway: 10.30.30.1, host: 10.30.30.11}",
        "",
        "transits:",
    ]
    tr = [("10.0.0.0/30", "core1", "10.0.0.1", "core2", "10.0.0.2"),
          ("10.0.0.4/30", "core1", "10.0.0.5", "edge-hq", "10.0.0.6"),
          ("10.0.0.8/30", "core2", "10.0.0.9", "edge-hq", "10.0.0.10"),
          ("10.0.0.12/30", "core1", "10.0.0.13", "edge-br", "10.0.0.14"),
          ("10.0.0.16/30", "core2", "10.0.0.17", "edge-br", "10.0.0.18"),
          ("10.0.0.20/30", "core1", "10.0.0.21", "fw-dmz", "10.0.0.22")]
    for subnet, a, ai, b, bi in tr:
        L.append(f"  - {{subnet: {subnet}, ips: {{{a}: {ai}, {b}: {bi}}}}}")

    L += [
        "",
        "# ---- Однесување на податочниот план ----",
        "reachability:",
        "  - {id: R1, src: pc-hq1, dst: 10.30.30.11, expect: reachable,   method: ping,",
        "     why: \"корисниците смеат до веб-серверот во DMZ\"}",
        "  - {id: R2, src: pc-hq1, dst: 10.30.30.12, expect: unreachable, method: ping,",
        "     why: \"корисниците немаат право директно до базата\"}",
        "  - {id: R3, src: pc-hq1, dst: 10.10.99.11, expect: unreachable, method: ping,",
        "     why: \"управувачката мрежа е одвоена од корисничката\"}",
        "  - {id: R4, src: pc-br1, dst: 10.10.10.11, expect: reachable,   method: ping,",
        "     why: \"подружницата и седиштето се поврзани преку јадрото\"}",
        "  - {id: R5, src: pc-hq1, dst: 10.10.10.12, expect: reachable,   method: ping,",
        "     why: \"комуникација во ист VLAN преку комутаторот\"}",
        "  - {id: R6, src: srv-web, dst: 10.10.10.11, expect: unreachable, method: ping,",
        "     why: \"DMZ не смее да иницира кон внатрешната мрежа\"}",
        "  - {id: R7, src: pc-mgmt, dst: 10.30.30.11, expect: reachable,  method: ping,",
        "     why: \"управувачката станица ги достигнува серверите\"}",
        "",
        "# ---- Однесување на контролниот план ----",
        "# Кај динамичко рутирање не се проверува внесен запис, туку дали",
        "# протоколот навистина ја научил мрежата.",
        "config_facts:",
    ]
    facts = [
        ("C1", "fw-dmz", "iptables_rule",
         "-s 10.10.10.0/24 -d 10.30.30.12/32 -j DROP",
         "забраната корисници→база е на границата на DMZ"),
        ("C2", "fw-dmz", "iptables_rule",
         "-s 10.30.30.0/24 -d 10.10.10.0/24 -j DROP",
         "DMZ не смее да иницира кон внатре"),
        ("C3", "edge-hq", "iptables_rule",
         "-s 10.10.10.0/24 -d 10.10.99.0/24 -j DROP",
         "управувачката мрежа е заштитена на рабниот рутер"),
    ]
    for i, n, k, m, w in facts:
        L.append(f'  - {{id: {i}, node: {n}, kind: {k}, match: "{m}",')
        L.append(f'     why: "{w}"}}')
    ospf = [("C4", "core1", "10.10.10.0/24", "OSPF ја научил корисничката мрежа"),
            ("C5", "edge-br", "10.10.10.0/24", "подружницата ја гледа мрежата на седиштето"),
            ("C6", "core2", "10.30.30.0/24", "второто јадро ја гледа DMZ"),
            ("C7", "edge-hq", "10.20.20.0/24", "седиштето ја гледа подружницата")]
    for i, n, p, w in ospf:
        L.append(f"  - {{id: {i}, node: {n}, kind: route, prefix: {p}, protocol: ospf,")
        L.append(f'     why: "{w}"}}')

    L += ["", "# ---- Ограничувања за проверката пред примена ----", "policy_rules:",
          "  must_deny:"]
    for node, src, dst, _ in POLICY:
        L.append(f"    - {{node: {node}, src: {src}, dst: {dst}}}")
    L += ["  must_allow:",
          "    - {src: 10.10.10.0/24, dst: 10.30.30.11/32}",
          "    - {src: 10.20.20.0/24, dst: 10.10.10.0/24}"]
    return "\n".join(L) + "\n"


def policy_sh():
    lines = [
        "#!/usr/bin/env bash",
        "# Ја применува пристапната политика на уредите што ја носат.",
        "# Идемпотентна: правилото се внесува само ако го нема.",
        "#",
        "# ВАЖЕН Е РЕДОСЛЕДОТ. iptables ги чита правилата од горе надолу и",
        "# застанува на првото што одговара. Затоа прво се внесуваат забраните,",
        "# а дозволата за веќе воспоставени врски НА КРАЈ, на позиција 1 — за да",
        "# заврши над сите нив. Ако се внесе прва, секоја наредна забрана ја",
        "# турка надолу и таа престанува да важи.",
        "set -e", 'P="clab-thesis-net-ent"', "",
    ]
    for node, src, dst, opis in POLICY:
        lines += [
            f'# {opis}',
            f'N="$P-{node}"',
            'docker exec "$N" sh -c \'command -v iptables >/dev/null 2>&1 '
            '|| apk add --no-cache iptables >/dev/null 2>&1 || true\'',
            f'if docker exec "$N" iptables -C FORWARD -s {src} -d {dst} -j DROP 2>/dev/null; then',
            f'  echo "{node}: правилото {src} -> {dst} веќе постои"',
            "else",
            f'  docker exec "$N" iptables -I FORWARD -s {src} -d {dst} -j DROP',
            f'  echo "{node}: внесено {src} -> {dst} DROP"',
            "fi", ""]
    lines += [
        "# Дозволата за воспоставени врски оди последна, на позиција 1, за да",
        "# застане над сите забрани внесени погоре. Без неа, забраната",
        "# DMZ -> внатре го блокира и одговорот на сообраќај што корисникот",
        "# сам го започнал, па дозволената насока престанува да работи.",
        'for n in fw-dmz edge-hq; do',
        '  N="$P-$n"',
        '  if docker exec "$N" iptables -C FORWARD -m conntrack '
        '--ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null; then',
        '    docker exec "$N" iptables -D FORWARD -m conntrack '
        '--ctstate ESTABLISHED,RELATED -j ACCEPT',
        "  fi",
        '  docker exec "$N" iptables -I FORWARD 1 -m conntrack '
        '--ctstate ESTABLISHED,RELATED -j ACCEPT',
        '  echo "$n: дозволата за воспоставени врски е поставена прва"',
        "done",
        "",
        'for n in fw-dmz edge-hq; do',
        '  echo "--- $n FORWARD ---"',
        '  docker exec "$P-$n" iptables -S FORWARD',
        "done",
    ]
    return "\n".join(lines) + "\n"


def readme():
    return textwrap.dedent("""\
        # Лабораторија со градба на претпријатие

        Пет рутери, три комутатори и шест крајни уреди. Замислена е како мрежа
        на организација со седиште, подружница и одвоена DMZ зона.

        ```
             srv-web        srv-db            DMZ 10.30.30.0/24
                 \\           /
                  \\  sw-dmz /
                       |
                    fw-dmz
                       |
        pc-hq1        core1 ═══ core2
             \\       /    \\    /   \\
              sw-hq /      \\  /     \\
                \\  /        \\/       \\
              edge-hq       /\\      edge-br
                 |                     |
             pc-mgmt                 sw-br
           VLAN 99                     |
                                    pc-br1
        ```

        | | |
        |---|---|
        | Јадро | core1, core2 — удвоено, поврзано меѓусебно |
        | Рабни рутери | edge-hq (седиште), edge-br (подружница) |
        | DMZ | fw-dmz — носи две од четирите забрани |
        | Рутирање | **OSPF, површина 0** — не статички рути |
        | Сегменти | корисници 10.10.10.0/24 · управување 10.10.99.0/24 · подружница 10.20.20.0/24 · DMZ 10.30.30.0/24 |
        | Политика | 4 забрани на **два различни уреди** (fw-dmz, edge-hq) |
        | Проверки | 7 за достапност · 7 конфигурациски |

        ## Што додава оваа лабораторија

        **Динамичко рутирање.** Кај претходните лаборатории рутите се внесени
        рачно, па проверката се сведува на тоа дали записот постои. Тука
        мрежите се учат преку OSPF, што значи дека проверката мора да утврди
        дали протоколот навистина ја пренел информацијата. Тоа е поблиску до
        она што се проверува во вистинска мрежа.

        **Удвоено јадро.** Секој раб е поврзан со двата основни рутери, па
        испадот на една врска не ја дели мрежата. Со статичко рутирање ваквата
        резервна патека постои физички, но не се користи; со OSPF се користи.

        **Распоредена политика.** Четирите забрани се на два уреди, а не на еден. Тоа значи
        дека предлог што ја отстранува заштитата на едно место, а ја додава на
        друго, поминува проверка на достапност, но паѓа на конфигурациска
        проверка — точно оној случај што е опишан во трудот.

        ## Распоредување

        ```bash
        cd labs/topology-ent
        sudo clab deploy -t topology-ent.clab.yml
        bash apply-policy.sh
        ```

        Имињата на контејнерите почнуваат со `clab-thesis-net-ent-`.

        ## Проверка и контролна табла

        ```bash
        export CLAB_PREFIX=clab-thesis-net-ent-
        python3 validation/dynamic_validate.py --intent labs/topology-ent/intent_ent.yaml
        python3 experiments/live_llm_dashboard.py --intent labs/topology-ent/intent_ent.yaml --open
        ```

        ## Расчистување

        ```bash
        sudo clab destroy -t topology-ent.clab.yml --cleanup
        ```

        ## Однос кон мерењата во трудот

        Оваа лабораторија **не** е дел од измерените докази. Сите резултати во
        Поглавје 6 се добиени врз основната лабораторија со два рутери, чија
        датотека со намера е дел од тоа мерење. Оваа служи за демонстрација и
        за проверка дали истиот пристап се пренесува на посложена градба.
        """)


BASE_DAEMONS = Path(
    "/sessions/ecstatic-pensive-volta/mnt/thesis-net/configs/daemons")


def daemons_file():
    """Датотеката од основната лабораторија, со вклучен само OSPF.

    Рачно напишана скратена верзија еднаш веќе не проработи: во неа
    недостасуваше `vtysh_enable=yes`, без кое обединетата конфигурација не се
    вчитува, како и повеќето `*_options` редови. Затоа тука се презема
    датотеката што работи и се менува точно една вредност.
    """
    text = BASE_DAEMONS.read_text(encoding="utf-8")
    out, changed = [], False
    for line in text.splitlines():
        if line.strip() == "ospfd=no":
            out.append("ospfd=yes")
            changed = True
        else:
            out.append(line)
    if not changed:
        raise RuntimeError(
            f"во {BASE_DAEMONS} не е најден редот 'ospfd=no' — "
            "провери ја пред да продолжиш")
    header = ["# Преземено од configs/daemons; сменето е само ospfd=no -> yes.",
              "# Не пишувај скратена верзија: без vtysh_enable=yes обединетата",
              "# конфигурација во frr.conf не се вчитува.", ""]
    return "\n".join(header + out) + "\n"


def main():
    LAB.mkdir(parents=True, exist_ok=True)
    (LAB / "configs").mkdir(exist_ok=True)
    written = []

    for name, spec in ROUTERS.items():
        d = LAB / "configs" / name
        d.mkdir(exist_ok=True)
        (d / "frr.conf").write_text(frr_conf(name, spec), encoding="utf-8")
        written.append(f"configs/{name}/frr.conf")

    (LAB / "configs" / "daemons").write_text(daemons_file(), encoding="utf-8")
    (LAB / "topology-ent.clab.yml").write_text(clab_yaml(), encoding="utf-8")
    (LAB / "intent_ent.yaml").write_text(intent_yaml(), encoding="utf-8")
    (LAB / "apply-policy.sh").write_text(policy_sh(), encoding="utf-8")
    (LAB / "README.md").write_text(readme(), encoding="utf-8")
    written += ["configs/daemons", "topology-ent.clab.yml", "intent_ent.yaml",
                "apply-policy.sh", "README.md"]

    print(f"изработено во {LAB}:")
    for w in written:
        print(f"  {w}")
    print(f"\nјазли: {len(ROUTERS)} рутери · {len(SWITCHES)} комутатори · "
          f"{len(HOSTS)} крајни уреди · {len(LINKS)} врски")


if __name__ == "__main__":
    main()
