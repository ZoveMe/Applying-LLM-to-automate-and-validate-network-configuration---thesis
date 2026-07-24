#!/usr/bin/env python3
"""Generate thesis-ready results text and SVG figures from preserved evidence.

This is a pure offline reporting step. It reads the immutable Week 5 evidence,
checks its internal consistency, and writes deterministic derived artifacts.
It never calls Ollama, Docker, or Containerlab and never changes source
evidence.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVALUATION = (
    REPO_ROOT / "docs" / "evidence" / "week5-v2" / "31-v2-live-evaluation.json"
)
DEFAULT_MANIFEST = (
    REPO_ROOT / "docs" / "evidence" / "week5-v2" / "live-matrix" / "manifest.json"
)
DEFAULT_CASES = REPO_ROOT / "experiments" / "v2_benchmark_cases.yaml"
DEFAULT_E2E_DIR = REPO_ROOT / "docs" / "evidence" / "week5-v2" / "e2e-live"
DEFAULT_CHAPTER = REPO_ROOT / "docs" / "chapter6_results.md"
DEFAULT_FIGURE_DIR = REPO_ROOT / "docs" / "figures" / "week5-v2"

MODEL_LABELS = {
    "qwen2.5-coder:7b-instruct-q4_K_M": "Qwen2.5-Coder 7B",
    "qwen3:4b-instruct": "Qwen3 4B",
}
COLORS = ("#2563eb", "#f59e0b")
EXPECTED_ROUTE = {
    "node": "r1",
    "prefix": "10.0.2.0/24",
    "next_hop": "10.0.12.2",
}


class ResultsError(RuntimeError):
    """Raised when preserved evidence cannot support the derived report."""


@dataclass
class SourceData:
    evaluation: dict
    manifest: dict
    cases: list[dict]
    before: dict
    proposal: dict
    approval: dict
    after: dict
    proposal_bytes: bytes


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultsError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ResultsError(f"expected a JSON object in {path}")
    return value


def load_sources(
    evaluation_path: Path = DEFAULT_EVALUATION,
    manifest_path: Path = DEFAULT_MANIFEST,
    cases_path: Path = DEFAULT_CASES,
    e2e_dir: Path = DEFAULT_E2E_DIR,
) -> SourceData:
    proposal_path = e2e_dir / "34-ollama-repair-proposal.json"
    try:
        cases = yaml.safe_load(cases_path.read_text())
        proposal_bytes = proposal_path.read_bytes()
    except OSError as exc:
        raise ResultsError(f"cannot read reporting source: {exc}") from exc
    if not isinstance(cases, list):
        raise ResultsError("benchmark cases must be a YAML list")

    return SourceData(
        evaluation=_load_json(evaluation_path),
        manifest=_load_json(manifest_path),
        cases=cases,
        before=_load_json(e2e_dir / "33-missing-route-detected.json"),
        proposal=json.loads(proposal_bytes),
        approval=_load_json(e2e_dir / "35-human-approval.json"),
        after=_load_json(e2e_dir / "36-repair-validated.json"),
        proposal_bytes=proposal_bytes,
    )


def validate_sources(data: SourceData) -> None:
    evaluation = data.evaluation
    manifest = data.manifest

    if manifest.get("kind") != "LIVE_OLLAMA_EXPERIMENT":
        raise ResultsError("manifest is not labeled as live Ollama evidence")
    if manifest.get("runs") != 60:
        raise ResultsError("manifest must describe exactly 60 live runs")
    if manifest.get("repetitions_per_case") != 3:
        raise ResultsError("manifest must describe three repetitions per case")
    if len(manifest.get("models", [])) != 2:
        raise ResultsError("manifest must contain exactly two models")
    if len(manifest.get("case_ids", [])) != 10:
        raise ResultsError("manifest must contain exactly ten cases")

    if evaluation.get("live_runs") != 60:
        raise ResultsError("evaluation must contain exactly 60 live runs")
    if evaluation.get("mock_runs") != 0:
        raise ResultsError("mock runs must not be mixed into live model metrics")
    if evaluation.get("unmatched_evidence") != []:
        raise ResultsError("evaluation contains unmatched evidence")

    aggregates = evaluation.get("model_performance_live_only")
    if not isinstance(aggregates, list) or len(aggregates) != 2:
        raise ResultsError("evaluation must contain two model aggregates")
    aggregate_models = {row.get("model") for row in aggregates}
    if aggregate_models != set(manifest["models"]):
        raise ResultsError("evaluation and manifest model sets differ")
    if any(row.get("runs") != 30 for row in aggregates):
        raise ResultsError("each model aggregate must contain exactly 30 runs")

    rows = evaluation.get("per_run")
    if not isinstance(rows, list) or len(rows) != 60:
        raise ResultsError("evaluation must contain 60 per-run rows")
    for model in manifest["models"]:
        for case_id in manifest["case_ids"]:
            matching = [
                row for row in rows
                if row.get("model") == model and row.get("case") == case_id
            ]
            if len(matching) != 3:
                raise ResultsError(
                    f"expected three rows for {model}/{case_id}; got {len(matching)}"
                )

    if data.before.get("verdict") != "DOES_NOT_MATCH_INTENT":
        raise ResultsError("live repair evidence does not start with a detected fault")
    if data.before.get("summary", {}).get("failed", 0) < 1:
        raise ResultsError("fault evidence contains no failed checks")
    if data.proposal.get("outcome") != "ACCEPTED":
        raise ResultsError("repair proposal was not accepted by the guarded pipeline")
    proposal = data.proposal.get("proposal", {})
    if proposal.get("static_routes") != [EXPECTED_ROUTE]:
        raise ResultsError("live proposal does not contain the exact expected route")
    if proposal.get("access_policy") != []:
        raise ResultsError("live proposal contains an access-policy change")
    if (
        data.proposal.get("gate_report", {}).get("verdict")
        != "PASS_PENDING_HUMAN_APPROVAL"
    ):
        raise ResultsError("live proposal did not remain pending human approval")
    if data.approval.get("decision") != "APPROVED":
        raise ResultsError("live repair has no explicit approval")
    if data.approval.get("approval_scope") != EXPECTED_ROUTE:
        raise ResultsError("approval scope differs from the proposed route")
    if data.approval.get("automatic_deployment_by_llm") is not False:
        raise ResultsError("evidence does not prove that automatic LLM deployment was off")
    actual_hash = hashlib.sha256(data.proposal_bytes).hexdigest()
    if data.approval.get("proposal_sha256") != actual_hash:
        raise ResultsError("approval checksum does not match the proposal evidence")
    if data.after.get("verdict") != "MATCHES_INTENT":
        raise ResultsError("post-repair runtime state does not match intent")
    if data.after.get("summary") != {"passed": 7, "failed": 0}:
        raise ResultsError("post-repair runtime evidence is not exactly 7/7 PASS")


def model_aggregates(data: SourceData) -> list[dict]:
    by_name = {
        row["model"]: row
        for row in data.evaluation["model_performance_live_only"]
    }
    return [by_name[name] for name in data.manifest["models"]]


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%".replace(".0%", "%")


def _metric_table(models: list[dict]) -> str:
    metrics = [
        ("Schema validity on first attempt", "schema_validity_first_attempt", "rate"),
        ("Final schema validity", "schema_validity_final", "rate"),
        ("Decision accuracy", "decision_accuracy", "rate"),
        ("Reason-code accuracy", "reason_code_accuracy", "rate"),
        ("Model-proposal safety", "model_policy_safety", "rate"),
        ("Whole-system safety", "system_policy_safety", "rate"),
        (
            "Completeness for valid cases",
            "completeness_rate_valid_cases",
            "rate",
        ),
        ("Mean extraneous changes", "extraneous_changes_mean", "number"),
        ("Pipeline-outcome accuracy", "pipeline_outcome_accuracy", "rate"),
        ("Cold first-run latency", "cold_first_run_latency_s", "seconds"),
        ("Median warm-run latency", "median_warm_latency_s", "seconds"),
    ]
    lines = [
        "| Metric | Qwen2.5-Coder 7B | Qwen3 4B |",
        "|---|---:|---:|",
    ]
    for label, key, kind in metrics:
        values = []
        for model in models:
            value = model[key]
            if kind == "rate":
                values.append(_pct(value))
            elif kind == "seconds":
                values.append(f"{value:.2f} s")
            else:
                values.append(f"{value:.3f}".rstrip("0").rstrip("."))
        lines.append(f"| {label} | {values[0]} | {values[1]} |")
    return "\n".join(lines)


def _case_sort_key(case_id: str) -> int:
    try:
        return int(case_id.removeprefix("T"))
    except ValueError:
        return sys.maxsize


def _observed(rows: list[dict], model: str, case_id: str) -> str:
    matches = [
        row for row in rows
        if row.get("model") == model and row.get("case") == case_id
    ]
    pairs = sorted(
        {
            f"{row.get('decision') or 'INVALID_SCHEMA'} / {row.get('outcome')}"
            for row in matches
        }
    )
    return "<br>".join(f"`{pair}`" for pair in pairs)


def _case_table(data: SourceData) -> str:
    case_by_id = {case["id"]: case for case in data.cases}
    rows = data.evaluation["per_run"]
    models = data.manifest["models"]
    lines = [
        "| Case | Category | Expected decision | Qwen2.5-Coder | Qwen3 |",
        "|---|---|---|---|---|",
    ]
    for case_id in sorted(data.manifest["case_ids"], key=_case_sort_key):
        case = case_by_id[case_id]
        lines.append(
            f"| {case_id} | `{case['category']}` | "
            f"`{case['expected_decision']}` | "
            f"{_observed(rows, models[0], case_id)} | "
            f"{_observed(rows, models[1], case_id)} |"
        )
    return "\n".join(lines)


def grouped_bar_svg(
    *,
    title: str,
    subtitle: str,
    categories: list[tuple[str, str]],
    models: list[dict],
    maximum: float,
    tick_step: float,
    multiplier: float,
    value_suffix: str,
) -> str:
    width = 1080
    left = 330
    right = 70
    top = 150
    bottom = 70
    row_height = 66
    height = top + bottom + row_height * len(categories)
    chart_width = width - left - right
    bar_height = 17

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" '
            'role="img" aria-labelledby="title description">'
        ),
        f"<title id=\"title\">{escape(title)}</title>",
        f"<desc id=\"description\">{escape(subtitle)}</desc>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        (
            f'<text x="40" y="44" font-family="Arial, sans-serif" '
            f'font-size="25" font-weight="700" fill="#111827">{escape(title)}</text>'
        ),
        (
            f'<text x="40" y="73" font-family="Arial, sans-serif" '
            f'font-size="14" fill="#4b5563">{escape(subtitle)}</text>'
        ),
    ]

    legend_x = 40
    for index, model in enumerate(models):
        x = legend_x + index * 250
        label = MODEL_LABELS.get(model["model"], model["model"])
        parts.extend(
            [
                f'<rect x="{x}" y="97" width="18" height="18" rx="3" '
                f'fill="{COLORS[index]}"/>',
                f'<text x="{x + 27}" y="111" font-family="Arial, sans-serif" '
                f'font-size="14" fill="#1f2937">{escape(label)}</text>',
            ]
        )

    tick = 0.0
    while tick <= maximum + 1e-9:
        x = left + chart_width * tick / maximum
        label = f"{tick:g}{value_suffix}"
        parts.extend(
            [
                f'<line x1="{x:.2f}" y1="{top - 10}" x2="{x:.2f}" '
                f'y2="{height - bottom + 8}" stroke="#e5e7eb" stroke-width="1"/>',
                f'<text x="{x:.2f}" y="{height - 30}" text-anchor="middle" '
                f'font-family="Arial, sans-serif" font-size="12" '
                f'fill="#6b7280">{escape(label)}</text>',
            ]
        )
        tick += tick_step

    for row_index, (label, key) in enumerate(categories):
        row_y = top + row_index * row_height
        parts.append(
            f'<text x="{left - 15}" y="{row_y + 27}" text-anchor="end" '
            f'font-family="Arial, sans-serif" font-size="14" '
            f'fill="#1f2937">{escape(label)}</text>'
        )
        for model_index, model in enumerate(models):
            value = float(model[key]) * multiplier
            bar_width = chart_width * value / maximum
            y = row_y + 7 + model_index * 25
            parts.extend(
                [
                    f'<rect x="{left}" y="{y}" width="{bar_width:.2f}" '
                    f'height="{bar_height}" rx="3" fill="{COLORS[model_index]}"/>',
                    f'<text x="{left + bar_width + 7:.2f}" y="{y + 13}" '
                    f'font-family="Arial, sans-serif" font-size="12" '
                    f'font-weight="700" fill="#374151">'
                    f'{value:.1f}{escape(value_suffix)}</text>',
                ]
            )

    parts.extend(
        [
            (
                f'<text x="{left + chart_width / 2:.2f}" y="{height - 8}" '
                'text-anchor="middle" font-family="Arial, sans-serif" '
                f'font-size="13" fill="#4b5563">'
                f'{"Percentage" if value_suffix == "%" else "Seconds"}</text>'
            ),
            "</svg>",
            "",
        ]
    )
    return "\n".join(parts)


def render_chapter(data: SourceData) -> str:
    models = model_aggregates(data)
    generated = data.manifest["generated"]
    proposal_latency = data.proposal["total_latency_s"]
    before = data.before["summary"]
    after = data.after["summary"]
    quality_figure = "figures/week5-v2/model-quality.svg"
    latency_figure = "figures/week5-v2/latency.svg"

    return f"""# 6. Experiments and Evaluation

## 6.1 Objective and experimental design

The evaluation examines whether a local large language model can correctly
interpret network intent and produce a structured proposal without being given
direct access to execute configuration commands. Model output is treated as
untrusted and must pass JSON Schema validation, deterministic topology and
policy checks, human approval, and validation of the actual runtime state.

The matrix contains 10 cases, 2 local models, and 3 repetitions for each
model–case combination, producing 60 live Ollama runs. The experiment manifest
was generated at `{generated}`. The runs used temperature 0, seed 42, an
8,192-token context window, a maximum of 1,024 output tokens, and one permitted
attempt. Mock data is excluded from the model-performance metrics.

## 6.2 Metrics

- **Schema validity:** whether the response is machine-readable and conforms to
  the defined structure.
- **Decision accuracy:** whether the model correctly selected `PROPOSE`,
  `REFUSE`, or `CLARIFY`.
- **Reason-code accuracy:** whether the explanation was classified with the
  expected reason code.
- **Model-proposal safety:** whether the proposal itself avoids conflicts with
  the intended state.
- **Whole-system safety:** whether any unsafe proposal reached an accepted
  outcome after deterministic checks.
- **Completeness and extraneous changes:** whether a valid proposal contains
  every required change and no unnecessary changes.
- **Latency:** the cold first run and the typical warm-run latency are reported
  separately.

## 6.3 Aggregate results

{_metric_table(models)}

![Comparison of model quality and safety]({quality_figure})

Figure 6.1 compares the principal quality and safety rates. Qwen2.5-Coder
performed better in schema validity, decision accuracy, reason-code accuracy,
and final pipeline outcome. Qwen3 produced safer raw proposals in this
benchmark, but it made more structural and classification errors. The most
important result is that whole-system safety reached 100% for both models even
though Qwen2.5-Coder's model-proposal safety was 90%. This difference shows that
the deterministic gate prevented the unsafe proposal from progressing.

![Comparison of model latency]({latency_figure})

Figure 6.2 shows that Qwen2.5-Coder had a slower cold first run (10.41 s versus
8.00 s) but a lower median warm-run latency (1.23 s versus 1.50 s). Latency
therefore depends not only on parameter count, but also on model loading,
quantization, runtime state, and the generated response.

## 6.4 Per-case results

{_case_table(data)}

All three repetitions for every model–case combination produced the same
decision, reason code, and final outcome. This demonstrates repeatability under
the selected deterministic settings, but it does not make the 30 runs per model
30 independent statistical observations. At the case level, decision accuracy
was 9/10 for Qwen2.5-Coder and 8/10 for Qwen3.

Case T9 is critical. Qwen2.5-Coder proposed a change that conflicted with the
`must_allow` policy, and the deterministic gate rejected it in all three
repetitions. Qwen3 produced an invalid schema, which was stopped at the first
validation layer. Together, these outcomes illustrate defence in depth:
different error classes are stopped at different control points.

## 6.5 Live guarded-repair demonstration

Following the model comparison, a live demonstration deliberately removed the
route `10.0.2.0/24 via 10.0.12.2` from `r1`.

1. The runtime validator detected the fault: `{before['passed']}` checks passed
   and `{before['failed']}` failed, producing `DOES_NOT_MATCH_INTENT`.
2. Qwen2.5-Coder proposed exactly the allow-listed route repair in
   {proposal_latency:.2f} s.
3. Schema validation and the deterministic gate produced
   `PASS_PENDING_HUMAN_APPROVAL`.
4. The engineer explicitly approved the exact scope, and the approval record
   was cryptographically bound to the proposal with SHA-256.
5. Only the hard-coded, allow-listed change was deployed; the LLM had no
   interface for direct execution.
6. After repair, all `{after['passed']}` of `{after['passed'] + after['failed']}`
   checks passed and the runtime state was `MATCHES_INTENT`.

## 6.6 Answers to the research questions

### RQ1 — To what extent does deterministic validation detect errors?

In the controlled benchmark, whole-system safety was 100% for both models. The
deterministic layer rejected the conflicting T9 proposal, while the runtime
validator detected the missing route before repair. These results support using
an LLM as an assistive layer, but not as an autonomous executor.

### RQ2 — Which classes of error are detected automatically?

Schema validation identifies structurally invalid responses. The
topology/policy gate detects unknown next hops, conflicting access rules, and
changes outside the permitted scope. Runtime checks detect missing routes,
broken reachability, and removed ACL policy. Ambiguous requests and semantic
choices among multiple otherwise valid solutions still require engineering
judgement.

### RQ3 — Can an LLM reduce effort without compromising correctness?

The models can prepare useful structured proposals and an exact repair, but
their output quality is not perfect: decision accuracy was 90% and 80%, while
completeness for valid cases was 66.7% for both models. Reduced manual effort is
therefore justified only within an architecture that includes schema
validation, a deterministic gate, exact-scope enforcement, human approval, and
post-deployment validation.

## 6.7 Limitations and threats to validity

- The benchmark contains only 10 cases from one controlled FRR topology and
  should not be generalized directly to all network vendors and environments.
- The three repetitions used deterministic settings and measure repeatability,
  not variance under stochastic generation.
- Latency depends on the local hardware, Ollama, model quantization, and cache
  state.
- The evaluation primarily measures classification, structured proposals, and
  the guarded control pipeline; it does not measure long-term stability in a
  production network.
- Human approval, scope restriction, and runtime validation are integral to the
  safety result and must not be removed.

## 6.8 Evaluation conclusion

Qwen2.5-Coder is the stronger primary model for this laboratory, but model
selection is not the principal research result. More importantly, the guarded
architecture maintained 100% whole-system safety and prevented invalid or
conflicting proposals from becoming automatic network changes.

## Reproducibility

This chapter and its figures are derived from the preserved JSON evidence. They
can be regenerated or verified offline with:

```bash
python3 experiments/generate_thesis_results.py
python3 experiments/generate_thesis_results.py --check
```
"""


def build_outputs(data: SourceData) -> dict[Path, str]:
    validate_sources(data)
    models = model_aggregates(data)
    quality = grouped_bar_svg(
        title="Model quality and safety",
        subtitle="60 live Ollama runs; a higher percentage is better.",
        categories=[
            ("Schema validity (first attempt)", "schema_validity_first_attempt"),
            ("Decision accuracy", "decision_accuracy"),
            ("Reason-code accuracy", "reason_code_accuracy"),
            ("Model-proposal safety", "model_policy_safety"),
            ("Whole-system safety", "system_policy_safety"),
            ("Completeness", "completeness_rate_valid_cases"),
            ("Pipeline-outcome accuracy", "pipeline_outcome_accuracy"),
        ],
        models=models,
        maximum=100,
        tick_step=20,
        multiplier=100,
        value_suffix="%",
    )
    latency = grouped_bar_svg(
        title="Local-model latency",
        subtitle="Cold first run and median warm-run latency.",
        categories=[
            ("Cold first run", "cold_first_run_latency_s"),
            ("Median warm run", "median_warm_latency_s"),
        ],
        models=models,
        maximum=12,
        tick_step=2,
        multiplier=1,
        value_suffix=" s",
    )
    return {
        DEFAULT_CHAPTER: render_chapter(data),
        DEFAULT_FIGURE_DIR / "model-quality.svg": quality,
        DEFAULT_FIGURE_DIR / "latency.svg": latency,
    }


def write_outputs(outputs: dict[Path, str], check: bool) -> None:
    stale = []
    for path, expected in outputs.items():
        if check:
            try:
                actual = path.read_text()
            except OSError:
                stale.append(path)
                continue
            if actual != expected:
                stale.append(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected)

    if stale:
        names = ", ".join(str(path.relative_to(REPO_ROOT)) for path in stale)
        raise ResultsError(f"generated outputs are missing or stale: {names}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate deterministic thesis results from Week 5 evidence"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that committed outputs match the preserved evidence",
    )
    args = parser.parse_args()

    try:
        outputs = build_outputs(load_sources())
        write_outputs(outputs, args.check)
    except ResultsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    action = "verified" if args.check else "generated"
    for path in outputs:
        print(f"{action}: {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
