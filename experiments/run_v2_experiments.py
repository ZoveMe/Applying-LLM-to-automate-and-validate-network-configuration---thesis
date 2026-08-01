#!/usr/bin/env python3
"""Generate genuine V2 Ollama evidence without deploying configuration.

This runner reads only the scorer-side benchmark requirements, sends each
requirement to the existing LLM pipeline, and writes one evidence JSON per run.
It never invokes Docker, Containerlab, Ansible, or a deployment command.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from llm.ollama_client import DEFAULT_MODEL, OLLAMA_URL, run_pipeline  # noqa: E402


DEFAULT_CASES = REPO_ROOT / "experiments" / "v2_benchmark_cases.yaml"


def sanitize(value: str) -> str:
    """Return a stable filename-safe identifier."""
    return (
        value.replace(":", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "_")
    )


def load_cases(path: Path) -> list[dict]:
    cases = yaml.safe_load(path.read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError("the benchmark file must contain a non-empty list")

    ids = [case.get("id") for case in cases]
    if any(not case_id for case_id in ids):
        raise ValueError("every benchmark case must have an id")
    if len(ids) != len(set(ids)):
        raise ValueError("benchmark case ids must be unique")
    if any(not case.get("requirement") for case in cases):
        raise ValueError("every benchmark case must have a requirement")
    return cases


def select_cases(cases: list[dict], requested_ids: list[str] | None) -> list[dict]:
    """Select requested cases in benchmark order."""
    if not requested_ids:
        return cases

    requested = set(requested_ids)
    known = {case["id"] for case in cases}
    unknown = sorted(requested - known)
    if unknown:
        raise ValueError(f"unknown case id(s): {', '.join(unknown)}")
    return [case for case in cases if case["id"] in requested]


def installed_models(url: str, timeout: int = 10) -> set[str]:
    """Return exact model names reported by the Ollama API."""
    request = urllib.request.Request(url.rstrip("/") + "/api/tags")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    return {
        model.get("name", model.get("model", ""))
        for model in data.get("models", [])
        if model.get("name") or model.get("model")
    }


def require_models(url: str, models: list[str]) -> None:
    available = installed_models(url)
    missing = [model for model in models if model not in available]
    if missing:
        raise RuntimeError(
            "required Ollama model(s) are not installed: " + ", ".join(missing)
        )


def prepare_output_dir(path: Path) -> None:
    """Create an empty output directory without overwriting prior evidence."""
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(
            f"output directory is not empty; nothing was overwritten: {path}"
        )
    path.mkdir(parents=True, exist_ok=True)


def write_json_atomic(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def run_matrix(
    *,
    cases: list[dict],
    models: list[str],
    repetitions: int,
    retries: int,
    url: str,
    out_dir: Path,
    pipeline=run_pipeline,
) -> dict:
    """Run the live matrix and return its non-scoring manifest."""
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    if retries < 1:
        raise ValueError("retries must be at least 1")
    if not models:
        raise ValueError("at least one model is required")

    prepare_output_dir(out_dir)
    rows = []

    for model in models:
        for case in cases:
            for repetition in range(1, repetitions + 1):
                evidence = pipeline(
                    case["requirement"],
                    model=model,
                    retries=retries,
                    url=url,
                )

                if str(evidence.get("model", "")).startswith("MOCK("):
                    raise RuntimeError("live runner received mock evidence")
                if evidence.get("outcome") == "ERROR_OLLAMA_UNREACHABLE":
                    raise RuntimeError(
                        f"Ollama became unreachable during {model} {case['id']}"
                    )

                evidence["benchmark_case_id"] = case["id"]
                evidence["repetition"] = repetition

                filename = (
                    f"{case['id']}_{sanitize(model)}_run{repetition}.json"
                )
                write_json_atomic(out_dir / filename, evidence)

                row = {
                    "case": case["id"],
                    "model": model,
                    "repetition": repetition,
                    "outcome": evidence.get("outcome"),
                    "attempts": len(evidence.get("attempts", [])),
                    "latency_s": evidence.get("total_latency_s"),
                    "file": filename,
                }
                rows.append(row)
                print(
                    f"LIVE | {model} | {case['id']} run{repetition} | "
                    f"outcome={row['outcome']} | attempts={row['attempts']} | "
                    f"latency={row['latency_s']}s",
                    flush=True,
                )

    manifest = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "kind": "LIVE_OLLAMA_EXPERIMENT",
        "note": "No configuration was deployed. Human approval remains required.",
        "models": models,
        "case_ids": [case["id"] for case in cases],
        "repetitions_per_case": repetitions,
        "retries": retries,
        "ollama_url": url,
        "runs": len(rows),
        "records": rows,
    }
    write_json_atomic(out_dir / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run genuine V2 Ollama experiments without deployment"
    )
    parser.add_argument("--models", nargs="+", default=[DEFAULT_MODEL])
    parser.add_argument("--n", type=int, default=1, help="runs per case and model")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--url", default=OLLAMA_URL)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--case-ids", nargs="+")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    try:
        cases = select_cases(load_cases(Path(args.cases)), args.case_ids)
        require_models(args.url, args.models)
        print("PASS: Ollama is reachable and all requested models are installed")
        manifest = run_matrix(
            cases=cases,
            models=args.models,
            repetitions=args.n,
            retries=args.retries,
            url=args.url,
            out_dir=Path(args.out_dir),
        )
    except (
        FileExistsError,
        RuntimeError,
        ValueError,
        OSError,
        TimeoutError,
        urllib.error.URLError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    print(
        f"PASS: wrote {manifest['runs']} genuine live evidence files "
        f"plus manifest.json to {args.out_dir}"
    )
    print("PASS: Docker, Containerlab, Ansible, and deployment were not used")
    return 0


if __name__ == "__main__":
    sys.exit(main())
