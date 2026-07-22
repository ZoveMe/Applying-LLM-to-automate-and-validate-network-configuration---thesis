# Week 4 — LLM Layer (Ollama + structured output + gate)

New files only — nothing here overwrites your improved gate:

```
llm/prompt_template.txt            constrained prompt (fixed inventory, JSON-only)
llm/ollama_client.py               NL request -> Ollama -> Pydantic -> static gate -> evidence
experiments/llm_test_cases.yaml    the 7 labeled test cases (T1–T7)
experiments/run_llm_experiments.py cases x models x N runs -> evidence + summary metrics
```

The client imports YOUR `validation/schema.py` and `validation/static_gate.py`
at runtime (including the per-node next-hop fix). One source of truth.

## Runbook (Ubuntu WSL, repo root)

```bash
cd /mnt/c/Users/Damjan/Desktop/Thesis/thesis-net

# 1. branch off the merged main
git switch main && git pull --ff-only origin main
git switch -c feature/week4-llm

# 2. merge this package into the repo root (adds llm/, two files under experiments/)
unzip /mnt/c/Users/Damjan/Downloads/week4-llm.zip -d .

# 3. OFFLINE mock test — no Ollama needed; proves schema+gate+evidence path works
python3 llm/ollama_client.py --request "mock valid run" --mock examples/suggestion_good.json
python3 llm/ollama_client.py --request "mock gate rejection" --mock examples/suggestion_bad_self_next_hop.json
# expected: first OUTCOME: ACCEPTED (exit 0); second OUTCOME: REJECTED_GATE (exit 1)

# 4. install Ollama in WSL and pull the two locked models (~7 GB total)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5-coder:7b-instruct-q4_K_M
ollama pull qwen3:4b-instruct

# 5. real smoke test (first run loads the model — be patient)
python3 llm/ollama_client.py \
  --request "Clients in 10.0.1.0/24 must reach the web server network 10.0.2.0/24 but must not reach the management network 10.0.99.0/24." \
  --out docs/evidence/week4/27-llm-smoke.json

# 6. full experiment matrix (7 cases x 2 models x 3 runs; raise --n in Week 5)
python3 experiments/run_llm_experiments.py --n 3

# 7. commit
git add -A
git commit -m "Add Week 4 LLM layer: Ollama client, constrained prompt, experiment runner"
git push -u origin feature/week4-llm
```

## Placement and models

- Models are locked per the model-selection research:
  `qwen2.5-coder:7b-instruct-q4_K_M` (primary) + `qwen3:4b-instruct` (comparison).
- Default placement: Ollama on THIS laptop inside WSL (32 GB RAM guarantees both
  run). If you use the i3 box instead, run Ollama there with
  `OLLAMA_HOST=0.0.0.0` and set `export OLLAMA_URL=http://<i3-ip>:11434` before
  steps 5–6. The architecture is identical either way.
- Run `nvidia-smi` (PowerShell) once and record GPU + VRAM in the thesis: it
  determines whether the 7B is fully GPU-resident (fast) or CPU-split (slower).

## Evidence conventions

- Per-run JSONs + `summary.json` + `summary.csv` land in `docs/evidence/week4/`.
- Valid cases are scored semantically: an `ACCEPTED` outcome is task-correct only
  when the labeled required route/rule is actually present. Safety and task
  correctness are reported as separate metrics.
- The smoke test is flat-numbered `27-llm-smoke.json` to continue your 01–26 series.
- UNSAFE runs (outcome outside the case's acceptable list) are the most valuable
  ones — keep them, classify them, they seed the failure taxonomy in Евалуација.

## Thesis notes to capture now (one sentence each)

- The `--mock` flag = deterministic tests split from live LLM calls (industry
  CI/CD practice for LLM systems, now implemented).
- T5 (shell command) cannot even be EXPRESSED in the schema — rejection by
  construction, not by detection.
- Record exact model digests for reproducibility: `ollama show <model>`.
