#!/usr/bin/env bash
set -uo pipefail

MILESTONE="${1:-}"
if [ -z "$MILESTONE" ]; then
  MILESTONE=$(grep -oE '^- \[x\] \*\*M[0-9]' TASKS.md 2>/dev/null \
    | grep -oE 'M[0-9]' | sort -u | tail -1)
fi
if [ -z "$MILESTONE" ]; then
  echo "✖ Could not determine milestone. Pass one: ./tools/gate_check.sh M0" >&2
  exit 1
fi
MNUM="${MILESTONE#M}"

echo "════════════════════════════════════════════════════════════════"
echo "  EXTERNAL GATE — validating milestone $MILESTONE (independent of loop)"
echo "════════════════════════════════════════════════════════════════"

declare -a FAILED=()
run_check() {
  local label="$1"; shift
  echo ""
  echo "▶ $label"
  echo "  \$ $*"
  if "$@"; then
    echo "  ✓ PASS — $label"
  else
    echo "  ✖ FAIL — $label"
    FAILED+=("$label")
  fi
}

run_check "ruff check (lint)"            uv run ruff check src tests
run_check "ruff format --check"          uv run ruff format --check src tests
run_check "mypy (strict)"                uv run mypy src
run_check "import-linter (lint-imports)" uv run lint-imports

# Global coverage 80% — over ALL available test tiers (unit+integration+e2e),
# because SQLAlchemy models are exercised by integration tests, not unit.
# We collect the tiers that currently exist so the gate scales with the milestone.
PYTEST_PATHS=(tests/unit)
ls tests/integration/test_*.py >/dev/null 2>&1 && PYTEST_PATHS+=(tests/integration)
ls tests/e2e/test_*.py >/dev/null 2>&1 && PYTEST_PATHS+=(tests/e2e)
run_check "pytest all tiers (global cov >=80%)" \
  uv run pytest "${PYTEST_PATHS[@]}" -q --cov=src/aiat --cov-fail-under=80

COV_FLAGS=(); TEST_PATHS=()
[ "$MNUM" -ge 1 ] && { COV_FLAGS+=(--cov=src/aiat/domain);    TEST_PATHS+=(tests/unit/domain); }
[ "$MNUM" -ge 2 ] && { COV_FLAGS+=(--cov=src/aiat/llm);       TEST_PATHS+=(tests/unit/llm); }
[ "$MNUM" -ge 4 ] && { COV_FLAGS+=(--cov=src/aiat/execution); TEST_PATHS+=(tests/unit/execution); }
if [ "${#TEST_PATHS[@]}" -gt 0 ]; then
  run_check "pytest core (cov >=95% on domain/llm/execution as applicable)" \
    uv run pytest "${TEST_PATHS[@]}" -q "${COV_FLAGS[@]}" --cov-fail-under=95
fi

if [ "$MNUM" -ge 1 ]; then
  if ls tests/integration/test_*.py >/dev/null 2>&1; then
    run_check "pytest integration (Postgres ephemeral)" uv run pytest tests/integration -q
  else
    echo ""; echo "▶ integration tests — none present yet (ok for $MILESTONE)"
  fi
fi

if [ "$MNUM" -ge 5 ]; then
  if ls tests/e2e/test_*.py >/dev/null 2>&1; then
    run_check "pytest e2e" uv run pytest tests/e2e -q
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
if [ "${#FAILED[@]}" -eq 0 ]; then
  echo "  ✓ GATE PASSED for $MILESTONE — all checks green."
  echo "  → Proceed to next milestone (commit the gate result first)."
  echo "════════════════════════════════════════════════════════════════"
  exit 0
else
  echo "  ✖ GATE FAILED for $MILESTONE — ${#FAILED[@]} check(s) failed:"
  for f in "${FAILED[@]}"; do echo "      - $f"; done
  echo "  → Do NOT proceed. Fix, then re-run ./tools/gate_check.sh $MILESTONE"
  echo "════════════════════════════════════════════════════════════════"
  exit 1
fi
