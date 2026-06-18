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
# Run the ClaudeCode experiment with the REAL Claude Opus 4.8 at maximum
# reasoning effort, authenticated with the host's Claude subscription
# (CLAUDE_CODE_OAUTH_TOKEN). No claude-code-proxy. See OPUS48_LOCAL_RUN.md.
set -uo pipefail

# cd to the claudecode project root (this script lives in shells/)
cd "$(dirname "$0")/.."

# --- ensure the defending-code harness is present (source of the in-container skills) ---
# Idempotent: clones only if missing. third_party/ is git-ignored, so a fresh checkout
# needs this. Non-fatal: if the clone fails the agent still runs (without those skills).
HARNESS_DIR="third_party/defending-code-reference-harness"
if [ ! -d "$HARNESS_DIR/.claude/skills" ]; then
    echo "[setup] fetching defending-code harness -> $HARNESS_DIR"
    git clone --depth 1 https://github.com/anthropics/defending-code-reference-harness "$HARNESS_DIR" \
        || echo "[setup] WARNING: harness clone failed; agent will run WITHOUT defending-code skills" >&2
fi

# --- config-first: if a run config exists, drive the whole batch from it ---
# (auth/model/effort/run knobs all live in the YAML; see patcheval/config.py).
PY=".venv/bin/python"; [ -x "$PY" ] || PY=python3
CONFIG="${CONFIG:-config.yaml}"
if [ -f "$CONFIG" ]; then
    echo "Config-driven inference: $CONFIG  (python: $PY)"
    "$PY" -m patcheval.cli batch --config "$CONFIG" --resume
    exit $?
fi

# --- fallback (no $CONFIG present): legacy env / .env.local invocation ---
# --- credentials: Claude subscription OAuth token (from `claude setup-token`) ---
if [ -f .env.local ]; then
    set -a; . ./.env.local; set +a
fi
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    echo "ERROR: CLAUDE_CODE_OAUTH_TOKEN is not set." >&2
    echo "       Put it in .env.local (generate on the host with: claude setup-token)." >&2
    exit 1
fi
# A stray API key on the host would be injected into the container and override
# the subscription token - drop it.
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL 2>/dev/null || true

# --- model / effort / platform ---
export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-opus-4-8}"
export CLAUDE_CODE_EFFORT_LEVEL="${CLAUDE_CODE_EFFORT_LEVEL:-max}"
export DOCKER_DEFAULT_PLATFORM="${DOCKER_DEFAULT_PLATFORM:-linux/amd64}"  # CVE images are amd64
export MY_MODEL="${MY_MODEL:-opus48}"                                     # container-name tag

# --- dataset / output / parallelism ---
DATASET="${DATASET:-dataset_subset.jsonl}"
OUTDIR="${OUTDIR:-./outputs/opus48_smoke}"
MAX_WORKERS="${MAX_WORKERS:-1}"

# --- python: prefer the local venv that has the docker SDK ---
PY="python"
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"

echo "Model:    $ANTHROPIC_MODEL"
echo "Effort:   $CLAUDE_CODE_EFFORT_LEVEL"
echo "Platform: $DOCKER_DEFAULT_PLATFORM"
echo "Dataset:  $DATASET"
echo "Output:   $OUTDIR"
echo "Workers:  $MAX_WORKERS"
echo "Python:   $PY"
echo ""

mkdir -p "$OUTDIR"
"$PY" -m patcheval.cli batch \
    --dataset "$DATASET" \
    --outputs-root "$OUTDIR" \
    --strategy default \
    --max-workers "$MAX_WORKERS" \
    --tool-limits "total:200" \
    --max-cost-usd 1000 \
    --allow-git-diff-fallback \
    --resume \
    --save-process-logs
rc=$?

# Best-effort cleanup of this run's work containers (naming: bench.<cve>.<MY_MODEL>.work)
docker ps -aq --filter "name=bench." --filter "name=${MY_MODEL}" 2>/dev/null \
    | xargs -r docker rm -f >/dev/null 2>&1 || true

echo ""
echo "Batch exit code: $rc"
echo "Patch (if produced): $OUTDIR/patches/CVE-2021-21384.patch"
