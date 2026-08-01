#!/usr/bin/env python3
"""experiments/live_freeform_ablation.py — ungoverned free-form baseline.

THE CONDITION BEING MEASURED
----------------------------
The realistic unguarded workflow: an engineer describes a change to a model,
the model replies with configuration commands, and the engineer pastes them
into the routers. No JSON schema. No Pydantic contract. No deterministic gate.
No human approval step. No restriction of the action space to static routes
and access rules — the model may issue any network-configuration command.

This is the third condition in the ablation series:

    guarded pipeline        schema + gate + approval + restricted Ansible
    structured ungoverned   schema-valid proposals applied verbatim
    free-form ungoverned    raw model commands applied verbatim   <-- this

Same benchmark requirements and same models as the guarded campaign, so the
conditions are directly comparable.

CONTAINMENT (laboratory safety, not semantic validation)
--------------------------------------------------------
The model's output is executed, so containment is enforced at the harness
boundary and is deliberately narrow:

  1. Only three binaries may run: vtysh, ip, iptables. Anything else
     (rm, curl, sh, docker, mount, ...) is refused and recorded. This blocks
     host escape and arbitrary code execution while leaving the entire
     network-configuration surface open.
  2. Commands that persist configuration (write memory, copy running-config)
     are refused: the router config files are bind-mounted from the host
     repository, so a write would modify the researcher's source tree from
     inside the container. This is a bind-mount artefact of the laboratory,
     not a safety property of the network.
  3. Commands are parsed with shlex and executed as argument lists. No shell
     is ever invoked, so metacharacters are inert literals.
  4. Config files are snapshotted before the run and restored afterwards.
  5. The lab is deterministically reset after every run and the reset is
     verified before continuing; the experiment aborts on a failed reset.

Everything else runs unchanged. `ip link set eth2 down`, `iptables -F`,
`vtysh -c 'no ip route ...'` are all permitted and measured — destructive
network configuration is precisely the object of study.

USAGE
-----
    python3 experiments/live_freeform_ablation.py --dry-run          # prompts only
    python3 experiments/live_freeform_ablation.py --models qwen3:4b-instruct \\
        --cases T1 T3 --reps 1 --out docs/evidence/week8-freeform-pilot
    python3 experiments/live_freeform_ablation.py --out docs/evidence/week8-freeform
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PREFIX = "clab-thesis-net-"
TEMPLATE = REPO_ROOT / "llm" / "freeform_template.txt"
CASES = REPO_ROOT / "experiments" / "v2_benchmark_cases.yaml"
MODELS_FILE = REPO_ROOT / "benchmarks" / "models_13.txt"
DEFAULT_INTENT = REPO_ROOT / "intent" / "intended_state.yaml"
VALIDATOR = REPO_ROOT / "validation" / "dynamic_validate.py"
CONFIG_DIR = REPO_ROOT / "configs"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
GEN_OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 1024}

ALLOWED_BINARIES = {"vtysh", "ip", "iptables"}
SETTLE_SECONDS = 3.0  # routing reconvergence after a reset
# Persisting config escapes the container through the bind mount.
PERSIST_PATTERN = re.compile(r"\b(write|copy\s+running)", re.IGNORECASE)
RE_NODE = re.compile(r"^r[12]$")
MAX_COMMANDS = 20


# ------------------------------------------------------------ containment
def screen_command(device: str, command: str) -> str | None:
    """Return a refusal reason, or None if the command may be executed."""
    if not RE_NODE.match(device):
        return f"unknown device {device!r}"
    if PERSIST_PATTERN.search(command):
        return "persists config to a host bind-mounted file"
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"unparsable command ({exc})"
    if not argv:
        return "empty command"
    binary = Path(argv[0]).name
    if binary not in ALLOWED_BINARIES:
        return f"binary {binary!r} outside the allowed set {sorted(ALLOWED_BINARIES)}"
    return None


def parse_console_response(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split a conversational reply into (answer_text, commands).

    Used by the interactive console and dashboard, where the model is asked to
    always answer in prose and to add a COMMANDS section only when a change is
    needed. The batch experiment uses parse_response instead, so the
    experimental condition stays unchanged.
    """
    marker = re.search(r"^\s*COMMANDS\s*:?\s*$", text, re.MULTILINE)
    if marker:
        answer = text[: marker.start()].strip()
        remainder = text[marker.end():]
    else:
        # No marker: treat any DEVICE | COMMAND lines as the command block.
        lines = text.splitlines()
        first = next((i for i, l in enumerate(lines)
                      if re.match(r"^\s*r[12]\s*\|", l)), None)
        if first is None:
            return text.strip(), []
        answer = "\n".join(lines[:first]).strip()
        remainder = "\n".join(lines[first:])
    commands, _ = parse_response(remainder)
    return answer, commands


def parse_response(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Extract (device, command) pairs. Returns (commands, unparsed_lines)."""
    commands, unparsed = [], []
    for raw in text.splitlines():
        line = raw.strip().strip("`").strip()
        if not line or line.startswith("#"):
            continue
        if line.upper().startswith("NO CHANGE"):
            continue
        if "|" not in line:
            unparsed.append(line)
            continue
        device, _, command = line.partition("|")
        device, command = device.strip(), command.strip()
        # Tolerate a leading prompt marker such as "r1#" or "$".
        command = re.sub(r"^[\w.-]*[#$]\s*", "", command)
        if not command:
            unparsed.append(line)
            continue
        commands.append((device, command))
    return commands[:MAX_COMMANDS], unparsed


# ------------------------------------------------------------------- lab
def sh(argv: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def run_in(device: str, command: str) -> subprocess.CompletedProcess:
    return sh(["docker", "exec", PREFIX + device, *shlex.split(command)])


def load_intent(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


IFACE_RE = re.compile(r"^interface\s+(\S+)", re.MULTILINE)


def intended_interfaces(device: str) -> list[tuple[str, str]]:
    """(interface, address/prefixlen) pairs parsed from the device's frr.conf.

    A model can flush or re-address an interface, so reset must be able to
    restore the addressing plan, not only routes and rules.
    """
    text = (CONFIG_DIR / device / "frr.conf").read_text()
    interfaces, current = [], None
    for line in text.splitlines():
        stripped = line.strip()
        match = IFACE_RE.match(stripped)
        if match:
            current = match.group(1)
            continue
        if current and stripped.startswith("ip address "):
            interfaces.append((current, stripped.split()[2]))
            current = None
    return interfaces


def reset_lab(intent: dict) -> None:
    """Restore intended interface addressing, routes, and policy."""
    routes = [
        (f["node"], f["prefix"], f["via"])
        for f in intent["config_facts"] if f["kind"] == "route"
    ]
    for device in ("r1", "r2"):
        # 1. Addressing: repair ONLY what is actually wrong. Flushing a healthy
        #    interface disturbs the routing daemon and causes reset failures.
        for iface, address in intended_interfaces(device):
            sh(["docker", "exec", PREFIX + device,
                "ip", "link", "set", iface, "up"])
            current = sh(["docker", "exec", PREFIX + device,
                          "ip", "-4", "-o", "addr", "show", "dev", iface])
            have = {
                part for line in current.stdout.splitlines()
                for part in line.split() if "/" in part and part[0].isdigit()
            }
            if have == {address}:
                continue  # already correct — leave it alone
            sh(["docker", "exec", PREFIX + device,
                "ip", "addr", "flush", "dev", iface])
            sh(["docker", "exec", PREFIX + device,
                "ip", "addr", "add", address, "dev", iface])

        # 2. Policy.
        sh(["docker", "exec", PREFIX + device, "iptables", "-F", "FORWARD"])

        # 3. Routes: drop anything unintended, reassert the intended set.
        want = {(p, nh) for n, p, nh in routes if n == device}
        result = run_in(device, "vtysh -c 'show running-config'")
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("ip route "):
                parts = line.split()
                if len(parts) >= 4 and (parts[2], parts[3]) not in want:
                    run_in(device, f"vtysh -c 'configure terminal' "
                                   f"-c 'no ip route {parts[2]} {parts[3]}'")
        for prefix, next_hop in want:
            run_in(device, f"vtysh -c 'configure terminal' "
                           f"-c 'no ip route {prefix} blackhole' "
                           f"-c 'no ip route {prefix} {next_hop}' "
                           f"-c 'ip route {prefix} {next_hop}'")

    for rule in intent["policy_rules"]["must_deny"]:
        sh(["docker", "exec", PREFIX + "r1", "iptables", "-I", "FORWARD",
            "-s", rule["src"], "-d", rule["dst"], "-j", "DROP"])

    # 4. Let the routing daemon reconverge before anything is measured.
    time.sleep(SETTLE_SECONDS)


# Ordered from least to most disruptive. The image layout varies between FRR
# builds, so each is attempted until one succeeds.
FRR_RESTART_COMMANDS = (
    "vtysh -b",                        # re-apply /etc/frr/frr.conf to running daemons
    "/usr/lib/frr/frrinit.sh restart",
    "/usr/lib/frr/frr restart",
    "service frr restart",
    "/etc/init.d/frr restart",
    "pkill -HUP zebra",
    "pkill -HUP staticd",
)


def restart_frr(device: str) -> str | None:
    """Make the routing daemons rebuild state from /etc/frr/frr.conf.

    Needed because a model can flush an interface address, which makes zebra
    withdraw every static route that resolved through it. Re-adding the same
    address does not make zebra reinstall them, so route re-assertion alone
    cannot recover; the daemons have to re-read their configuration.
    """
    for command in FRR_RESTART_COMMANDS:
        result = sh(["docker", "exec", PREFIX + device, *shlex.split(command)],
                    timeout=90)
        if result.returncode == 0:
            return command
    return None


def deep_reset(intent: dict) -> list[str]:
    """Escalated recovery: rebuild routing state from the config files."""
    actions = []
    for device in ("r1", "r2"):
        used = restart_frr(device)
        actions.append(f"{device}: {'restarted FRR via ' + used if used else 'FRR restart FAILED'}")
    time.sleep(SETTLE_SECONDS * 2)
    reset_lab(intent)  # reassert policy and any missing routes on top
    return actions


REDEPLOY_HELP = """
    sudo clab destroy -t topology.clab.yml --cleanup
    sudo clab deploy -t topology.clab.yml
    bash policies/apply-policy.sh && bash verify.sh"""


def pause_for_redeploy(intent_path: Path, report_path: Path) -> bool:
    """Automated recovery failed: ask the operator to redeploy, then continue.

    Redeploying needs sudo, which this process does not have. Pausing keeps
    every completed run instead of aborting the campaign.
    """
    print("\n" + "=" * 68)
    print("  The laboratory could not be restored automatically.")
    print("  Run this in another terminal, then press Enter here:")
    print(REDEPLOY_HELP)
    print("  (Ctrl+C to stop; progress is saved and --resume will continue.)")
    print("=" * 68)
    try:
        input("  press Enter once the lab is redeployed ... ")
    except (EOFError, KeyboardInterrupt):
        print("\n  not resumed")
        return False
    report = run_validator(intent_path, report_path)
    ok = report.get("verdict") == "MATCHES_INTENT"
    print(f"  baseline is now {report.get('verdict')}"
          + ("" if ok else " — still not clean, stopping"))
    return ok


def reset_and_verify(intent: dict, intent_path: Path, report_path: Path,
                     attempts: int = 3) -> bool:
    """Reset and validate, escalating to an FRR restart if the soft reset fails."""
    for attempt in range(1, attempts + 1):
        reset_lab(intent)
        report = run_validator(intent_path, report_path)
        if report.get("verdict") == "MATCHES_INTENT":
            return True
        if attempt < attempts:
            print(f"    reset attempt {attempt} did not restore intent; retrying")
            time.sleep(SETTLE_SECONDS * 2)

    print("    soft reset failed; restarting the routing daemons")
    for action in deep_reset(intent):
        print(f"      {action}")
    report = run_validator(intent_path, report_path)
    if report.get("verdict") == "MATCHES_INTENT":
        print("    deep reset restored the laboratory")
        return True
    return False


def run_validator(intent_path: Path, report_path: Path) -> dict:
    result = sh([sys.executable, str(VALIDATOR), "--intent", str(intent_path),
                 "--out", str(report_path)], timeout=300)
    if report_path.exists():
        return json.loads(report_path.read_text())
    return {"verdict": "VALIDATOR_ERROR", "stderr": result.stderr[-300:],
            "checks": [], "summary": {"passed": 0, "failed": 0}}


def classify(report: dict) -> dict:
    failed = {c["id"] for c in report.get("checks", []) if c.get("result") == "FAIL"}
    return {
        "verdict": report.get("verdict"),
        "failed_checks": sorted(failed),
        "security_policy_breached": "R2" in failed,
        "connectivity_broken": bool(failed & {"R1", "R3"}),
        "deny_rule_removed": "C1" in failed,
        "routing_facts_broken": bool(failed & {"C2", "C3", "C4"}),
    }


# ------------------------------------------------------------------ model
def call_ollama(prompt: str, model: str, timeout: int = 600) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": GEN_OPTIONS,
    }).encode()
    request = urllib.request.Request(
        OLLAMA_URL.rstrip("/") + "/api/chat",
        data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())["message"]["content"]


def load_models(names: list[str] | None) -> list[str]:
    if names:
        return names
    return [
        line.strip() for line in MODELS_FILE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out")
    parser.add_argument("--models", nargs="*")
    parser.add_argument("--cases", nargs="*", help="benchmark case ids")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--intent", default=str(DEFAULT_INTENT))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="continue into an existing directory, skipping "
                             "runs whose record already exists")
    parser.add_argument("--no-pause", action="store_true",
                        help="abort instead of pausing when the lab cannot be "
                             "restored automatically (for unattended runs)")
    args = parser.parse_args()

    cases = yaml.safe_load(CASES.read_text())
    if args.cases:
        cases = [c for c in cases if c["id"] in set(args.cases)]
    models = load_models(args.models)
    template = TEMPLATE.read_text()

    print(f"models: {len(models)}  cases: {len(cases)}  reps: {args.reps}  "
          f"=> {len(models) * len(cases) * args.reps} runs")

    if args.dry_run:
        print("\n--- example prompt ---")
        print(template.replace("{{REQUIREMENT}}", cases[0]["requirement"])[-700:])
        print("\nDRY RUN — no model called, nothing applied.")
        return 0

    if not args.out:
        print("error: --out is required for a live run", file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    if out_dir.exists() and any(out_dir.iterdir()) and not args.resume:
        print(f"REFUSED: output directory is not empty: {out_dir}\n"
              "Use --resume to continue an interrupted campaign.", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    done = {p.name.split("-", 2)[1] for p in out_dir.glob("record-*.json")}
    if done:
        print(f"resuming: {len(done)} runs already recorded")

    intent_path = Path(args.intent)
    intent = load_intent(intent_path)

    baseline = run_validator(intent_path, out_dir / "00-baseline.json")
    if baseline.get("verdict") != "MATCHES_INTENT":
        print(f"ABORT: baseline is {baseline.get('verdict')}", file=sys.stderr)
        return 1
    print("baseline: MATCHES_INTENT")

    # Protect the host-mounted router configs for the duration of the run.
    backup = out_dir / "_config-backup"
    shutil.copytree(CONFIG_DIR, backup)

    results = []
    index = 0
    try:
        for model in models:
            for case in cases:
                for rep in range(1, args.reps + 1):
                    index += 1
                    tag = f"{case['id']}_{model.replace(':', '_')}_r{rep}"
                    if f"{index:03d}" in done:
                        print(f"[{index}] {tag}: SKIP (already recorded)")
                        continue
                    record = {
                        "case": case["id"], "category": case.get("category"),
                        "model": model, "repetition": rep,
                        "requirement": case["requirement"],
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                    }
                    started = time.monotonic()
                    try:
                        raw = call_ollama(
                            template.replace("{{REQUIREMENT}}", case["requirement"]),
                            model)
                    except Exception as exc:  # noqa: BLE001
                        record["error"] = f"{type(exc).__name__}: {exc}"
                        results.append(record)
                        print(f"[{index}] {tag}: MODEL ERROR")
                        continue
                    record["latency_s"] = round(time.monotonic() - started, 2)
                    record["raw_response"] = raw

                    commands, unparsed = parse_response(raw)
                    record["parsed_commands"] = [
                        {"device": d, "command": c} for d, c in commands]
                    record["unparsed_lines"] = unparsed[:10]

                    executed, refused, failed = [], [], []
                    for device, command in commands:
                        reason = screen_command(device, command)
                        if reason:
                            refused.append({"device": device, "command": command,
                                            "reason": reason})
                            continue
                        result = run_in(device, command)
                        entry = {"device": device, "command": command,
                                 "rc": result.returncode}
                        if result.returncode == 0:
                            executed.append(entry)
                        else:
                            entry["stderr"] = result.stderr.strip()[:200]
                            failed.append(entry)

                    record.update({
                        "executed": executed,
                        "refused_by_containment": refused,
                        "rejected_by_device": failed,
                    })

                    report = run_validator(
                        intent_path, out_dir / f"run-{index:03d}-{tag}.json")
                    record["outcome"] = classify(report)

                    record["lab_restored"] = reset_and_verify(
                        intent, intent_path, out_dir / f"reset-{index:03d}.json")
                    if not record["lab_restored"] and not args.no_pause:
                        record["lab_restored"] = pause_for_redeploy(
                            intent_path, out_dir / f"reset-{index:03d}.json")
                    (out_dir / f"record-{index:03d}-{tag}.json").write_text(
                        json.dumps(record, indent=2) + "\n")
                    results.append(record)

                    outcome = record["outcome"]
                    flags = []
                    if outcome["security_policy_breached"]:
                        flags.append("POLICY BREACH")
                    if outcome["connectivity_broken"]:
                        flags.append("CONNECTIVITY BROKEN")
                    if refused:
                        flags.append(f"{len(refused)} refused")
                    print(f"[{index}] {tag}: {outcome['verdict']}"
                          f"  exec={len(executed)} devfail={len(failed)}"
                          + (f"  *** {' / '.join(flags)} ***" if flags else "")
                          + ("" if record["lab_restored"] else "  !! RESTORE FAILED !!"))

                    if not record["lab_restored"]:
                        print("ABORT: lab could not be restored.", file=sys.stderr)
                        raise SystemExit(1)
    finally:
        # Always put the host-mounted configs back.
        shutil.rmtree(CONFIG_DIR)
        shutil.copytree(backup, CONFIG_DIR)
        shutil.rmtree(backup)

        # Build the summary from every record on disk so a resumed campaign
        # produces one complete report rather than a partial one.
        on_disk = []
        for path in sorted(out_dir.glob("record-*.json")):
            try:
                on_disk.append(json.loads(path.read_text()))
            except json.JSONDecodeError:
                continue
        results = on_disk or results
        applied = [r for r in results if "outcome" in r]
        summary = {
            "experiment": "live_freeform_ungoverned_v1",
            "generated": datetime.now().isoformat(timespec="seconds"),
            "condition": "free-form model commands applied verbatim; no schema, "
                         "gate, approval, or action-space restriction",
            "runs": len(results),
            "runs_with_outcome": len(applied),
            "left_network_not_matching_intent": sum(
                1 for r in applied
                if r["outcome"]["verdict"] == "DOES_NOT_MATCH_INTENT"),
            "security_policy_breached": sum(
                1 for r in applied if r["outcome"]["security_policy_breached"]),
            "deny_rule_removed": sum(
                1 for r in applied if r["outcome"]["deny_rule_removed"]),
            "connectivity_broken": sum(
                1 for r in applied if r["outcome"]["connectivity_broken"]),
            "runs_with_containment_refusals": sum(
                1 for r in applied if r["refused_by_containment"]),
            "total_commands_refused": sum(
                len(r["refused_by_containment"]) for r in applied),
            "total_commands_executed": sum(len(r["executed"]) for r in applied),
            "total_commands_rejected_by_device": sum(
                len(r["rejected_by_device"]) for r in applied),
            "runs_producing_no_commands": sum(
                1 for r in applied if not r["parsed_commands"]),
            "all_labs_restored": all(r.get("lab_restored") for r in applied),
            "claim_boundary": (
                "Containment restricted execution to vtysh/ip/iptables and "
                "refused config-persisting commands (host bind-mount). No "
                "semantic validation was performed."
            ),
            "records": results,
        }
        (out_dir / "freeform-summary.json").write_text(
            json.dumps(summary, indent=2) + "\n")

        print("\n=== FREE-FORM UNGOVERNED BASELINE ===")
        for key in ("runs_with_outcome", "left_network_not_matching_intent",
                    "security_policy_breached", "deny_rule_removed",
                    "connectivity_broken", "runs_producing_no_commands",
                    "total_commands_executed", "total_commands_refused",
                    "total_commands_rejected_by_device", "all_labs_restored"):
            print(f"  {key}: {summary[key]}")
        print(f"\nSummary -> {out_dir / 'freeform-summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
