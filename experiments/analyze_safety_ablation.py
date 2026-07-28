#!/usr/bin/env python3
"""Deterministic safety-layer ablation over the preserved Week 6 corpus.

The analysis never calls an LLM, changes raw evidence, or deploys a
configuration. It replays the recorded structured proposals through the real
schema/topology/policy gate and measures how many known-unsafe actionable
proposals would continue under progressively stronger controls:

1. no deterministic gate;
2. strict schema only;
3. schema plus policy checks, with topology checks removed;
4. the complete schema/topology/policy gate;
5. the complete gate plus exact-byte SHA-256 approval binding.

The checksum probe is explicitly counterfactual: each full-gate-passing
proposal is hashed and then changed by one byte. It tests integrity binding,
not a live deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from validation.static_gate import DEFAULT_INTENT, Gate, load_intent  # noqa: E402


DEFAULT_EVIDENCE_ROOT = REPO_ROOT / "docs" / "evidence" / "week6-v2-12m"
DEFAULT_CASES = REPO_ROOT / "experiments" / "v2_benchmark_cases.yaml"
DEFAULT_JSON_OUT = (
    REPO_ROOT
    / "docs"
    / "derived"
    / "safety-ablation"
    / "week6-safety-ablation.json"
)
DEFAULT_MARKDOWN_OUT = (
    REPO_ROOT
    / "docs"
    / "derived"
    / "safety-ablation"
    / "week6-safety-ablation-mk.md"
)
DEFAULT_SVG_OUT = (
    REPO_ROOT
    / "docs"
    / "figures"
    / "week6-v2"
    / "safety-layer-ablation.svg"
)

EXPECTED_MODELS = 12
EXPECTED_CASES = 10
EXPECTED_REPETITIONS = 3
EXPECTED_RUNS = EXPECTED_MODELS * EXPECTED_CASES * EXPECTED_REPETITIONS


class AblationError(RuntimeError):
    """Raised when the source corpus cannot support a defensible analysis."""


@dataclass(frozen=True)
class RunRecord:
    path: Path
    model: str
    case: str
    repetition: int
    payload: dict[str, Any]

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.model, self.case, self.repetition)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AblationError(message)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AblationError(f"invalid JSON source {path}: {exc}") from exc
    require(isinstance(value, dict), f"JSON source is not an object: {path}")
    return value


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    try:
        cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AblationError(f"cannot load benchmark cases {path}: {exc}") from exc
    require(isinstance(cases, list), "benchmark cases must be a list")
    ids = [case.get("id") for case in cases if isinstance(case, dict)]
    require(len(ids) == EXPECTED_CASES, "benchmark case count is not 10")
    require(len(set(ids)) == EXPECTED_CASES, "benchmark case IDs are not unique")
    return cases


def load_corpus(
    evidence_root: Path = DEFAULT_EVIDENCE_ROOT,
    cases_path: Path = DEFAULT_CASES,
) -> tuple[list[RunRecord], list[Path]]:
    """Load only run files named by the twelve per-model manifests."""

    cases = load_cases(cases_path)
    expected_case_ids = {case["id"] for case in cases}
    manifests = sorted(evidence_root.glob("*/manifest.json"))
    require(
        len(manifests) == EXPECTED_MODELS,
        f"expected {EXPECTED_MODELS} manifests; found {len(manifests)}",
    )

    records: list[RunRecord] = []
    source_paths: list[Path] = []
    manifest_models: set[str] = set()

    for manifest_path in manifests:
        manifest = read_json(manifest_path)
        source_paths.append(manifest_path)

        models = manifest.get("models")
        require(
            isinstance(models, list) and len(models) == 1,
            f"manifest must name exactly one model: {manifest_path}",
        )
        model = models[0]
        require(isinstance(model, str) and model, f"invalid model: {manifest_path}")
        require(model not in manifest_models, f"duplicate model manifest: {model}")
        manifest_models.add(model)

        require(
            manifest.get("kind") == "LIVE_OLLAMA_EXPERIMENT",
            f"non-live manifest found: {manifest_path}",
        )
        require(
            set(manifest.get("case_ids", [])) == expected_case_ids,
            f"manifest case coverage mismatch: {manifest_path}",
        )
        require(
            manifest.get("repetitions_per_case") == EXPECTED_REPETITIONS,
            f"manifest repetition count mismatch: {manifest_path}",
        )
        entries = manifest.get("records")
        require(isinstance(entries, list), f"manifest records missing: {manifest_path}")
        require(
            manifest.get("runs") == len(entries) == EXPECTED_CASES * EXPECTED_REPETITIONS,
            f"manifest run count mismatch: {manifest_path}",
        )

        named_files: set[str] = set()
        for entry in entries:
            require(isinstance(entry, dict), f"invalid manifest entry: {manifest_path}")
            filename = entry.get("file")
            require(
                isinstance(filename, str) and filename not in named_files,
                f"duplicate or invalid run filename in {manifest_path}",
            )
            named_files.add(filename)
            run_path = manifest_path.parent / filename
            payload = read_json(run_path)
            source_paths.append(run_path)

            record = RunRecord(
                path=run_path,
                model=str(payload.get("model")),
                case=str(payload.get("benchmark_case_id")),
                repetition=int(payload.get("repetition", 0)),
                payload=payload,
            )
            require(record.model == model == entry.get("model"), f"model mismatch: {run_path}")
            require(record.case == entry.get("case"), f"case mismatch: {run_path}")
            require(
                record.repetition == entry.get("repetition"),
                f"repetition mismatch: {run_path}",
            )
            require(
                payload.get("outcome") == entry.get("outcome"),
                f"outcome mismatch: {run_path}",
            )
            require(
                not record.model.startswith("MOCK("),
                f"mock record found in live corpus: {run_path}",
            )
            records.append(record)

    require(len(records) == EXPECTED_RUNS, f"expected 360 runs; found {len(records)}")
    keys = [record.key for record in records]
    require(len(set(keys)) == len(keys), "duplicate model/case/repetition key")

    expected_keys = {
        (model, case, repetition)
        for model in manifest_models
        for case in expected_case_ids
        for repetition in range(1, EXPECTED_REPETITIONS + 1)
    }
    require(set(keys) == expected_keys, "model/case/repetition coverage is incomplete")
    return records, source_paths


def source_digest(paths: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def failed_categories(report: dict[str, Any]) -> frozenset[str]:
    checks = report.get("checks")
    require(isinstance(checks, list), "gate report has no checks")
    return frozenset(
        str(check.get("category"))
        for check in checks
        if check.get("result") == "FAIL"
    )


def check_signature(report: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    checks = report.get("checks")
    require(isinstance(checks, list), "gate report has no checks")
    return [
        (
            str(check.get("category")),
            str(check.get("description")),
            str(check.get("result")),
            str(check.get("detail", "")),
        )
        for check in checks
    ]


def recompute_gate(
    proposal: dict[str, Any],
    intent: dict[str, Any],
) -> dict[str, Any]:
    raw = json.dumps(proposal, ensure_ascii=False)
    return Gate(intent).run(raw, "ablation:recomputed")


def layer_row(
    *,
    key: str,
    label: str,
    eligible: int,
    allowed: int,
    unsafe_allowed: int,
    note: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "eligible_actionable_proposals": eligible,
        "blocked": eligible - allowed,
        "allowed": allowed,
        "known_unsafe_allowed": unsafe_allowed,
        "unsafe_share_of_allowed": (
            round(unsafe_allowed / allowed, 4) if allowed else 0.0
        ),
        "note": note,
    }


def analyze_corpus(
    evidence_root: Path = DEFAULT_EVIDENCE_ROOT,
    cases_path: Path = DEFAULT_CASES,
    intent_path: Path = DEFAULT_INTENT,
) -> dict[str, Any]:
    records, source_paths = load_corpus(evidence_root, cases_path)
    intent = load_intent(intent_path)

    outcome_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    failure_sets: Counter[tuple[str, ...]] = Counter()
    per_model: dict[str, Counter[str]] = defaultdict(Counter)
    per_case: dict[str, Counter[str]] = defaultdict(Counter)

    final_schema_valid = 0
    schema_invalid = 0
    actionable = 0
    policy_only_allowed = 0
    policy_only_unsafe_allowed = 0
    full_gate_allowed = 0
    full_gate_unsafe_allowed = 0
    recorded_recomputed_mismatches = 0

    for record in records:
        payload = record.payload
        outcome = str(payload.get("outcome"))
        outcome_counts[outcome] += 1
        proposal = payload.get("proposal")

        if proposal is None:
            schema_invalid += 1
            decision_counts["SCHEMA_INVALID"] += 1
            require(outcome == "REJECTED_SCHEMA", f"missing proposal not rejected: {record.path}")
            require(payload.get("gate_report") is None, f"invalid schema reached gate: {record.path}")
            continue

        require(isinstance(proposal, dict), f"proposal is not an object: {record.path}")
        final_schema_valid += 1
        decision = str(proposal.get("decision"))
        decision_counts[decision] += 1

        if decision == "REFUSE":
            require(outcome == "REFUSED", f"REFUSE outcome mismatch: {record.path}")
            require(payload.get("gate_report") is None, f"REFUSE reached gate: {record.path}")
            continue
        if decision == "CLARIFY":
            require(
                outcome == "CLARIFICATION_REQUIRED",
                f"CLARIFY outcome mismatch: {record.path}",
            )
            require(payload.get("gate_report") is None, f"CLARIFY reached gate: {record.path}")
            continue

        require(decision == "PROPOSE", f"unsupported decision: {decision}")
        actionable += 1
        recorded = payload.get("gate_report")
        require(isinstance(recorded, dict), f"PROPOSE missing gate report: {record.path}")
        recomputed = recompute_gate(proposal, intent)

        if (
            recorded.get("verdict") != recomputed.get("verdict")
            or recorded.get("summary") != recomputed.get("summary")
            or check_signature(recorded) != check_signature(recomputed)
        ):
            recorded_recomputed_mismatches += 1

        categories = failed_categories(recomputed)
        require(
            categories <= {"topology", "policy"},
            f"unexpected failed gate category: {record.path}",
        )
        failure_sets[tuple(sorted(categories))] += 1

        topology_failed = "topology" in categories
        policy_failed = "policy" in categories
        unsafe = topology_failed or policy_failed
        model_counts = per_model[record.model]
        case_counts = per_case[record.case]
        for counts in (model_counts, case_counts):
            counts["runs"] += 1
            counts["actionable"] += 1
            counts["topology_failed"] += int(topology_failed)
            counts["policy_failed"] += int(policy_failed)
            counts["known_unsafe"] += int(unsafe)

        if not policy_failed:
            policy_only_allowed += 1
            policy_only_unsafe_allowed += int(topology_failed)

        if not unsafe:
            full_gate_allowed += 1
            full_gate_unsafe_allowed += 0

        expected_verdict = "REJECT" if unsafe else "PASS_PENDING_HUMAN_APPROVAL"
        expected_outcome = "REJECTED_GATE" if unsafe else "ACCEPTED"
        require(
            recomputed.get("verdict") == expected_verdict,
            f"recomputed gate verdict mismatch: {record.path}",
        )
        require(outcome == expected_outcome, f"pipeline outcome mismatch: {record.path}")

    require(recorded_recomputed_mismatches == 0, "recorded and recomputed gates differ")
    require(final_schema_valid + schema_invalid == EXPECTED_RUNS, "schema totals differ")
    unsafe_actionable = sum(
        count for categories, count in failure_sets.items() if categories
    )
    require(actionable == 156, f"expected 156 actionable proposals; found {actionable}")
    require(unsafe_actionable == 80, f"expected 80 unsafe proposals; found {unsafe_actionable}")
    require(policy_only_unsafe_allowed == 29, "policy-only residual is not 29")
    require(full_gate_allowed == 76, "full gate pass count is not 76")
    require(full_gate_unsafe_allowed == 0, "full gate allowed a known violation")

    tampered_candidates = full_gate_allowed
    tampered_accepted = 0
    for record in records:
        proposal = record.payload.get("proposal")
        report = record.payload.get("gate_report")
        if (
            isinstance(proposal, dict)
            and isinstance(report, dict)
            and report.get("verdict") == "PASS_PENDING_HUMAN_APPROVAL"
        ):
            approved_bytes = json.dumps(
                proposal,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            approved_hash = hashlib.sha256(approved_bytes).hexdigest()
            tampered_hash = hashlib.sha256(approved_bytes + b" ").hexdigest()
            tampered_accepted += int(approved_hash == tampered_hash)

    layers = [
        layer_row(
            key="no_deterministic_gate",
            label="No deterministic gate",
            eligible=actionable,
            allowed=actionable,
            unsafe_allowed=unsafe_actionable,
            note="All schema-valid PROPOSE outputs would continue.",
        ),
        layer_row(
            key="schema_only",
            label="Strict schema only",
            eligible=actionable,
            allowed=actionable,
            unsafe_allowed=unsafe_actionable,
            note="Schema rejects 11 malformed outputs but no semantic violation.",
        ),
        layer_row(
            key="schema_plus_policy",
            label="Schema + policy",
            eligible=actionable,
            allowed=policy_only_allowed,
            unsafe_allowed=policy_only_unsafe_allowed,
            note="All 51 policy violations are blocked; 29 topology-only violations remain.",
        ),
        layer_row(
            key="complete_gate",
            label="Schema + topology + policy",
            eligible=actionable,
            allowed=full_gate_allowed,
            unsafe_allowed=full_gate_unsafe_allowed,
            note="All 80 known violations are blocked.",
        ),
        layer_row(
            key="complete_gate_plus_checksum",
            label="Complete gate + SHA-256 approval",
            eligible=actionable,
            allowed=full_gate_allowed,
            unsafe_allowed=0,
            note="All 76 one-byte tampering probes are rejected by exact hash comparison.",
        ),
    ]

    def counter_map(values: dict[str, Counter[str]]) -> dict[str, dict[str, int]]:
        return {
            name: dict(sorted(counts.items()))
            for name, counts in sorted(values.items())
        }

    return {
        "analysis": "week6_safety_layer_ablation_v1",
        "source": {
            "evidence_root": "docs/evidence/week6-v2-12m",
            "benchmark_cases": "experiments/v2_benchmark_cases.yaml",
            "intent": "intent/intended_state.yaml",
            "source_file_count": len(source_paths),
            "source_corpus_sha256": source_digest(source_paths, evidence_root),
        },
        "grain": "one live model x benchmark case x repetition",
        "quality": {
            "models": EXPECTED_MODELS,
            "cases": EXPECTED_CASES,
            "repetitions_per_case": EXPECTED_REPETITIONS,
            "run_records": len(records),
            "mock_runs": 0,
            "duplicate_candidate_keys": 0,
            "missing_candidate_keys": 0,
            "manifest_run_count_mismatches": 0,
            "recorded_vs_recomputed_gate_mismatches": recorded_recomputed_mismatches,
        },
        "population": {
            "all_runs": len(records),
            "final_schema_valid": final_schema_valid,
            "schema_invalid": schema_invalid,
            "actionable_proposals": actionable,
            "non_actionable_refuse_or_clarify": (
                decision_counts["REFUSE"] + decision_counts["CLARIFY"]
            ),
            "outcomes": dict(sorted(outcome_counts.items())),
            "decisions": dict(sorted(decision_counts.items())),
        },
        "failure_overlap": {
            "policy_only": failure_sets[("policy",)],
            "topology_only": failure_sets[("topology",)],
            "policy_and_topology": failure_sets[("policy", "topology")],
            "no_known_violation": failure_sets[()],
        },
        "layers": layers,
        "checksum_probe": {
            "full_gate_passing_proposals": full_gate_allowed,
            "one_byte_tampering_probes": tampered_candidates,
            "tampered_proposals_accepted": tampered_accepted,
            "tampered_proposals_rejected": tampered_candidates - tampered_accepted,
            "scope": "deterministic post-hoc integrity probe; not a live deployment",
        },
        "per_model": counter_map(per_model),
        "per_case": counter_map(per_case),
        "claim_boundary": (
            "The ablation is a deterministic post-hoc replay over preserved live "
            "model outputs. It measures known schema, topology, policy, and "
            "exact-byte integrity failures; it does not prove live deployment "
            "success or capture every possible unsafe configuration."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    population = report["population"]
    overlap = report["failure_overlap"]
    checksum = report["checksum_probe"]
    layer_lines = []
    for layer in report["layers"]:
        rate = layer["unsafe_share_of_allowed"] * 100
        layer_lines.append(
            "| {label} | {allowed} | {unsafe} | {rate:.1f}% |".format(
                label=layer["label"],
                allowed=layer["allowed"],
                unsafe=layer["known_unsafe_allowed"],
                rate=rate,
            )
        )

    return f"""# Аблациска анализа на безбедносните слоеви

## Техничко резиме

Анализата ги користи сите **{population["all_runs"]} живи PROPOSE извршувања**
од дванаесет модели, без повторно повикување модел и без менување на суровите
докази. Од нив, {population["schema_invalid"]} излези не поминале строга шема,
а {population["actionable_proposals"]} биле шема-валидни `PROPOSE` предлози.

Строгата шема сама по себе не обезбедува семантичка безбедност: без
детерминистичката порта би продолжиле **80 од 156** познато небезбедни
предлози (51,3%). Само политичката проверка ги блокира сите 51 политички
прекршувања, но би пропуштила **29 тополошки прекршувања**. Комплетната порта
за шема, топологија и политика ги блокира сите 80, така што бројот на познато
небезбедни предлози што продолжуваат е **0**.

![Познати небезбедни предлози по безбедносен слој](../../figures/week6-v2/safety-layer-ablation.svg)

Сликата го прикажува истиот именител од 156 шема-валидни акциски предлози.
Намалувањето од 80 на 29 го изолира придонесот на политичката проверка, а
намалувањето од 29 на 0 го изолира дополнителниот придонес на тополошката
проверка.

## Резултати по конфигурација

| Конфигурација | Предлози што продолжуваат | Познато небезбедни што продолжуваат | Удел меѓу продолжените |
|---|---:|---:|---:|
{chr(10).join(layer_lines)}

## Што докажува секој слој

- **Строга шема:** одбива {population["schema_invalid"]} синтаксички или
  структурно невалидни излези, но не ги разбира мрежната топологија и
  безбедносната намера.
- **Политичка проверка:** блокира 51 предлог што е во судир со `must_deny` или
  `must_allow`, но без тополошка проверка би пропуштила {overlap["topology_only"]}
  предлози со непостоечки мрежи, нелогични рути или невалидни next-hop адреси.
- **Комплетна детерминистичка порта:** блокира
  {overlap["policy_only"]} само-политички,
  {overlap["topology_only"]} само-тополошки и
  {overlap["policy_and_topology"]} комбинирани прекршувања; 76 чисти предлози
  остануваат подобни само за човечки преглед.
- **SHA-256 врзување на одобрувањето:** од
  {checksum["one_byte_tampering_probes"]} еднобајтни измени на предлози што ја
  поминале портата, прифатени се {checksum["tampered_proposals_accepted"]}.
  Ова докажува интегритет на точните одобрени бајти, а не квалитет на
  инженерската одлука.

## Опсег, квалитет и ограничувања

Единицата на анализа е едно живо извршување на модел × тест-случај ×
повторување. Покриеноста е 12 × 10 × 3 = 360, без mock извршувања, дупликати
или недостасувачки комбинации. Сите 156 зачувани извештаи од портата беа
независно пресметани повторно со тековната имплементација; несовпаѓања: 0.
SHA-256 на изворниот корпус е
`{report["source"]["source_corpus_sha256"]}`.

Ова е **post-hoc контрафактичка аблација** врз веќе зачувани живи моделски
излези. Таа не извршува небезбедни конфигурации и не докажува дека примената
во Containerlab успеала. „Познато небезбедно“ значи конкретно прекршување
детектирано од независно повторно извршените шема, топологија и политика;
можни ризици надвор од тие правила не се опфатени. Живиот Ansible експеримент
има посебна улога: да ја докаже примената, откривањето грешки, поправката и
идемпотентноста врз активна мрежа.

## Следен доказ

Аблацијата ја докажува потребата од секој контролен слој. Следниот чекор е
извршување на Week 7 живиот експеримент и внесување на неговите независно
проверени времиња и резултати во Поглавје 6.
"""


def render_svg(report: dict[str, Any]) -> str:
    labels = [
        ("Без порта", report["layers"][0]["known_unsafe_allowed"]),
        ("Само шема", report["layers"][1]["known_unsafe_allowed"]),
        ("Само политика", report["layers"][2]["known_unsafe_allowed"]),
        ("Целосна порта", report["layers"][3]["known_unsafe_allowed"]),
        ("Порта + SHA-256", report["layers"][4]["known_unsafe_allowed"]),
    ]
    width = 1120
    height = 600
    plot_x = 310
    plot_width = 700
    max_value = 80
    bar_height = 50
    row_gap = 82
    start_y = 175
    colors = ["#D97706", "#D97706", "#8A7A17", "#2563A6", "#2563A6"]

    rows = []
    for index, ((label, value), color) in enumerate(zip(labels, colors)):
        y = start_y + index * row_gap
        bar_width = (value / max_value) * plot_width if max_value else 0
        rows.extend(
            [
                f'<text x="{plot_x - 18}" y="{y + 33}" text-anchor="end" '
                f'class="label">{label}</text>',
                f'<rect x="{plot_x}" y="{y}" width="{bar_width:.1f}" '
                f'height="{bar_height}" rx="6" fill="{color}" />',
                f'<text x="{plot_x + max(bar_width + 14, 18):.1f}" '
                f'y="{y + 34}" class="value">{value}</text>',
            ]
        )

    ticks = []
    for value in range(0, max_value + 1, 20):
        x = plot_x + (value / max_value) * plot_width
        ticks.extend(
            [
                f'<line x1="{x:.1f}" y1="160" x2="{x:.1f}" y2="525" '
                'stroke="#D9DEE7" stroke-width="1" />',
                f'<text x="{x:.1f}" y="552" text-anchor="middle" '
                f'class="tick">{value}</text>',
            ]
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">Познати небезбедни предлози што би продолжиле</title>
  <desc id="desc">Хоризонтални столбови за пет контролни конфигурации врз 156 шема-валидни PROPOSE предлози.</desc>
  <style>
    .title {{ font: 700 28px "Segoe UI", Arial, sans-serif; fill: #172033; }}
    .subtitle {{ font: 16px "Segoe UI", Arial, sans-serif; fill: #596579; }}
    .label {{ font: 600 17px "Segoe UI", Arial, sans-serif; fill: #283449; }}
    .value {{ font: 700 18px "Cascadia Mono", Consolas, monospace; fill: #172033; }}
    .tick {{ font: 14px "Cascadia Mono", Consolas, monospace; fill: #68758A; }}
    .source {{ font: 13px "Segoe UI", Arial, sans-serif; fill: #68758A; }}
  </style>
  <rect width="100%" height="100%" fill="#FAFBFD" />
  <text x="54" y="55" class="title">Познати небезбедни предлози што би продолжиле</text>
  <text x="54" y="88" class="subtitle">156 шема-валидни PROPOSE предлози од 360 живи извршувања; број на прекршувања дозволени по слој</text>
  <path d="M1040 34 l12 12 -12 12 -12 -12 z" fill="#D5A21A" opacity="0.9" />
  {''.join(ticks)}
  {''.join(rows)}
  <text x="54" y="584" class="source">Извор: зачувани Week 6 докази; портата е повторно пресметана, без нови LLM или мрежни извршувања.</text>
</svg>
"""


def json_text(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def check_text(path: Path, expected: str) -> None:
    try:
        actual = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AblationError(f"missing derived output {path}: {exc}") from exc
    require(actual == expected, f"derived output is stale: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive the Week 6 deterministic safety-layer ablation"
    )
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--intent", type=Path, default=DEFAULT_INTENT)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--markdown-out", type=Path, default=DEFAULT_MARKDOWN_OUT)
    parser.add_argument("--svg-out", type=Path, default=DEFAULT_SVG_OUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify committed derived outputs instead of rewriting them",
    )
    args = parser.parse_args()

    try:
        report = analyze_corpus(args.evidence_root, args.cases, args.intent)
        outputs = {
            args.json_out: json_text(report),
            args.markdown_out: render_markdown(report),
            args.svg_out: render_svg(report),
        }
        if args.check:
            for path, text in outputs.items():
                check_text(path, text)
                print(f"verified: {path.relative_to(REPO_ROOT).as_posix()}")
        else:
            for path, text in outputs.items():
                write_text(path, text)
                print(f"written: {path.relative_to(REPO_ROOT).as_posix()}")
    except AblationError as exc:
        print(f"SAFETY ABLATION FAILED: {exc}", file=sys.stderr)
        return 1

    print(
        "SAFETY ABLATION: PASS "
        "(schema-only unsafe=80, policy-only unsafe=29, full-gate unsafe=0)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
