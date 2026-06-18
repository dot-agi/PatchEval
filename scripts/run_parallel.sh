#!/bin/bash
# Run claude-code + codex in PARALLEL in a tmux session (survives Cursor-tunnel/ssh drops).
#
#   bash scripts/run_parallel.sh infer            # generation (default)
#   bash scripts/run_parallel.sh eval [prefix]    # evaluation  (prefix = outputs_root basename)
#
# Then:   tmux attach -t patcheval     (detach without stopping: Ctrl-b then d)
# Layout: 3 panes -> [ claude gen | codex gen | live monitor ]
set -uo pipefail
cd "$(dirname "$0")/.."          # repo root
REPO="$PWD"
MODE="${1:-infer}"
PREFIX="${2:-full}"
SESSION="patcheval"
CLA="patcheval/exp_agent/claudecode"
COD="patcheval/exp_agent/codex"

command -v tmux >/dev/null || { echo "tmux not installed — e.g. 'sudo apt install -y tmux' or 'sudo dnf install -y tmux'"; exit 1; }

if [ "$MODE" = "infer" ]; then
  CCMD="cd $CLA && MY_MODEL=claude bash shells/run_infer.sh 2>&1 | tee $REPO/gen.claude.log"
  XCMD="cd $COD && MY_MODEL=codex  bash shells/run_infer.sh 2>&1 | tee $REPO/gen.codex.log"
elif [ "$MODE" = "eval" ]; then
  CCMD="cd $CLA && EVAL_WORKERS=16 bash shells/run_eval.sh $PREFIX 2>&1 | tee $REPO/eval.claude.log"
  XCMD="cd $COD && EVAL_WORKERS=16 bash shells/run_eval.sh $PREFIX 2>&1 | tee $REPO/eval.codex.log"
else
  echo "usage: $0 {infer|eval} [prefix]"; exit 1
fi

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session   -d  -s "$SESSION" -c "$REPO"
tmux send-keys     -t "$SESSION" "$CCMD" C-m
tmux split-window  -h  -t "$SESSION" -c "$REPO"
tmux send-keys     -t "$SESSION" "$XCMD" C-m
tmux split-window  -v  -t "$SESSION" -c "$REPO"
tmux send-keys     -t "$SESSION" "bash scripts/monitor.sh $PREFIX" C-m
tmux select-layout -t "$SESSION" tiled

echo "tmux session '$SESSION' started  [claude | codex | monitor]   mode=$MODE prefix=$PREFIX"
echo "  attach:   tmux attach -t $SESSION"
echo "  detach:   Ctrl-b then d        (the run keeps going after you disconnect)"
echo "  reattach: tmux attach -t $SESSION   (after a tunnel drop)"
echo "  stop all: tmux kill-session -t $SESSION"
