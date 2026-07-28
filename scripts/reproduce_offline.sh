#!/usr/bin/env bash
#
# Reproduce the thesis verification checks without Docker, Containerlab,
# Ollama, network access, or changes to preserved evidence.
#
# Normal use (after this file is committed):
#   bash scripts/reproduce_offline.sh
#
# First verification immediately after applying the patch:
#   bash scripts/reproduce_offline.sh --allow-dirty
#
set -Eeuo pipefail
IFS=$'\n\t'

EXPECTED_BASE_COMMIT="${REPRO_BASE_COMMIT:-cad3e2e}"
EXPECTED_TESTS=120
EXPECTED_CHECKSUM_FILES=39
EXPECTED_CHECKSUM_ENTRIES=444
EXPECTED_EXPLAIN_PILOT_RUNS=12
EXPECTED_EXPLAIN_RUNS=288
EXPECTED_PROPOSE_RUNS=360
EXPECTED_MODELS=12

ALLOW_DIRTY=0
SKIP_GIT=0
SKIP_TESTS=0
REQUIRE_MODEL_DIGESTS=0

usage() {
    cat <<'EOF'
Usage: bash scripts/reproduce_offline.sh [OPTIONS]

Read-only offline verification for the thesis repository.

Options:
  --allow-dirty            Report a dirty worktree instead of failing.
                           Use this only for the first run after applying
                           the patch; committed reproductions should be clean.
  --require-model-digests  Fail if no recorded model-digest file is present.
  --skip-git               Skip Git commit/cleanliness checks (archive
                           diagnostics only; not a full reproduction).
  --skip-tests             Skip pytest (diagnostics only; not a full
                           reproduction).
  -h, --help               Show this help.
EOF
}

while (($#)); do
    case "$1" in
        --allow-dirty)
            ALLOW_DIRTY=1
            ;;
        --require-model-digests)
            REQUIRE_MODEL_DIGESTS=1
            ;;
        --skip-git)
            SKIP_GIT=1
            ;;
        --skip-tests)
            SKIP_TESTS=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'ERROR: unknown option: %s\n\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

step() {
    printf '\n[%s] %s\n' "$1" "$2"
}

pass() {
    printf 'PASS: %s\n' "$1"
}

warn() {
    printf 'WARN: %s\n' "$1" >&2
}

die() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

evidence_tree_digest() {
    python3 - "$REPO_ROOT/docs/evidence" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
digest = hashlib.sha256()
files = (path for path in root.rglob("*") if path.is_file())
for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
    relative = path.relative_to(root).as_posix().encode("utf-8")
    digest.update(relative)
    digest.update(b"\0")
    digest.update(hashlib.sha256(path.read_bytes()).digest())
print(digest.hexdigest())
PY
}

require_command python3
require_command sha256sum
if ((SKIP_GIT == 0)); then
    require_command git
fi

step "1/8" "Repository identity and worktree state"

git_state_before=""
if ((SKIP_GIT == 1)); then
    warn "Git checks skipped; this run is diagnostic, not a full reproduction."
else
    actual_root_raw="$(git rev-parse --show-toplevel 2>/dev/null)" ||
        die "the script is not inside a Git repository"
    actual_root="$(cd -- "$actual_root_raw" 2>/dev/null && pwd -P)" ||
        die "cannot resolve the Git repository root: $actual_root_raw"
    [[ "$actual_root" == "$REPO_ROOT" ]] ||
        die "script root and Git root differ: $REPO_ROOT != $actual_root"

    git cat-file -e "${EXPECTED_BASE_COMMIT}^{commit}" 2>/dev/null ||
        die "required Week 6 base commit is unavailable: $EXPECTED_BASE_COMMIT"
    git merge-base --is-ancestor "$EXPECTED_BASE_COMMIT" HEAD ||
        die "HEAD does not contain the verified Week 6 checkpoint $EXPECTED_BASE_COMMIT"

    printf 'Git version: %s\n' "$(git --version)"
    printf 'Branch: %s\n' "$(git branch --show-current)"
    printf 'HEAD: %s\n' "$(git rev-parse --short=12 HEAD)"
    printf 'Verified base: %s\n' "$EXPECTED_BASE_COMMIT"

    git_state_before="$(git status --porcelain=v1 --untracked-files=all)"
    if [[ -n "$git_state_before" ]]; then
        if ((ALLOW_DIRTY == 0)); then
            printf '%s\n' "$git_state_before" >&2
            die "worktree is not clean; commit or stash changes, or use --allow-dirty for the first patch check"
        fi
        warn "Dirty worktree allowed for this pre-commit verification:"
        printf '%s\n' "$git_state_before"
    else
        pass "worktree is clean"
    fi
fi

step "2/8" "Python and pinned dependency versions"

python3 --version
SKIP_PYTEST_DEPENDENCY="$SKIP_TESTS" python3 - <<'PY'
import importlib.metadata
import os
from pathlib import Path

requirements = Path("requirements.txt")
if not requirements.is_file():
    raise SystemExit("ERROR: requirements.txt is missing")

skip_pytest = os.environ.get("SKIP_PYTEST_DEPENDENCY") == "1"
checked = 0
for raw_line in requirements.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    if "==" not in line:
        raise SystemExit(f"ERROR: dependency is not exactly pinned: {line}")
    name, expected = line.split("==", 1)
    if skip_pytest and name.lower() == "pytest":
        print(f"WARN: skipped dependency check for {name}=={expected}")
        continue
    try:
        actual = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        raise SystemExit(f"ERROR: required dependency is not installed: {name}=={expected}")
    if actual != expected:
        raise SystemExit(
            f"ERROR: dependency version mismatch for {name}: "
            f"expected {expected}, found {actual}"
        )
    print(f"{name}=={actual}")
    checked += 1

if checked < 2:
    raise SystemExit("ERROR: fewer than two runtime dependencies were verified")
PY
pass "installed runtime dependencies match requirements.txt"

evidence_digest_before="$(evidence_tree_digest)"

step "3/8" "Recorded evidence checksums"

checksum_files=0
checksum_entries=0
while IFS= read -r -d '' checksum_file; do
    first_name="$(
        awk 'NF >= 2 {
            sub(/^[[:xdigit:]]{64}[[:space:]]+\*?/, "")
            print
            exit
        }' "$checksum_file"
    )"
    [[ -n "$first_name" ]] ||
        die "checksum file contains no entries: $checksum_file"

    if [[ -f "$REPO_ROOT/$first_name" ]]; then
        (
            cd "$REPO_ROOT"
            sha256sum --quiet -c "$checksum_file"
        ) || die "checksum verification failed: $checksum_file"
    else
        (
            cd "$(dirname "$checksum_file")"
            sha256sum --quiet -c "$(basename "$checksum_file")"
        ) || die "checksum verification failed: $checksum_file"
    fi

    entries="$(
        awk 'NF >= 2 && $1 ~ /^[[:xdigit:]]{64}$/ {count++}
             END {print count + 0}' "$checksum_file"
    )"
    checksum_files=$((checksum_files + 1))
    checksum_entries=$((checksum_entries + entries))
done < <(
    find "$REPO_ROOT/docs/evidence" -type f -name SHA256SUMS.txt -print0 |
        sort -z
)

[[ "$checksum_files" -eq "$EXPECTED_CHECKSUM_FILES" ]] ||
    die "expected $EXPECTED_CHECKSUM_FILES checksum files, found $checksum_files"
[[ "$checksum_entries" -eq "$EXPECTED_CHECKSUM_ENTRIES" ]] ||
    die "expected $EXPECTED_CHECKSUM_ENTRIES checksum entries, found $checksum_entries"
pass "$checksum_entries artifacts verified across $checksum_files checksum files"

step "4/8" "Week 6 corpus, manifests, parameters, and safety metrics"

EXPECTED_EXPLAIN_PILOT_RUNS="$EXPECTED_EXPLAIN_PILOT_RUNS" \
EXPECTED_EXPLAIN_RUNS="$EXPECTED_EXPLAIN_RUNS" \
EXPECTED_PROPOSE_RUNS="$EXPECTED_PROPOSE_RUNS" \
EXPECTED_MODELS="$EXPECTED_MODELS" \
python3 - <<'PY'
import json
import math
import os
from pathlib import Path

root = Path("docs/evidence")
pilot_root = root / "week6-explain"
explain_root = root / "week6-explain-13m"
propose_root = root / "week6-v2-12m"

expected_pilot = int(os.environ["EXPECTED_EXPLAIN_PILOT_RUNS"])
expected_explain = int(os.environ["EXPECTED_EXPLAIN_RUNS"])
expected_propose = int(os.environ["EXPECTED_PROPOSE_RUNS"])
expected_models = int(os.environ["EXPECTED_MODELS"])
generation_options = {
    "temperature": 0,
    "seed": 42,
    "num_ctx": 8192,
    "num_predict": 1024,
}

def load(path: Path):
    if not path.is_file():
        raise SystemExit(f"ERROR: required evidence file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ERROR: cannot read JSON evidence {path}: {exc}")

def require(condition, message):
    if not condition:
        raise SystemExit(f"ERROR: {message}")

pilot_runs = sorted(pilot_root.glob("EXPLAIN_*_run*.json"))
explain_runs = sorted(explain_root.glob("*/*/EXPLAIN_*_run*.json"))
propose_runs = sorted(propose_root.glob("*/T*_run*.json"))
require(len(pilot_runs) == expected_pilot,
        f"expected {expected_pilot} EXPLAIN pilot runs, found {len(pilot_runs)}")
require(len(explain_runs) == expected_explain,
        f"expected {expected_explain} comparative EXPLAIN runs, found {len(explain_runs)}")
require(len(propose_runs) == expected_propose,
        f"expected {expected_propose} comparative PROPOSE runs, found {len(propose_runs)}")

explain_manifests = sorted(explain_root.glob("*/*/manifest.json"))
propose_manifests = sorted(propose_root.glob("*/manifest.json"))
require(len(explain_manifests) == expected_models * 3,
        f"expected {expected_models * 3} EXPLAIN manifests, found {len(explain_manifests)}")
require(len(propose_manifests) == expected_models,
        f"expected {expected_models} PROPOSE manifests, found {len(propose_manifests)}")

explain_models = set()
for path in explain_manifests:
    manifest = load(path)
    require(manifest.get("task") == "EXPLAIN", f"wrong task in {path}")
    require(manifest.get("mock") is False, f"mock EXPLAIN manifest found: {path}")
    require(manifest.get("reps") == 3, f"wrong repetition count in {path}")
    require(manifest.get("generation_options") == generation_options,
            f"generation options changed in {path}")
    models = manifest.get("models")
    require(isinstance(models, list) and len(models) == 1,
            f"expected exactly one model in {path}")
    explain_models.add(models[0])

propose_models = set()
for path in propose_manifests:
    manifest = load(path)
    require(manifest.get("kind") == "LIVE_OLLAMA_EXPERIMENT",
            f"non-live PROPOSE manifest found: {path}")
    require(manifest.get("runs") == 30, f"wrong run count in {path}")
    require(manifest.get("repetitions_per_case") == 3,
            f"wrong repetition count in {path}")
    require(manifest.get("retries") == 3, f"wrong retry setting in {path}")
    require(manifest.get("case_ids") == [f"T{i}" for i in range(1, 11)],
            f"benchmark cases changed in {path}")
    models = manifest.get("models")
    require(isinstance(models, list) and len(models) == 1,
            f"expected exactly one model in {path}")
    propose_models.add(models[0])

require(len(explain_models) == expected_models,
        f"expected {expected_models} EXPLAIN models, found {len(explain_models)}")
require(propose_models == explain_models,
        "EXPLAIN and PROPOSE model rosters do not match")

for path in explain_runs + propose_runs:
    record = load(path)
    require(record.get("mock") is not True, f"mock record entered live corpus: {path}")
    require(record.get("generation_options") == generation_options,
            f"generation options changed in {path}")

v2 = load(propose_root / "v2-12m-evaluation.json")
require(v2.get("runs_total") == expected_propose,
        "PROPOSE evaluation run total is not 360")
require(v2.get("live_runs") == expected_propose,
        "PROPOSE live run total is not 360")
require(v2.get("mock_runs") == 0, "mock runs entered PROPOSE evaluation")
require(v2.get("unmatched_evidence") == [],
        "PROPOSE evaluation contains unmatched evidence")
performance = v2.get("model_performance_live_only")
require(isinstance(performance, list) and len(performance) == expected_models,
        "PROPOSE evaluation does not contain 12 model summaries")
require(all(math.isclose(row.get("system_policy_safety", -1), 1.0)
            for row in performance),
        "system_policy_safety is not 1.000 for every model")

explain_evaluations = [
    load(explain_root / "small-evaluation.json"),
    load(explain_root / "l-clean-evaluation.json"),
    load(explain_root / "l-faulty-evaluation.json"),
]
require(sum(len(item.get("per_run", [])) for item in explain_evaluations)
        == expected_explain,
        "comparative EXPLAIN evaluation does not contain 288 runs")
require(all(len(item.get("aggregate", {})) == expected_models
            for item in explain_evaluations),
        "an EXPLAIN evaluation does not contain all 12 models")

deep = load(propose_root / "v2-deep-analysis.json")
require(deep.get("live_runs") == expected_propose,
        "deep analysis does not report 360 live runs")
require(deep.get("models") == expected_models,
        "deep analysis does not report 12 models")
matrix = deep.get("gate_confusion_matrix", {})
require(matrix.get("true_positives_violation_rejected") == 51,
        "expected 51 policy violations rejected by the gate")
require(matrix.get("false_negatives_violation_allowed") == 0,
        "the gate allowed a known policy violation")
require(math.isclose(matrix.get("recall", -1), 1.0),
        "gate safety recall is not 1.000")

cross = load(root / "cross-task-analysis.json")
require(cross.get("models_compared") == expected_models,
        "cross-task analysis does not compare 12 models")
require(cross.get("system_safety_invariant_holds") is True,
        "cross-task system safety invariant does not hold")
require(cross.get("models_violating_system_safety") == [],
        "cross-task analysis lists models violating system safety")

print(f"Models: {expected_models}")
for model in sorted(propose_models):
    print(f"  - {model}")
print(f"EXPLAIN pilot runs: {len(pilot_runs)}")
print(f"EXPLAIN comparative runs: {len(explain_runs)}")
print(f"PROPOSE comparative runs: {len(propose_runs)}")
print("Mock comparative runs: 0")
print("Gate violations rejected/allowed: 51/0")
print("System policy safety: 1.000 for all 12 models")
print("Generation options: temperature=0, seed=42, num_ctx=8192, num_predict=1024")
print("PROPOSE manifest retries: 3")
PY
pass "Week 6 corpus and headline safety claims match the preserved results"

step "5/8" "Recorded model identities and optional digests"

digest_file=""
for candidate in \
    docs/evidence/week6-v2-12m/model-digests.txt \
    docs/evidence/week6-explain-13m/model-digests.txt \
    docs/evidence/model-digests.txt
do
    if [[ -s "$candidate" ]]; then
        digest_file="$candidate"
        break
    fi
done

if [[ -n "$digest_file" ]]; then
    printf 'Model digest record: %s\n' "$digest_file"
    pass "recorded model digest file is present"
elif ((REQUIRE_MODEL_DIGESTS == 1)); then
    die "model digests were not recorded; add a versioned digest file before using --require-model-digests"
else
    warn "Model names are verified from manifests, but model digests were not recorded in the campaign."
fi

step "6/8" "Deterministic derived Chapter 6 outputs"

python3 experiments/generate_thesis_results.py --check
pass "Chapter 6 and Week 5 figures match their preserved inputs"

step "7/8" "Offline test suite"

if ((SKIP_TESTS == 1)); then
    warn "pytest skipped; this run is diagnostic, not a full reproduction."
else
    test_output="$(python3 -m pytest -q 2>&1)" || {
        printf '%s\n' "$test_output" >&2
        die "offline test suite failed"
    }
    printf '%s\n' "$test_output"
    if ! grep -Eq "(^|[[:space:]])${EXPECTED_TESTS} passed([,[:space:]]|$)" \
        <<<"$test_output"; then
        die "expected exactly $EXPECTED_TESTS passing tests"
    fi
    pass "$EXPECTED_TESTS offline tests passed"
fi

step "8/8" "Read-only integrity and final state"

evidence_digest_after="$(evidence_tree_digest)"
[[ "$evidence_digest_after" == "$evidence_digest_before" ]] ||
    die "docs/evidence changed during offline reproduction"
printf 'Evidence tree SHA-256: %s\n' "$evidence_digest_after"

if ((SKIP_GIT == 0)); then
    git_state_after="$(git status --porcelain=v1 --untracked-files=all)"
    [[ "$git_state_after" == "$git_state_before" ]] ||
        die "the offline reproduction changed the Git worktree"
    if ((ALLOW_DIRTY == 0)); then
        [[ -z "$git_state_after" ]] ||
            die "worktree is not clean after reproduction"
        pass "repository remained clean"
    else
        pass "pre-existing dirty state was unchanged"
    fi
fi

printf '\n============================================================\n'
if ((SKIP_GIT == 1 || SKIP_TESTS == 1)); then
    printf 'DIAGNOSTIC CHECK COMPLETED (one or more full checks skipped)\n'
else
    printf 'OFFLINE REPRODUCTION: PASS\n'
fi
printf 'No Docker, Containerlab, Ollama, or network access was used.\n'
printf 'No preserved evidence was rewritten.\n'
printf '============================================================\n'
