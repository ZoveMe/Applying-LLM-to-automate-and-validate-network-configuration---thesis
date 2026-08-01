#!/usr/bin/env python3
"""experiments/run_explain_experiments.py — EXPLAIN campaign runner.

Covers the thesis-description role "објаснување на постоечки конфигурации":
raw device configuration -> Ollama (schema-constrained) -> structured
explanation. The model receives ONLY the configuration text — never the
intent file — so factuality scoring against intent/intended_state.yaml
(evaluate_explain.py) is uncontaminated.

Matrix: 2 devices x N models x N reps (default 2 x 2 x 3 = 12 live runs).

This script only PRODUCES evidence (raw output, parsed explanation,
latency). It deploys nothing and never modifies the network.

Usage:
    python3 experiments/run_explain_experiments.py --out docs/evidence/week6-explain
    python3 experiments/run_explain_experiments.py --out /tmp/explain-selftest \
        --mock examples/explanation_r1_good.json

Environment:
    OLLAMA_URL   default http://localhost:11434
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "validation"))
from explain_schema import ConfigExplanation  # noqa: E402

MODELS = ["qwen2.5-coder:7b-instruct-q4_K_M", "qwen3:4b-instruct"]
DEVICES = ["r1", "r2"]
REPS = 3
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
TEMPLATE_PATH = REPO_ROOT / "llm" / "explain_template.txt"
GEN_OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 1024}

# The applied runtime policy is part of the "existing configuration" for r1.
R1_POLICY_APPENDIX = (
    "\n! Applied firewall rules (iptables -S FORWARD):\n"
    "! -A FORWARD -s 10.0.1.0/24 -d 10.0.99.0/24 -j DROP\n"
)


def load_config(device: str, config_root: Path, appendix: bool) -> str:
    text = (config_root / device / "frr.conf").read_text()
    if appendix and device == "r1":
        text += R1_POLICY_APPENDIX
    return text


def build_prompt(device: str, config_root: Path, appendix: bool) -> str:
    template = TEMPLATE_PATH.read_text()
    schema = json.dumps(ConfigExplanation.model_json_schema(), indent=2)
    return (
        template.replace("{{SCHEMA}}", schema)
        .replace("{{DEVICE}}", device)
        .replace("{{CONFIG}}", load_config(device, config_root, appendix))
    )


def call_ollama(prompt: str, model: str, timeout: int = 600) -> str:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": ConfigExplanation.model_json_schema(),
            "options": GEN_OPTIONS,
        }
    ).encode()
    req = urllib.request.Request(
        OLLAMA_URL.rstrip("/") + "/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data["message"]["content"]


def run_one(device: str, model: str, rep: int, mock: str | None,
            config_root: Path, appendix: bool) -> dict:
    record = {
        "task": "EXPLAIN",
        "device": device,
        "model": model if not mock else f"MOCK({mock})",
        "rep": rep,
        "config_root": str(config_root),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "generation_options": GEN_OPTIONS,
        "raw_response": None,
        "schema_valid": False,
        "parsed": None,
        "error": None,
        "latency_seconds": None,
    }
    start = time.monotonic()
    try:
        if mock:
            raw = Path(mock).read_text()
        else:
            raw = call_ollama(build_prompt(device, config_root, appendix), model)
        record["raw_response"] = raw
        parsed = ConfigExplanation.model_validate_json(raw)
        record["schema_valid"] = True
        record["parsed"] = parsed.model_dump()
    except Exception as exc:  # noqa: BLE001 — evidence must record any failure
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["latency_seconds"] = round(time.monotonic() - start, 3)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="output evidence directory")
    parser.add_argument("--mock", help="canned response file (offline selftest)")
    parser.add_argument("--models", nargs="*", default=MODELS)
    parser.add_argument("--reps", type=int, default=REPS)
    parser.add_argument("--devices", nargs="*", default=DEVICES)
    parser.add_argument(
        "--config-root",
        default=str(REPO_ROOT / "configs"),
        help="directory containing <device>/frr.conf (default: thesis-core configs)",
    )
    parser.add_argument(
        "--no-appendix",
        action="store_true",
        help="do not append the r1 iptables appendix (benchmark configs embed it)",
    )
    args = parser.parse_args()
    config_root = Path(args.config_root)

    out_dir = Path(args.out)
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"REFUSED: output directory {out_dir} is not empty.", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "task": "EXPLAIN",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "devices": args.devices,
        "models": args.models,
        "reps": args.reps,
        "config_root": str(config_root),
        "appendix": not args.no_appendix,
        "mock": args.mock or False,
        "generation_options": GEN_OPTIONS,
        "ollama_url": OLLAMA_URL if not args.mock else None,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    files = ["manifest.json"]
    for device in args.devices:
        for model in args.models:
            for rep in range(1, args.reps + 1):
                record = run_one(device, model, rep, args.mock,
                                 config_root, not args.no_appendix)
                safe_model = model.replace(":", "_").replace("/", "_")
                name = f"EXPLAIN_{device}_{safe_model}_run{rep}.json"
                (out_dir / name).write_text(json.dumps(record, indent=2))
                files.append(name)
                status = "OK" if record["schema_valid"] else "SCHEMA_FAIL"
                print(f"{name}: {status} ({record['latency_seconds']}s)")

    sums = []
    for name in files:
        digest = hashlib.sha256((out_dir / name).read_bytes()).hexdigest()
        sums.append(f"{digest}  {name}")
    (out_dir / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n")
    print(f"\nDone: {len(files) - 1} runs -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
