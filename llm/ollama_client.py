#!/usr/bin/env python3
"""llm/ollama_client.py — LLM adapter: natural-language request -> validated proposal.

Pipeline position (the LLM is UNTRUSTED and never touches the network):

    requirement -> Ollama (schema-constrained) -> Pydantic parse
                -> deterministic static gate -> human decision

This module only PRODUCES evidence: the raw model output, the parsed
proposal, the gate verdict, and timing. It deploys nothing.

Outcomes:
    ACCEPTED                 valid proposal that passed the deterministic gate
    CLARIFICATION_REQUIRED   valid request for additional information
    REFUSED                  valid refusal of an unsafe or unsupported request
    REJECTED_GATE            valid proposal rejected by the deterministic gate
    REJECTED_SCHEMA          no schema-valid output after all retries
    ERROR_OLLAMA_UNREACHABLE the configured Ollama service could not be reached

Usage:
    python3 llm/ollama_client.py --request "..." [--model NAME] [--out FILE]
    python3 llm/ollama_client.py --request "..." --mock examples/suggestion_good.json

The --mock flag replaces the live model call with a canned response, so the
whole downstream pipeline (schema + gate + evidence) is testable
deterministically and offline — fast deterministic tests split from live
LLM calls.

Environment:
    OLLAMA_URL   default http://localhost:11434
                 (set to http://<second-pc-ip>:11434 if using the i3 host)
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "validation"))
from schema import ConfigSuggestion  # noqa: E402
from static_gate import DEFAULT_INTENT, Gate, load_intent  # noqa: E402

DEFAULT_MODEL = "qwen2.5-coder:7b-instruct-q4_K_M"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
TEMPLATE_PATH = REPO_ROOT / "llm" / "prompt_template.txt"
GEN_OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 1024}


def build_prompt(requirement: str) -> str:
    template = TEMPLATE_PATH.read_text()
    schema = json.dumps(ConfigSuggestion.model_json_schema(), indent=2)
    return template.replace("{{SCHEMA}}", schema).replace("{{REQUIREMENT}}", requirement)


def call_ollama(prompt: str, model: str, url: str, timeout: int = 600) -> str:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": ConfigSuggestion.model_json_schema(),
            "options": GEN_OPTIONS,
        }
    ).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data["message"]["content"]


def run_pipeline(requirement: str, model: str = DEFAULT_MODEL, retries: int = 3,
                 mock_raw: str = None, url: str = OLLAMA_URL) -> dict:
    evidence = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model": model if mock_raw is None else f"MOCK({model})",
        "ollama_url": url if mock_raw is None else None,
        "generation_options": GEN_OPTIONS,
        "requirement": requirement,
        "attempts": [],
        "proposal": None,
        "gate_report": None,
        "outcome": None,
        "total_latency_s": 0.0,
    }
    prompt = build_prompt(requirement)
    proposal = None
    attempts = 1 if mock_raw is not None else max(1, retries)

    for i in range(attempts):
        t0 = time.time()
        try:
            raw = mock_raw if mock_raw is not None else call_ollama(prompt, model, url)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            evidence["attempts"].append(
                {"n": i + 1, "raw": None, "schema_valid": False,
                 "error": f"ollama unreachable: {e}", "latency_s": round(time.time() - t0, 2)}
            )
            evidence["outcome"] = "ERROR_OLLAMA_UNREACHABLE"
            return evidence
        latency = round(time.time() - t0, 2)
        evidence["total_latency_s"] = round(evidence["total_latency_s"] + latency, 2)
        try:
            proposal = ConfigSuggestion.model_validate_json(raw)
            evidence["attempts"].append(
                {"n": i + 1, "raw": raw, "schema_valid": True, "error": None, "latency_s": latency}
            )
            break
        except Exception as e:
            evidence["attempts"].append(
                {"n": i + 1, "raw": raw, "schema_valid": False,
                 "error": str(e).splitlines()[0][:200], "latency_s": latency}
            )
            proposal = None

    if proposal is None:
        evidence["outcome"] = "REJECTED_SCHEMA"
        return evidence

    evidence["proposal"] = proposal.model_dump()

    # CLARIFY and REFUSE are valid decisions, but contain no configuration.
    # They do not need to enter the configuration safety gate.
    if proposal.decision == "CLARIFY":
        evidence["outcome"] = "CLARIFICATION_REQUIRED"
        return evidence

    if proposal.decision == "REFUSE":
        evidence["outcome"] = "REFUSED"
        return evidence

    # Only PROPOSE decisions reach the deterministic gate.
    gate = Gate(load_intent(Path(DEFAULT_INTENT)))
    report = gate.run(
        proposal.model_dump_json(),
        source=f"llm:{evidence['model']}",
    )
    evidence["gate_report"] = report

    if report["verdict"] != "PASS_PENDING_HUMAN_APPROVAL":
        evidence["outcome"] = "REJECTED_GATE"
    else:
        evidence["outcome"] = "ACCEPTED"

    return evidence


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM adapter with deterministic gate")
    ap.add_argument("--request", help="natural-language requirement")
    ap.add_argument("--request-file", help="file containing the requirement")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--mock", help="path to a canned raw response (skips Ollama)")
    ap.add_argument("--url", default=OLLAMA_URL)
    ap.add_argument("--out", help="write full evidence JSON to this path")
    args = ap.parse_args()

    if not args.request and not args.request_file:
        ap.error("provide --request or --request-file")
    requirement = args.request or Path(args.request_file).read_text().strip()
    mock_raw = Path(args.mock).read_text() if args.mock else None

    ev = run_pipeline(requirement, model=args.model, retries=args.retries,
                      mock_raw=mock_raw, url=args.url)

    print(f"model:    {ev['model']}")
    print(f"attempts: {len(ev['attempts'])}  total_latency: {ev['total_latency_s']}s")
    if ev["gate_report"]:
        failed = [c for c in ev["gate_report"]["checks"] if c["result"] == "FAIL"]
        print(f"gate:     {ev['gate_report']['verdict']}"
              f" (passed={ev['gate_report']['summary']['passed']},"
              f" failed={ev['gate_report']['summary']['failed']})")
        for c in failed:
            print(f"          FAIL[{c['category']}] {c['description']} {c['detail']}")
    print(f"OUTCOME:  {ev['outcome']}")
    print("NOTE: nothing was deployed. A human engineer decides what happens next.")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ev, indent=2))
        print(f"evidence written to {out}")

    return {
        "ACCEPTED": 0,
        "CLARIFICATION_REQUIRED": 0,
        "REFUSED": 0,
        "REJECTED_GATE": 1,
        "REJECTED_SCHEMA": 3,
        "ERROR_OLLAMA_UNREACHABLE": 4,
    }[ev["outcome"]]


if __name__ == "__main__":
    sys.exit(main())
