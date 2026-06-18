#!/bin/bash
# Copyright (c) 2025 ByteDance Ltd. and/or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Run the OpenAI Codex agent on PatchEval. Authenticates the in-container Codex
# CLI with an OpenAI API key OR a ChatGPT-plan subscription (~/.codex/auth.json).
# See README.md.
set -uo pipefail

cd "$(dirname "$0")/.."   # codex project root

# --- ensure the defending-code harness is present (source of the in-container skills) ---
# Idempotent: clones only if missing. third_party/ is git-ignored, so a fresh checkout
# needs this. Non-fatal: if the clone fails Codex still runs (with its Security plugin only).
HARNESS_DIR="third_party/defending-code-reference-harness"
if [ ! -d "$HARNESS_DIR/.claude/skills" ]; then
    echo "[setup] fetching defending-code harness -> $HARNESS_DIR"
    git clone --depth 1 https://github.com/anthropics/defending-code-reference-harness "$HARNESS_DIR" \
        || echo "[setup] WARNING: harness clone failed; Codex will run WITHOUT defending-code skills" >&2
fi

# --- credentials (.env.local may hold CODEX_API_KEY / OPENAI_API_KEY) ---
if [ -f .env.local ]; then
    set -a; . ./.env.local; set +a
fi

# --- agent / auth / model ---
export AGENT="${AGENT:-codex}"
export AUTH_MODE="${AUTH_MODE:-auto}"                 # api-key | subscription | auto
export CODEX_MODEL="${CODEX_MODEL:-gpt-5.5}"          # override per your Codex access
export CODEX_REASONING_EFFORT="${CODEX_REASONING_EFFORT:-xhigh}"
export HOST_CODEX_AUTH="${HOST_CODEX_AUTH:-$HOME/.codex/auth.json}"  # seeded for subscription auth
export DOCKER_DEFAULT_PLATFORM="${DOCKER_DEFAULT_PLATFORM:-linux/amd64}"  # CVE images are amd64
export MY_MODEL="${MY_MODEL:-codex}"                  # container-name tag

# --- dataset / output / parallelism ---
DATASET="${DATASET:-dataset_subset.jsonl}"
OUTDIR="${OUTDIR:-./outputs/codex_smoke}"
MAX_WORKERS="${MAX_WORKERS:-1}"

PY="python"
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"

echo "Agent:    $AGENT"
echo "Auth:     $AUTH_MODE"
echo "Model:    $CODEX_MODEL  (effort: $CODEX_REASONING_EFFORT)"
echo "Platform: $DOCKER_DEFAULT_PLATFORM"
echo "Dataset:  $DATASET   Output: $OUTDIR   Workers: $MAX_WORKERS   Python: $PY"
echo ""

mkdir -p "$OUTDIR"

# Prefer a config.yaml run when present: model / reasoning / auth / dataset /
# outputs / workers all come from the config (CLI flags below are the fallback).
CONFIG="${CONFIG:-config.yaml}"
if [ -f "$CONFIG" ]; then
    echo "Config:   $CONFIG (config-driven; env/--auth flags ignored)"
    echo ""
    "$PY" -m patcheval.cli batch --config "$CONFIG" --resume
    rc=$?
else
    "$PY" -m patcheval.cli batch \
        --dataset "$DATASET" \
        --outputs-root "$OUTDIR" \
        --agent codex \
        --auth "$AUTH_MODE" \
        --strategy default \
        --max-workers "$MAX_WORKERS" \
        --tool-limits "total:200" \
        --max-cost-usd 1000 \
        --allow-git-diff-fallback \
        --resume \
        --save-process-logs
    rc=$?
fi

# Best-effort cleanup of this run's work containers.
docker ps -aq --filter "name=bench." --filter "name=${MY_MODEL}" 2>/dev/null \
    | xargs -r docker rm -f >/dev/null 2>&1 || true

echo ""
echo "Batch exit code: $rc"
echo "Patches in: $OUTDIR/patches/"
