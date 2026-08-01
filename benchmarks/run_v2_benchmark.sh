#!/usr/bin/env bash
# Scaled PROPOSE (V2) benchmark: 12 models x 10 benchmark cases x 3 repetitions
# = 360 live runs. Companion to run_explain_benchmark.sh (the EXPLAIN task).
#
# Purpose: the V2 campaign originally measured two models. Running the same
# labeled benchmark across every model in the roster tests the thesis's central
# claim at breadth — that whole-system safety is INDEPENDENT of model quality,
# because the deterministic gate, not the model, decides what may deploy.
#
# Deploys nothing: no Docker, no Containerlab, no Ansible. Evidence only.
#
# Resumable: one subdirectory per model; a model whose directory already
# contains a manifest.json is skipped. Interrupted models are redone cleanly.
#
# Usage:  bash benchmarks/run_v2_benchmark.sh [EVIDENCE_ROOT]
set -u
cd "$(dirname "$0")/.."
ROOT="${1:-docs/evidence/week6-v2-12m}"
REPS="${REPS:-3}"

while IFS= read -r model; do
  [[ -z "$model" || "$model" == \#* ]] && continue
  out="$ROOT/$(echo "$model" | tr ':/' '__')"
  if [[ -f "$out/manifest.json" ]]; then
    echo "SKIP (complete): $model"
    continue
  fi
  [[ -d "$out" ]] && { echo "PARTIAL -> redoing: $model"; rm -rf "$out"; }
  echo "############ $model ############"
  python3 experiments/run_v2_experiments.py \
    --models "$model" --n "$REPS" --out-dir "$out"
done < benchmarks/models_13.txt

echo
echo "=== evaluating the full corpus ==="
python3 experiments/evaluate_v2.py \
  --evidence-dir "$ROOT" \
  --out-json "$ROOT/v2-12m-evaluation.json" \
  --out-csv  "$ROOT/v2-12m-evaluation.csv"

echo
echo "Campaign complete -> $ROOT"
echo "Next: python3 experiments/analyze_cross_task.py"
