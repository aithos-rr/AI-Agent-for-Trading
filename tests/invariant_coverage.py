"""Invariant coverage matrix (PRD §9.7, M5-T12).

This file provides @pytest.mark.invariant("N") tests for invariants that are
verified by tooling (ruff, import-linter, AST) or by schema inspection.
Tests for invariants #1, #3-#9, #13 live in dedicated test modules.

Coverage map:
  #1  test_isolation.py
  #2  test_run_logs_git_sha_and_hashes  ← here
  #3  test_db_migrations.py::test_denormalization_columns_present
  #4  test_db_repositories_decisions.py::test_persist_decision_creates_all_rows
  #5  test_lifecycle.py::test_agent_a9_memory_off_ok
  #6  test_schemas_trade_decision.py::test_unknown_signal_raises
  #7  test_schemas_trade_decision.py::test_confidence_boundary_valid
  #8  test_guardrails.py::TestCleanPassthrough::test_no_flags_on_valid_long
  #9  test_lifecycle.py::test_check_network_testnet_rejects_mainnet
  #10 test_ruff_t201_no_print_in_src     ← here
  #11 test_no_raw_sql_outside_repos      ← here
  #12 test_no_float_in_money_fields      ← here
  #13 test_context_parity.py
  #14 test_import_linter_clean           ← here
  #15 test_tick_coverage_schema          ← here
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent / "src"
_TESTS = Path(__file__).parent
_SCHEMAS_PY = _SRC / "aiat" / "domain" / "schemas.py"


# ---------------------------------------------------------------------------
# #2 — Determinismo configurazione: every Run row carries git_sha + hash
# ---------------------------------------------------------------------------


@pytest.mark.invariant("2")
def test_run_logs_git_sha_and_hashes() -> None:
    """Run SQLAlchemy model must have git_commit_sha and prompt_template_hash columns (inv #2)."""
    from aiat.db.models.run import Run

    col_names = {c.name for c in Run.__table__.columns}
    assert "git_commit_sha" in col_names, "Run model missing git_commit_sha column"
    assert "prompt_template_hash" in col_names, "Run model missing prompt_template_hash column"
    assert "rendered_prompt_hash" in col_names, "Run model missing rendered_prompt_hash column"
    assert "schema_version" in col_names, "Run model missing schema_version column"


# ---------------------------------------------------------------------------
# #10 — No print() in runtime code (ruff T201)
# ---------------------------------------------------------------------------


@pytest.mark.invariant("10")
def test_ruff_t201_no_print_in_src() -> None:
    """ruff --select T201 must report zero violations in src/ (inv #10)."""
    result = subprocess.run(
        ["uv", "run", "ruff", "check", "--select", "T201", str(_SRC)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    assert result.returncode == 0, (
        f"ruff T201 found print() calls in src/:\n{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# #11 — No raw SQL ad-hoc outside repositories/ (text() calls)
# ---------------------------------------------------------------------------


@pytest.mark.invariant("11")
def test_no_raw_sql_outside_repos() -> None:
    """No execute(text(...)) calls outside aiat/db/repositories/ and scripts/ (inv #11)."""
    violations: list[str] = []
    allowed_dirs = {
        _SRC / "aiat" / "db" / "repositories",
        _SRC / "aiat" / "db" / "scripts" if (_SRC / "aiat" / "db" / "scripts").exists() else None,
    }
    for py_file in _SRC.rglob("*.py"):
        if any(allowed is not None and py_file.is_relative_to(allowed) for allowed in allowed_dirs):
            continue
        source = py_file.read_text(encoding="utf-8")
        if "execute(text(" in source or ".execute(text(" in source:
            violations.append(str(py_file.relative_to(_SRC)))
    assert not violations, (
        "Raw SQL (execute(text(...))) found outside repositories/:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# #12 — Decimal for money fields (no float literals in schemas)
# ---------------------------------------------------------------------------


@pytest.mark.invariant("12")
def test_no_float_in_money_fields() -> None:
    """No float literals for money fields in domain/schemas.py (inv #12)."""
    source = _SCHEMAS_PY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_SCHEMAS_PY))

    # Money-related field names that must never use float
    _MONEY_NAMES = frozenset(
        {
            "equity_usd",
            "available_usd",
            "margin_used_usd",
            "unrealized_pnl_usd",
            "cost_usd",
            "price_usd",
            "size_units",
            "size_pct",
            "leverage",
            "stop_loss_pct",
            "take_profit_pct",
            "limit_price",
            "confidence",
            "portfolio_confidence",
        }
    )

    violations: list[str] = []
    for node in ast.walk(tree):
        # Check assignments like: field: float = 0.1 or field = 0.1
        if isinstance(node, (ast.AnnAssign, ast.Assign)):
            # Check if the value is a float literal
            value = node.value if hasattr(node, "value") else None
            if isinstance(value, ast.Constant) and isinstance(value.value, float):
                # Get the variable name
                target = node.target if isinstance(node, ast.AnnAssign) else None
                if target is None and isinstance(node, ast.Assign):
                    target = node.targets[0] if node.targets else None
                name = getattr(target, "id", None) or getattr(target, "attr", None)
                if name and name in _MONEY_NAMES:
                    violations.append(
                        f"line {value.lineno}: float literal {value.value!r} "
                        f"for money field '{name}'"
                    )

    # Also check for Decimal(float) calls like Decimal(0.20) — lossy init
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            is_decimal_call = (isinstance(func, ast.Name) and func.id == "Decimal") or (
                isinstance(func, ast.Attribute) and func.attr == "Decimal"
            )
            if is_decimal_call and node.args:
                first_arg = node.args[0]
                if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, float):
                    violations.append(
                        f"line {first_arg.lineno}: Decimal(float) "
                        f"Decimal({first_arg.value!r}) — use Decimal(str) form"
                    )

    assert not violations, (
        "Float literals found in money fields of domain/schemas.py (inv #12):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# #14 — No circular module dependencies (import-linter)
# ---------------------------------------------------------------------------


@pytest.mark.invariant("14")
def test_import_linter_clean() -> None:
    """import-linter must report no contract violations (inv #14 — no module cycles)."""
    result = subprocess.run(
        ["uv", "run", "lint-imports"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    assert result.returncode == 0, (
        f"import-linter found contract violations (inv #14):\n{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# #15 — Tick coverage KPI: schema supports 4-run-per-tick query
# ---------------------------------------------------------------------------


@pytest.mark.invariant("15")
def test_tick_coverage_schema() -> None:
    """Run model must expose tick_id, model_id, experiment_id for tick KPI queries (inv #15)."""
    from aiat.db.models.run import Run

    col_names = {c.name for c in Run.__table__.columns}
    # These 3 columns are required to query: N runs per tick per experiment
    assert "tick_id" in col_names, "Run model missing tick_id — can't query tick coverage KPI"
    assert "model_id" in col_names, "Run model missing model_id — can't query tick coverage KPI"
    assert "experiment_id" in col_names, (
        "Run model missing experiment_id — can't query tick coverage KPI"
    )
    assert "status" in col_names, "Run model missing status — can't filter successful ticks"
