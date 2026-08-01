#!/usr/bin/env bash
# Scaled EXPLAIN benchmark: 13 models x 3 suites.
#   small    thesis-core configs (r1, r2)            — 2 devices x 3 reps
#   l-clean  larger topology, correct configs (r1-r3) — 3 devices x 3 reps
#   l-faulty larger topology, injected faults (r1-r3) — 3 devices x 3 reps
# Per model: 24 runs. Full campaign: 13 x 24 = 312 live runs.
#
# Resumable: each model writes into its own subdirectory; models with an
# existing non-empty subdirectory are SKIPPED, so re-running after a crash
# or a bad model continues where it stopped.
#
# Usage:  bash benchmarks/run_explain_benchmark.sh [EVIDENCE_ROOT]
set -u
cd "$(dirname "$0")/.."
ROOT="${1:-docs/evidence/week6-explain-13m}"

run_suite() {
  local suite="$1"; shift
  local model="$1"; shift
  local out="$ROOT/$suite/$(echo "$model" | tr ':/' '__')"
  # SHA256SUMS.txt is written last -> it is the completion marker.
  if [[ -f "$out/SHA256SUMS.txt" ]]; then
    echo "SKIP (complete): $suite / $model"
    return 0
  fi
  if [[ -d "$out" ]]; then
    echo "PARTIAL (interrupted run) -> redoing: $suite / $model"
    rm -rf "$out"
  fi
  python3 experiments/run_explain_experiments.py \
    --out "$out" --models "$model" "$@"
}

while IFS= read -r model; do
  [[ -z "$model" || "$model" == \#* ]] && continue
  echo "############ $model ############"
  run_suite small    "$model"
  run_suite l-clean  "$model" --devices r1 r2 r3 \
    --config-root benchmarks/topology-l/configs --no-appendix
  run_suite l-faulty "$model" --devices r1 r2 r3 \
    --config-root benchmarks/topology-l/configs-faulty --no-appendix
done < benchmarks/models_13.txt

echo
echo "=== evaluating all three suites ==="
python3 experiments/evaluate_explain.py --evidence "$ROOT/small" \
  --out "$ROOT/small-evaluation.json"
python3 experiments/evaluate_explain.py --evidence "$ROOT/l-clean" \
  --intent benchmarks/topology-l/intent_l.yaml \
  --out "$ROOT/l-clean-evaluation.json"
python3 experiments/evaluate_explain.py --evidence "$ROOT/l-faulty" \
  --intent benchmarks/topology-l/intent_l_faulty.yaml \
  --out "$ROOT/l-faulty-evaluation.json"

echo
echo "Campaign complete -> $ROOT"
