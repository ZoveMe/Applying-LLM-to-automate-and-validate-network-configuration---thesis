#!/usr/bin/env python3
"""experiments/live_llm_console.py — interactive live LLM network console.

You type the request. A local model answers with router commands. You see
exactly what it proposes, decide whether to run it, and the independent
runtime validator reports what the network state became.

This is the ungoverned condition driven by hand: no schema, no deterministic
gate, no restricted action space. The only stop between the model and the
routers is you — which is precisely the workflow this thesis argues is
insufficient, and this console lets you demonstrate that live.

Also useful as the defence demo: type a plausible-sounding request, watch a
model produce something confident and wrong, watch the validator catch it.

CONTAINMENT (laboratory safety, unchanged from the batch experiment)
--------------------------------------------------------------------
  * only vtysh, ip and iptables may execute; anything else is refused
  * commands that persist config are refused (configs are host bind-mounted)
  * commands run as argument lists, never through a shell
  * :reset restores the intended state and verifies it

SESSION COMMANDS
----------------
    :model <name>     switch model (default: qwen2.5-coder:7b-instruct-q4_K_M)
    :models           list installed models
    :check            run the validator without changing anything
    :reset            restore the lab to intended state and verify
    :state            show routes and firewall rules on r1 and r2
    :auto on|off      apply without asking (default: off — you confirm each time)
    :save <file>      write the session transcript to a JSON file
    :help             show this
    :quit

Anything else you type is sent to the model as a network change request.

USAGE
-----
    python3 experiments/live_llm_console.py
    python3 experiments/live_llm_console.py --model llama3.1:8b-instruct-q4_K_M
"""
import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments"))

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

# Conversational template: the model always answers in prose and adds a
# COMMANDS block only when a change is needed. The batch experiment keeps
# using freeform_template.txt so its condition stays unchanged.
TEMPLATE = REPO_ROOT / "llm" / "console_template.txt"
DEFAULT_INTENT = REPO_ROOT / "intent" / "intended_state.yaml"
DEFAULT_MODEL = "qwen2.5-coder:7b-instruct-q4_K_M"
SCRATCH = REPO_ROOT / "docs" / "evidence" / "_console-scratch"

BOLD, DIM, RED, GREEN, YELLOW, RESET = (
    "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")


def installed_models() -> list[str]:
    try:
        request = urllib.request.Request(OLLAMA_URL.rstrip("/") + "/api/tags")
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
        return sorted(m.get("name", m.get("model", "")) for m in data.get("models", []))
    except Exception as exc:  # noqa: BLE001
        print(f"{RED}could not reach Ollama at {OLLAMA_URL}: {exc}{RESET}")
        return []


def show_state() -> None:
    for device in ("r1", "r2"):
        print(f"\n{BOLD}--- {device} static routes ---{RESET}")
        result = run_in(device, "vtysh -c 'show ip route static'")
        print(result.stdout.strip() or "(none)")
        print(f"{BOLD}--- {device} FORWARD chain ---{RESET}")
        result = run_in(device, "iptables -S FORWARD")
        print(result.stdout.strip() or "(none)")


def print_verdict(report: dict) -> dict:
    outcome = classify(report)
    ok = outcome["verdict"] == "MATCHES_INTENT"
    colour = GREEN if ok else RED
    print(f"\n{colour}{BOLD}VERDICT: {outcome['verdict']}{RESET}")
    if outcome["failed_checks"]:
        print(f"  failed checks: {', '.join(outcome['failed_checks'])}")
    flags = []
    if outcome["security_policy_breached"]:
        flags.append("SECURITY POLICY BREACHED — client can reach management")
    if outcome["connectivity_broken"]:
        flags.append("REQUIRED CONNECTIVITY BROKEN")
    if outcome["deny_rule_removed"]:
        flags.append("DENY RULE DESTROYED")
    for flag in flags:
        print(f"  {RED}{BOLD}*** {flag} ***{RESET}")
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--intent", default=str(DEFAULT_INTENT))
    args = parser.parse_args()

    intent_path = Path(args.intent)
    intent = load_intent(intent_path)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    template = TEMPLATE.read_text()

    model = args.model
    auto = False
    transcript = []

    print(f"{BOLD}Live LLM network console{RESET}")
    print(f"model: {model}   ollama: {OLLAMA_URL}")
    print("checking baseline ...")
    baseline = run_validator(intent_path, SCRATCH / "baseline.json")
    if baseline.get("verdict") != "MATCHES_INTENT":
        print(f"{YELLOW}warning: baseline is {baseline.get('verdict')}. "
              f"Run :reset before trusting any result.{RESET}")
    else:
        print(f"{GREEN}baseline: MATCHES_INTENT{RESET}")
    print(f"{DIM}Type a request, or :help for commands.{RESET}")

    while True:
        try:
            line = input(f"\n{BOLD}[{model}]>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue

        if line in (":quit", ":q", ":exit"):
            break
        if line == ":help":
            print(__doc__[__doc__.index("SESSION COMMANDS"):])
            continue
        if line == ":models":
            for name in installed_models():
                print(f"  {name}")
            continue
        if line.startswith(":model "):
            model = line.split(None, 1)[1].strip()
            print(f"model set to {model}")
            continue
        if line.startswith(":auto"):
            auto = line.endswith("on")
            print(f"auto-apply {'ON — commands run without asking' if auto else 'OFF'}")
            continue
        if line == ":state":
            show_state()
            continue
        if line == ":check":
            print_verdict(run_validator(intent_path, SCRATCH / "check.json"))
            continue
        if line == ":reset":
            print("resetting ...")
            ok = reset_and_verify(intent, intent_path, SCRATCH / "reset.json")
            print(f"{GREEN}lab restored{RESET}" if ok
                  else f"{RED}RESET FAILED — inspect r1/r2{RESET}")
            continue
        if line.startswith(":save "):
            path = Path(line.split(None, 1)[1].strip())
            path.write_text(json.dumps(transcript, indent=2) + "\n")
            print(f"transcript -> {path}")
            continue
        if line.startswith(":"):
            print(f"unknown command {line!r}; :help for the list")
            continue

        # ---- a request for the model ----
        entry = {"timestamp": datetime.now().isoformat(timespec="seconds"),
                 "model": model, "request": line}
        print(f"{DIM}asking {model} ...{RESET}")
        started = time.monotonic()
        try:
            raw = call_ollama(template.replace("{{REQUIREMENT}}", line), model)
        except Exception as exc:  # noqa: BLE001
            print(f"{RED}model error: {type(exc).__name__}: {exc}{RESET}")
            entry["error"] = str(exc)
            transcript.append(entry)
            continue
        entry["latency_s"] = round(time.monotonic() - started, 2)
        entry["raw_response"] = raw

        answer, commands = parse_console_response(raw)
        entry["answer"] = answer
        entry["parsed_commands"] = [{"device": d, "command": c} for d, c in commands]

        # Always show what the model said, whatever it proposed.
        if answer:
            print()
            for text_line in answer.splitlines():
                print(f"  {text_line}")
        print(f"{DIM}  ({entry['latency_s']}s){RESET}")

        if not commands:
            print(f"{DIM}no configuration commands proposed{RESET}")
            transcript.append(entry)
            continue

        print(f"\n{BOLD}proposed commands:{RESET}")
        screened = []
        for device, command in commands:
            reason = screen_command(device, command)
            screened.append((device, command, reason))
            if reason:
                print(f"  {RED}REFUSED{RESET} {device} | {command}")
                print(f"          {DIM}{reason}{RESET}")
            else:
                print(f"  {device} | {command}")

        runnable = [(d, c) for d, c, r in screened if not r]
        entry["refused_by_containment"] = [
            {"device": d, "command": c, "reason": r} for d, c, r in screened if r]
        if not runnable:
            print(f"{YELLOW}nothing runnable after containment{RESET}")
            transcript.append(entry)
            continue

        if not auto:
            answer = input(f"\napply {len(runnable)} command(s)? [y/N] ").strip().lower()
            if answer != "y":
                print("skipped")
                entry["applied"] = False
                transcript.append(entry)
                continue

        executed, failed = [], []
        for device, command in runnable:
            result = run_in(device, command)
            item = {"device": device, "command": command, "rc": result.returncode}
            if result.returncode == 0:
                executed.append(item)
                print(f"  {GREEN}ok{RESET}   {device} | {command}")
            else:
                item["stderr"] = result.stderr.strip()[:200]
                failed.append(item)
                print(f"  {YELLOW}rc={result.returncode}{RESET} {device} | {command}")
                print(f"        {DIM}{item['stderr']}{RESET}")

        entry.update({"applied": True, "executed": executed,
                      "rejected_by_device": failed})
        entry["outcome"] = print_verdict(
            run_validator(intent_path, SCRATCH / "after.json"))
        transcript.append(entry)

        if entry["outcome"]["verdict"] != "MATCHES_INTENT":
            answer = input(f"\n{YELLOW}reset the lab now? [Y/n]{RESET} ").strip().lower()
            if answer != "n":
                ok = reset_and_verify(intent, intent_path, SCRATCH / "reset.json")
                print(f"{GREEN}lab restored{RESET}" if ok
                      else f"{RED}RESET FAILED — inspect r1/r2{RESET}")

    if transcript:
        default = SCRATCH / f"session-{datetime.now():%Y%m%d-%H%M%S}.json"
        default.write_text(json.dumps(transcript, indent=2) + "\n")
        print(f"\ntranscript -> {default}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
