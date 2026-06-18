#!/bin/bash
# Live progress monitor for parallel claude-code + codex runs.
# Usage: bash scripts/monitor.sh [prefix]   (prefix = outputs_root basename; default: full)
PREFIX="${1:-full}"
cd "$(dirname "$0")/.."        # repo root
TOTAL=$(wc -l < patcheval/exp_agent/claudecode/dataset.jsonl 2>/dev/null | tr -d ' ')
while true; do
  clear
  echo "=== PatchEval parallel run — prefix '$PREFIX'  (refresh 5s · Ctrl-C to stop) ==="
  for a in claudecode codex; do
    pdir="patcheval/exp_agent/$a/outputs/$PREFIX/patches"
    edir="patcheval/exp_agent/$a/evaluation_output/$PREFIX"
    n=$(ls "$pdir"/*.patch 2>/dev/null | wc -l | tr -d ' ')
    line=$(printf "  %-11s gen: %3s/%s patches" "$a" "$n" "${TOTAL:-229}")
    [ -f "$edir/summary.json" ] && line="$line   eval: summary.json ready"
    echo "$line"
  done
  echo "  --- running CVE containers ---"
  docker ps --format '  {{.Names}}  {{.Status}}' 2>/dev/null | grep bench || echo "  (none running)"
  sleep 5
done
