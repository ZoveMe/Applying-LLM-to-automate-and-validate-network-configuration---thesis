"""Pre-flight validation of FRR configs — adapted from network-config-validation skill patterns."""
import re, ipaddress, json
from collections import Counter

DANGEROUS = [
    (re.compile(r"\breload\b", re.I), "device reload"),
    (re.compile(r"\berase\s+(startup|nvram|flash)", re.I), "erase storage"),
    (re.compile(r"no\s+router\s+(bgp|ospf|eigrp)", re.I), "remove routing process"),
    (re.compile(r"no\s+interface\s+\S+", re.I), "remove interface"),
    (re.compile(r"no\s+ip\s+forwarding", re.I), "disable forwarding — kills transit"),
    (re.compile(r"shutdown", re.I), "interface shutdown"),
]

IP_RE = re.compile(r"ip address (\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})")
ROUTE_RE = re.compile(r"^ip route (\S+) (\S+)", re.M)

def analyze(name, text):
    report = {"device": name, "dangerous": [], "subnets": [], "routes": [], "issues": []}
    for i, line in enumerate(text.splitlines(), 1):
        for pat, reason in DANGEROUS:
            if pat.search(line):
                report["dangerous"].append({"line": i, "cmd": line.strip(), "reason": reason})
    for m in IP_RE.finditer(text):
        iface = ipaddress.ip_interface(f"{m.group(1)}/{m.group(2)}")
        report["subnets"].append(str(iface.network))
    for m in ROUTE_RE.finditer(text):
        prefix, nh = m.group(1), m.group(2)
        report["routes"].append({"prefix": prefix, "next_hop": nh})
        # route sanity: next hop must not be inside the destination prefix
        try:
            if ipaddress.ip_address(nh) in ipaddress.ip_network(prefix, strict=False):
                report["issues"].append(f"Route {prefix} via {nh}: next hop inside destination prefix")
        except ValueError:
            report["issues"].append(f"Route {prefix} via {nh}: unparsable")
    return report

configs = {}
for dev in ["r1", "r2"]:
    with open(f"/sessions/ecstatic-pensive-volta/mnt/thesis-net/configs/{dev}/frr.conf") as f:
        configs[dev] = f.read()

reports = [analyze(d, t) for d, t in configs.items()]

# Cross-device checks
all_subnets, all_ips = [], []
for d, t in configs.items():
    for m in IP_RE.finditer(t):
        all_ips.append((m.group(1), d))
        all_subnets.append((str(ipaddress.ip_interface(f"{m.group(1)}/{m.group(2)}").network), d))

dup_ips = [ip for ip, c in Counter(ip for ip, _ in all_ips).items() if c > 1]

overlaps = []
nets = [(ipaddress.ip_network(s), d) for s, d in all_subnets]
seen = set()
for i, (a, da) in enumerate(nets):
    for b, db in nets[i+1:]:
        if a != b and a.overlaps(b) and (str(a), str(b)) not in seen:
            overlaps.append(f"{a} ({da}) overlaps {b} ({db})")
            seen.add((str(a), str(b)))

# Next-hop reachability: every static route next hop must be on a directly connected subnet
nh_issues = []
for r in reports:
    dev = r["device"]
    connected = [ipaddress.ip_network(s) for s, d in all_subnets if d == dev]
    for route in r["routes"]:
        nh = ipaddress.ip_address(route["next_hop"])
        if not any(nh in n for n in connected):
            nh_issues.append(f"{dev}: route {route['prefix']} via {nh} — next hop not on any connected subnet")

# Bidirectional route symmetry: r1 routes to 10.0.2/24 and 10.0.99/24; r2 must route back to 10.0.1/24
symmetry = []
r1_routes = {r["prefix"] for r in reports[0]["routes"]}
r2_routes = {r["prefix"] for r in reports[1]["routes"]}
if "10.0.2.0/24" in r1_routes and "10.0.1.0/24" not in r2_routes:
    symmetry.append("Asymmetry: r1 routes to server segment but r2 has no return route to client")

result = {
    "per_device": reports,
    "duplicate_ips": dup_ips,
    "subnet_overlaps": overlaps,
    "next_hop_issues": nh_issues,
    "symmetry_issues": symmetry,
    "verdict": "PASS" if not (dup_ips or overlaps or nh_issues or symmetry
                              or any(r["dangerous"] or r["issues"] for r in reports)) else "REVIEW",
}
print(json.dumps(result, indent=2))
