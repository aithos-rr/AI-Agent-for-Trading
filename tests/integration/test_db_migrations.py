"""Integration tests for the initial Alembic migration (§9.3, §12 M1).

Requires a live PostgreSQL server (🐘 pytest-postgresql).
Verifies:
  - upgrade head creates all 20 tables
  - key constraints and columns present
  - downgrade base removes all tables
  - re-upgrade is idempotent
"""

import os

import psycopg
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def _get_sync_url(asyncpg_url: str) -> str:
    """Convert asyncpg URL to psycopg (sync) for inspection."""
    return asyncpg_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


@pytest.fixture(scope="module")
def migration_db_url(postgresql_proc_fixture: object) -> str:  # type: ignore[override]
    """Create a dedicated database and return its asyncpg URL (module-scoped)."""
    proc = postgresql_proc_fixture  # type: ignore[attr-defined]
    dbname = "aiat_migration_test"

    # Create the database (connect to postgres admin db first).
    with psycopg.connect(
        host=proc.host, port=proc.port, user=proc.user, dbname="postgres", autocommit=True
    ) as conn:
        # Drop in case a previous failed run left it around.
        conn.execute(f"DROP DATABASE IF EXISTS {dbname}")
        conn.execute(f"CREATE DATABASE {dbname}")

    return f"postgresql+asyncpg://{proc.user}@{proc.host}:{proc.port}/{dbname}"


@pytest.fixture(scope="module")
def alembic_cfg(migration_db_url: str) -> Config:
    os.environ["AIAT_DATABASE_URL"] = migration_db_url
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", migration_db_url)
    return cfg


def get_inspector(asyncpg_url: str):  # type: ignore[no-untyped-def]
    sync_url = _get_sync_url(asyncpg_url)
    engine = create_engine(sync_url)
    inspector = inspect(engine)
    return inspector, engine


EXPECTED_TABLES = {
    "experiments",
    "models",
    "prompt_templates",
    "context_snapshots",
    "context_build_runs",
    "runs",
    "llm_invocations",
    "decisions",
    "decision_actions",
    "account_snapshots",
    "positions",
    "orders",
    "fee_events",
    "funding_events",
    "cost_events",
    "tax_sim_periods",
    "outcomes",
    "baseline_configs",
    "baseline_equity_snapshots",
    "errors",
}


def test_upgrade_creates_20_tables(alembic_cfg: Config, migration_db_url: str) -> None:
    """upgrade head creates exactly 20 tables (§3.2.1-§3.2.9, DISCREPANZA #1)."""
    command.upgrade(alembic_cfg, "head")
    inspector, engine = get_inspector(migration_db_url)
    tables = set(inspector.get_table_names())
    engine.dispose()
    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables after upgrade: {missing}"
    assert len(tables) >= 20, f"Expected ≥20 tables, got {len(tables)}: {tables}"


def test_denormalization_columns_present(alembic_cfg: Config, migration_db_url: str) -> None:
    """Tables that require inv #3 denormalization have experiment_id/model_id/run_id."""
    inspector, engine = get_inspector(migration_db_url)

    # runs and decision_actions must have experiment_id + model_id (inv #3)
    for table in ("runs", "decision_actions", "cost_events", "fee_events"):
        cols = {c["name"] for c in inspector.get_columns(table)}
        assert "experiment_id" in cols, f"{table} missing experiment_id"
        assert "model_id" in cols, f"{table} missing model_id"

    engine.dispose()


def test_unique_constraints_present(migration_db_url: str) -> None:
    """Key unique constraints (uq_runs_exp_model_sched, etc.) are present."""
    inspector, engine = get_inspector(migration_db_url)
    ucs = {uc["name"] for uc in inspector.get_unique_constraints("runs") if uc.get("name")}
    assert "uq_runs_exp_model_sched" in ucs, f"runs unique constraints: {ucs}"
    engine.dispose()


def test_downgrade_removes_all_tables(alembic_cfg: Config, migration_db_url: str) -> None:
    """downgrade base removes all 20 tables."""
    command.downgrade(alembic_cfg, "base")
    inspector, engine = get_inspector(migration_db_url)
    tables = set(inspector.get_table_names())
    engine.dispose()
    aiat_tables = EXPECTED_TABLES & tables
    assert not aiat_tables, f"Tables still present after downgrade base: {aiat_tables}"


def test_upgrade_idempotent(alembic_cfg: Config, migration_db_url: str) -> None:
    """After downgrade, upgrade head re-creates 20 tables (idempotent)."""
    command.upgrade(alembic_cfg, "head")
    inspector, engine = get_inspector(migration_db_url)
    tables = set(inspector.get_table_names())
    engine.dispose()
    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables after re-upgrade: {missing}"
