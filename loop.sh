#!/usr/bin/env bash
# Ralph loop — fresh context per iteration. Run ONLY inside the devcontainer.
set -uo pipefail

MAX_ITERATIONS="${1:-25}"
MODEL="${RALPH_MODEL:-sonnet}"
MARKER_DONE="RALPH_COMPLETE"
MARKER_BLOCKED="RALPH_BLOCKED"
MARKER_MILESTONE="MILESTONE_COMPLETE"
STALL_LIMIT=3

# Guardia: bypass permissions è consentito SOLO dentro la sandbox
if [ ! -f /.dockerenv ]; then
  echo "ERROR: not inside the devcontainer. Refusing to run with bypassed permissions." >&2
  exit 1
fi

[ -s docs/PRD_V2.md ] || { echo "ERROR: docs/PRD_V2.md missing or empty." >&2; exit 1; }
[ -s TASKS.md ] || { echo "ERROR: TASKS.md missing or empty. Run /prd first." >&2; exit 1; }

mkdir -p progress
touch progress/log.md
stall=0

for i in $(seq 1 "$MAX_ITERATIONS"); do
  echo "════ Ralph iteration $i/$MAX_ITERATIONS — $(date -Is) ════"
  head_before=$(git rev-parse HEAD 2>/dev/null || echo none)

  output=$(claude -p "$(cat PROMPT.md)" \
      --model "$MODEL" \
      --dangerously-skip-permissions \
      --output-format text 2>&1 | tee "progress/iteration-$i.log")

  # Commit di sicurezza se l'agente non ha committato
  git add -A >/dev/null 2>&1
  if ! git diff --cached --quiet; then
    git commit -m "ralph: iteration $i (auto)" --no-verify >/dev/null 2>&1 || true
  fi
  head_after=$(git rev-parse HEAD 2>/dev/null || echo none)
  if [ "$head_after" = "$head_before" ]; then
    stall=$((stall + 1))
    echo "── no progress this iteration (stall $stall/$STALL_LIMIT)"
  else
    stall=0
  fi

  if grep -q "$MARKER_MILESTONE" <<< "$output"; then
    ms=$(grep -oE "MILESTONE_COMPLETE M[0-9]+" <<< "$output" | head -1)
    echo "✔ $ms at iteration $i — STOP. Run the external gate: ./tools/gate_check.sh"
    exit 0
  fi
  if grep -q "$MARKER_DONE" <<< "$output"; then
    echo "✔ RALPH_COMPLETE at iteration $i"; exit 0
  fi
  if grep -q "$MARKER_BLOCKED" <<< "$output"; then
    echo "✖ RALPH_BLOCKED — see progress/log.md"; exit 2
  fi
  if [ "$stall" -ge "$STALL_LIMIT" ]; then
    echo "✖ Circuit breaker: $STALL_LIMIT iterations with no changes. Aborting."; exit 3
  fi
done

echo "✖ Max iterations ($MAX_ITERATIONS) reached without completion."
exit 4
