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
# Evaluate Codex-generated patches. Evaluation is agent-agnostic: it applies each
# patch in its CVE container and runs the PoC + unit tests (no LLM involved).
prefix="${1:?usage: run_eval.sh <prefix> (e.g. codex_smoke)}"

cd "$(dirname "$0")/.."

PY="python"; [ -x ".venv/bin/python" ] && PY=".venv/bin/python"

# Config-aware: when config.yaml exists (git-ignored) and DATASET isn't already
# set in the env, read dataset + docker platform from it. A null docker_platform
# means native (we do NOT force linux/amd64); set it to linux/amd64 to opt in.
if [ -z "${DATASET:-}" ] && [ -f config.yaml ]; then
  DATASET="$("$PY" -c 'import yaml; c=yaml.safe_load(open("config.yaml")).get("run",{}) or {}; print(c.get("dataset","dataset.jsonl"))')"
  _PLAT="$("$PY" -c 'import yaml; c=yaml.safe_load(open("config.yaml")).get("run",{}) or {}; print(c.get("docker_platform") or "")')"
  [ -n "$_PLAT" ] && export DOCKER_DEFAULT_PLATFORM="$_PLAT"
fi
DATASET="${DATASET:-dataset.jsonl}"           # scope to the CVEs you generated for
EVAL_WORKERS="${EVAL_WORKERS:-4}"

echo "Eval prefix: $prefix   dataset: $DATASET   workers: $EVAL_WORKERS   python: $PY"

# 1) Reshape outputs/<prefix>/patches/*.patch -> a process JSONL scoped to $DATASET.
"$PY" evaluation/process_data.py \
    --output_dir outputs/${prefix}/patches \
    --dataset_path "$DATASET" \
    --process_data_path evaluation/process_datas/${prefix}_process.jsonl \
    --test_data_path ../../datasets/patcheval_dataset.json

# 2) Apply each patch in its CVE container and run the PoC / unit tests.
"$PY" ../../evaluation/run_evaluation.py \
    --output ${prefix} \
    --patch_file evaluation/process_datas/${prefix}_process.jsonl \
    --input_file ../../datasets/input.json \
    --max_workers "$EVAL_WORKERS"
