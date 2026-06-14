"""Fix context_build_runs and context_snapshots to match models (M3-T08).

Revision ID: 002
Revises: 001
Create Date: 2026-06-14

Changes:
- context_build_runs: add failure_stage, error_context; drop error_message, duration_ms;
  fix check constraint to include 'running'; remove FK to experiments;
  add unique constraint; rename partial index.
- context_snapshots: remove FK to experiments (not in model).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── context_snapshots: remove FK to experiments (not in model) ──────────
    op.drop_constraint(
        "context_snapshots_experiment_id_fkey",
        "context_snapshots",
        type_="foreignkey",
    )

    # ── context_build_runs ───────────────────────────────────────────────────

    # Remove FK to experiments (not in model)
    op.drop_constraint(
        "context_build_runs_experiment_id_fkey",
        "context_build_runs",
        type_="foreignkey",
    )

    # Add columns present in model but missing from migration
    op.add_column(
        "context_build_runs",
        sa.Column("failure_stage", sa.String(), nullable=True),
    )
    op.add_column(
        "context_build_runs",
        sa.Column("error_context", postgresql.JSONB(), nullable=True),
    )

    # Drop columns in migration that are absent from model
    op.drop_column("context_build_runs", "error_message")
    op.drop_column("context_build_runs", "duration_ms")

    # Fix check constraint: add 'running' status
    op.drop_constraint("chk_ctx_build_run_status", "context_build_runs")
    op.create_check_constraint(
        "chk_ctx_build_run_status",
        "context_build_runs",
        "status IN ('running','success','partial','failed','timeout')",
    )

    # Add unique constraint missing from migration
    op.create_unique_constraint(
        "uq_context_build_runs_exp_tick",
        "context_build_runs",
        ["experiment_id", "tick_id"],
    )

    # Rename partial index to match model definition
    op.drop_index("idx_ctx_build_run_partial", table_name="context_build_runs")
    op.create_index(
        "idx_context_build_runs_status",
        "context_build_runs",
        ["status"],
        postgresql_where=sa.text("status IN ('failed','timeout','partial')"),
    )
    op.create_index(
        "idx_context_build_runs_tick",
        "context_build_runs",
        ["tick_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_context_build_runs_tick", table_name="context_build_runs")
    op.drop_index("idx_context_build_runs_status", table_name="context_build_runs")
    op.create_index(
        "idx_ctx_build_run_partial",
        "context_build_runs",
        ["status"],
        postgresql_where=sa.text("status IN ('failed','timeout','partial')"),
    )
    op.drop_constraint("uq_context_build_runs_exp_tick", "context_build_runs", type_="unique")

    op.drop_constraint("chk_ctx_build_run_status", "context_build_runs")
    op.create_check_constraint(
        "chk_ctx_build_run_status",
        "context_build_runs",
        "status IN ('success','partial','failed','timeout')",
    )

    op.drop_column("context_build_runs", "error_context")
    op.drop_column("context_build_runs", "failure_stage")

    op.add_column(
        "context_build_runs",
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "context_build_runs",
        sa.Column("error_message", sa.Text(), nullable=True),
    )

    op.create_foreign_key(
        "context_build_runs_experiment_id_fkey",
        "context_build_runs",
        "experiments",
        ["experiment_id"],
        ["id"],
    )
    op.create_foreign_key(
        "context_snapshots_experiment_id_fkey",
        "context_snapshots",
        "experiments",
        ["experiment_id"],
        ["id"],
    )
