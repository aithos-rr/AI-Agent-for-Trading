"""Initial schema — all 20 tables (§3.2.1-§3.2.9).

Revision ID: 001
Revises:
Create Date: 2026-06-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # § 3.2.1 — anagrafica/config
    op.create_table(
        "experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("git_commit_sha", sa.String(), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "models",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model_name_api", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("geography", sa.String(), nullable=False),
        sa.Column("wallet_address", sa.String(), nullable=False, unique=True),
        sa.Column("pricing_input_usd_per_1m", sa.Numeric(12, 6), nullable=False),
        sa.Column("pricing_output_usd_per_1m", sa.Numeric(12, 6), nullable=False),
        sa.Column(
            "pricing_reasoning_usd_per_1m",
            sa.Numeric(12, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("tier IN ('premium','cheap_alt')", name="chk_model_tier"),
        sa.CheckConstraint("geography IN ('USA','CN')", name="chk_model_geography"),
        sa.CheckConstraint("pricing_input_usd_per_1m >= 0", name="chk_model_pricing_input_ge0"),
        sa.CheckConstraint("pricing_output_usd_per_1m >= 0", name="chk_model_pricing_output_ge0"),
        sa.CheckConstraint(
            "pricing_reasoning_usd_per_1m >= 0", name="chk_model_pricing_reasoning_ge0"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "prompt_templates",
        sa.Column("sha256_hash", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False, unique=True),
        sa.Column("template_text", sa.Text(), nullable=False),
        sa.Column("confidence_def", sa.Text(), nullable=False),
        sa.Column("controlled_signals", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sha256_hash"),
    )

    # § 3.2.2 — context
    op.create_table(
        "context_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tick_id", sa.String(), nullable=False),
        sa.Column("tick_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("context_hash", sa.String(), nullable=False),
        sa.Column("context_json", postgresql.JSONB(), nullable=False),
        sa.Column("source_timestamps", postgresql.JSONB(), nullable=False),
        sa.Column("build_duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("build_duration_ms >= 0", name="chk_ctx_snap_build_duration_ge0"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_id", "tick_id", name="uq_context_snapshots_exp_tick"),
        sa.UniqueConstraint(
            "id", "experiment_id", "tick_id", name="uq_context_snapshots_id_exp_tick"
        ),
    )
    op.create_index("idx_context_tick", "context_snapshots", ["tick_at"])

    op.create_table(
        "context_build_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tick_id", sa.String(), nullable=False),
        sa.Column("tick_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('success','partial','failed','timeout')",
            name="chk_ctx_build_run_status",
        ),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["context_snapshot_id"], ["context_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_ctx_build_run_partial",
        "context_build_runs",
        ["status"],
        postgresql_where=sa.text("status IN ('failed','timeout','partial')"),
    )

    # § 3.2.3 — runs + decisions
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("tick_id", sa.String(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("failure_stage", sa.String(), nullable=True),
        sa.Column("last_completed_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_template_hash", sa.String(), nullable=False),
        sa.Column("rendered_prompt_hash", sa.String(), nullable=False),
        sa.Column("rendered_prompt_text", sa.Text(), nullable=True),
        sa.Column("context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("git_commit_sha", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running','success','partial','failed','timeout','missed','skipped')",
            name="chk_run_status",
        ),
        sa.CheckConstraint("retry_count >= 0", name="chk_run_retry_count_ge0"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(["prompt_template_hash"], ["prompt_templates.sha256_hash"]),
        sa.ForeignKeyConstraint(["context_snapshot_id"], ["context_snapshots.id"]),
        sa.ForeignKeyConstraint(
            ["context_snapshot_id", "experiment_id", "tick_id"],
            [
                "context_snapshots.id",
                "context_snapshots.experiment_id",
                "context_snapshots.tick_id",
            ],
            name="fk_runs_ctx_snap_composite",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_id", "model_id", "scheduled_for", name="uq_runs_exp_model_sched"
        ),
    )
    op.create_index(
        "idx_runs_experiment_model_time",
        "runs",
        ["experiment_id", "model_id", "scheduled_for"],
    )
    op.create_index("idx_runs_tick", "runs", ["tick_id"])

    op.create_table(
        "llm_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("provider_snapshot", sa.String(), nullable=False),
        sa.Column("model_name_api_snapshot", sa.String(), nullable=False),
        sa.Column("temperature", sa.Numeric(4, 3), nullable=True),
        sa.Column("top_p", sa.Numeric(4, 3), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("llm_config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "temperature IS NULL OR temperature >= 0", name="chk_llm_inv_temperature_ge0"
        ),
        sa.CheckConstraint(
            "top_p IS NULL OR (top_p > 0 AND top_p <= 1)", name="chk_llm_inv_top_p_range"
        ),
        sa.CheckConstraint(
            "max_tokens IS NULL OR max_tokens > 0", name="chk_llm_inv_max_tokens_gt0"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_response_id", sa.String(), nullable=True),
        sa.Column("portfolio_reasoning", sa.Text(), nullable=False),
        sa.Column("risk_assessment", sa.Text(), nullable=False),
        sa.Column("portfolio_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("latency_ms >= 0", name="chk_decision_latency_ms_ge0"),
        sa.CheckConstraint(
            "portfolio_confidence IS NULL OR (portfolio_confidence BETWEEN 0 AND 1)",
            name="chk_decision_portfolio_confidence_range",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_decisions_model_time", "decisions", ["model_id", "decided_at"])
    op.create_index("idx_decisions_experiment", "decisions", ["experiment_id", "decided_at"])

    op.create_table(
        "decision_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("time_horizon_min", sa.Integer(), nullable=False),
        sa.Column("action_reasoning", sa.Text(), nullable=False),
        sa.Column(
            "action_key_signals",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("jsonb_build_array()"),
        ),
        sa.Column("side_requested", sa.String(), nullable=False),
        sa.Column("leverage_requested", sa.Numeric(5, 2), nullable=False),
        sa.Column("size_pct_requested", sa.Numeric(5, 4), nullable=False),
        sa.Column("stop_loss_pct", sa.Numeric(5, 4), nullable=True),
        sa.Column("take_profit_pct", sa.Numeric(5, 4), nullable=True),
        sa.Column("entry_type", sa.String(), nullable=False),
        sa.Column("limit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("side_executed", sa.String(), nullable=False),
        sa.Column("leverage_executed", sa.Numeric(5, 2), nullable=False),
        sa.Column("size_pct_executed", sa.Numeric(5, 4), nullable=False),
        sa.Column("leverage_clamped", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("size_pct_clamped", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("forced_hold", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("original_side", sa.String(), nullable=True),
        sa.Column("execution_status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("executed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("execution_error", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="chk_action_confidence_range"),
        sa.CheckConstraint("time_horizon_min > 0", name="chk_action_time_horizon_gt0"),
        sa.CheckConstraint(
            "side_requested IN ('LONG','SHORT','FLAT','HOLD')",
            name="chk_action_side_requested",
        ),
        sa.CheckConstraint("leverage_requested >= 0", name="chk_action_leverage_requested_ge0"),
        sa.CheckConstraint(
            "size_pct_requested >= 0 AND size_pct_requested <= 1",
            name="chk_action_size_pct_requested_range",
        ),
        sa.CheckConstraint(
            "stop_loss_pct IS NULL OR stop_loss_pct > 0", name="chk_action_sl_pct_gt0"
        ),
        sa.CheckConstraint(
            "take_profit_pct IS NULL OR take_profit_pct > 0", name="chk_action_tp_pct_gt0"
        ),
        sa.CheckConstraint("entry_type IN ('market','limit','none')", name="chk_action_entry_type"),
        sa.CheckConstraint(
            "limit_price IS NULL OR limit_price > 0", name="chk_action_limit_price_gt0"
        ),
        sa.CheckConstraint(
            "side_executed IN ('LONG','SHORT','FLAT','HOLD')",
            name="chk_action_side_executed",
        ),
        sa.CheckConstraint("leverage_executed >= 0", name="chk_action_leverage_executed_ge0"),
        sa.CheckConstraint(
            "size_pct_executed >= 0 AND size_pct_executed <= 1",
            name="chk_action_size_pct_executed_range",
        ),
        sa.CheckConstraint(
            "execution_status IN "
            "('not_applicable','pending','filled','partial','failed','cancelled')",
            name="chk_action_execution_status",
        ),
        sa.CheckConstraint(
            "side_requested NOT IN ('HOLD','FLAT') OR "
            "(size_pct_requested = 0 AND leverage_requested = 0 AND entry_type = 'none' "
            "AND stop_loss_pct IS NULL AND take_profit_pct IS NULL)",
            name="chk_hold_flat_no_sizing",
        ),
        sa.CheckConstraint(
            "side_requested NOT IN ('LONG','SHORT') OR "
            "(size_pct_requested > 0 AND leverage_requested >= 1 "
            "AND entry_type IN ('market','limit') "
            "AND stop_loss_pct IS NOT NULL AND take_profit_pct IS NOT NULL)",
            name="chk_open_close_has_sizing",
        ),
        sa.CheckConstraint(
            "entry_type != 'limit' OR limit_price IS NOT NULL",
            name="chk_limit_requires_price",
        ),
        sa.CheckConstraint(
            "entry_type NOT IN ('market','none') OR limit_price IS NULL",
            name="chk_market_no_limit_price",
        ),
        sa.ForeignKeyConstraint(["decision_id"], ["decisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", "symbol", name="uniq_action_decision_symbol"),
    )
    op.create_index("idx_actions_symbol_time", "decision_actions", ["symbol", "created_at"])
    op.create_index("idx_actions_model_time", "decision_actions", ["model_id", "created_at"])

    # § 3.2.4 — wallet/positions
    op.create_table(
        "account_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("available_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("margin_used_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("n_open_positions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "total_position_value_usd",
            sa.Numeric(20, 8),
            nullable=False,
            server_default="0",
        ),
        sa.Column("unrealized_pnl_usd", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("portfolio_state_hash", sa.String(), nullable=False),
        sa.Column("raw_account_state", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("equity_usd >= 0", name="chk_acc_snap_equity_ge0"),
        sa.CheckConstraint("available_usd >= 0", name="chk_acc_snap_available_ge0"),
        sa.CheckConstraint("margin_used_usd >= 0", name="chk_acc_snap_margin_used_ge0"),
        sa.CheckConstraint("n_open_positions >= 0", name="chk_acc_snap_n_open_pos_ge0"),
        sa.CheckConstraint(
            "total_position_value_usd >= 0", name="chk_acc_snap_total_pos_value_ge0"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_snapshots_model_time", "account_snapshots", ["model_id", "snapshot_at"])

    op.create_table(
        "positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("opening_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("opening_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("size_units", sa.Numeric(20, 8), nullable=False),
        sa.Column("leverage", sa.Numeric(5, 2), nullable=False),
        sa.Column("notional_value_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("initial_margin_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("stop_loss_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("take_profit_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("closing_action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("close_reason", sa.String(), nullable=True),
        sa.Column("realized_pnl_usd", sa.Numeric(20, 8), nullable=True),
        sa.Column("hl_position_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("side IN ('LONG','SHORT')", name="chk_position_side"),
        sa.CheckConstraint("entry_price > 0", name="chk_position_entry_price_gt0"),
        sa.CheckConstraint("size_units > 0", name="chk_position_size_units_gt0"),
        sa.CheckConstraint("leverage >= 1", name="chk_position_leverage_ge1"),
        sa.CheckConstraint("notional_value_usd > 0", name="chk_position_notional_gt0"),
        sa.CheckConstraint("initial_margin_usd > 0", name="chk_position_initial_margin_gt0"),
        sa.CheckConstraint("stop_loss_price > 0", name="chk_position_sl_price_gt0"),
        sa.CheckConstraint("take_profit_price > 0", name="chk_position_tp_price_gt0"),
        sa.CheckConstraint(
            "exit_price IS NULL OR exit_price > 0", name="chk_position_exit_price_gt0"
        ),
        sa.CheckConstraint(
            "close_reason IN ('manual','stop_loss','take_profit','liquidated','model_close') "
            "OR close_reason IS NULL",
            name="chk_position_close_reason",
        ),
        sa.CheckConstraint(
            "(closed_at IS NULL AND exit_price IS NULL AND realized_pnl_usd IS NULL "
            "AND close_reason IS NULL AND closing_action_id IS NULL) OR "
            "(closed_at IS NOT NULL AND exit_price IS NOT NULL "
            "AND realized_pnl_usd IS NOT NULL AND close_reason IS NOT NULL)",
            name="chk_position_closed_consistency",
        ),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(["opening_run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["opening_action_id"], ["decision_actions.id"]),
        sa.ForeignKeyConstraint(["closing_action_id"], ["decision_actions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uniq_positions_opening_action", "positions", ["opening_action_id"], unique=True
    )
    op.create_index(
        "idx_positions_model_open",
        "positions",
        ["model_id", "closed_at"],
        postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.create_index("idx_positions_model_symbol", "positions", ["model_id", "symbol", "opened_at"])

    # § 3.2.5 — orders
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("order_kind", sa.String(), nullable=False),
        sa.Column("hl_order_id", sa.String(), nullable=True),
        sa.Column("client_order_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("requested_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("filled_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("requested_size_units", sa.Numeric(20, 8), nullable=False),
        sa.Column("filled_size_units", sa.Numeric(20, 8), nullable=True),
        sa.Column("slippage_bps", sa.Numeric(10, 4), nullable=True),
        sa.Column("raw_order_response", postgresql.JSONB(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "order_kind IN ('entry','stop_loss','take_profit','close')",
            name="chk_order_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending','filled','partial','cancelled','rejected','triggered')",
            name="chk_order_status",
        ),
        sa.CheckConstraint(
            "requested_price IS NULL OR requested_price > 0",
            name="chk_order_requested_price_gt0",
        ),
        sa.CheckConstraint(
            "filled_price IS NULL OR filled_price > 0", name="chk_order_filled_price_gt0"
        ),
        sa.CheckConstraint("requested_size_units > 0", name="chk_order_requested_size_gt0"),
        sa.CheckConstraint(
            "filled_size_units IS NULL OR filled_size_units >= 0",
            name="chk_order_filled_size_ge0",
        ),
        sa.ForeignKeyConstraint(["decision_action_id"], ["decision_actions.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_orders_action", "orders", ["decision_action_id"])
    op.create_index("idx_orders_model_time", "orders", ["model_id", "submitted_at"])
    op.create_index(
        "idx_orders_status",
        "orders",
        ["status"],
        postgresql_where=sa.text("status IN ('pending','partial')"),
    )

    # § 3.2.6 — ledger costi
    op.create_table(
        "fee_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fee_type", sa.String(), nullable=False),
        sa.Column("fee_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "fee_type IN ('taker_open','taker_close','maker_open','maker_close')",
            name="chk_fee_event_fee_type",
        ),
        sa.CheckConstraint("fee_usd >= 0", name="chk_fee_event_fee_usd_ge0"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_fee_events_model_time", "fee_events", ["model_id", "occurred_at"])

    op.create_table(
        "funding_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("funding_rate", sa.Numeric(10, 8), nullable=False),
        sa.Column("funding_amount_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("funding_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("funding_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "funding_period_end > funding_period_start",
            name="chk_funding_period_end_gt_start",
        ),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_funding_model_time", "funding_events", ["model_id", "funding_period_end"])

    op.create_table(
        "cost_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cost_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("pricing_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("input_tokens >= 0", name="chk_cost_event_input_tokens_ge0"),
        sa.CheckConstraint("output_tokens >= 0", name="chk_cost_event_output_tokens_ge0"),
        sa.CheckConstraint("reasoning_tokens >= 0", name="chk_cost_event_reasoning_tokens_ge0"),
        sa.CheckConstraint("n_attempts >= 1", name="chk_cost_event_n_attempts_ge1"),
        sa.CheckConstraint("cost_usd >= 0", name="chk_cost_event_cost_usd_ge0"),
        sa.ForeignKeyConstraint(["decision_id"], ["decisions.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_cost_model_time", "cost_events", ["model_id", "created_at"])

    op.create_table(
        "tax_sim_periods",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("quarter_label", sa.String(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_pnl_gross_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("total_fees_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("total_funding_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("taxable_base_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("tax_rate_pct", sa.Numeric(5, 4), nullable=False, server_default="0.26"),
        sa.Column("tax_due_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("n_positions_closed", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("period_end > period_start", name="chk_tax_sim_period_end_gt_start"),
        sa.CheckConstraint("total_fees_usd >= 0", name="chk_tax_sim_fees_ge0"),
        sa.CheckConstraint(
            "tax_rate_pct >= 0 AND tax_rate_pct <= 1", name="chk_tax_sim_rate_range"
        ),
        sa.CheckConstraint("tax_due_usd >= 0", name="chk_tax_sim_due_ge0"),
        sa.CheckConstraint("n_positions_closed >= 0", name="chk_tax_sim_n_positions_ge0"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_id", "model_id", "quarter_label", name="uq_tax_sim_exp_model_quarter"
        ),
    )
    op.create_index("idx_tax_model", "tax_sim_periods", ["model_id"])

    # § 3.2.7 — outcomes
    op.create_table(
        "outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("opening_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opening_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("closing_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("realized_pnl_gross_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("sum_fees_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("sum_funding_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("pnl_net_fee_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("pnl_net_fee_funding_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("pnl_net_fee_funding_tax_sim_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("was_profitable_net", sa.Boolean(), nullable=False),
        sa.Column("holding_duration_min", sa.Integer(), nullable=False),
        sa.Column("decision_action_confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("decision_action_time_horizon_min", sa.Integer(), nullable=False),
        sa.Column("horizon_met", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sum_fees_usd >= 0", name="chk_outcome_sum_fees_ge0"),
        sa.CheckConstraint("holding_duration_min >= 0", name="chk_outcome_holding_duration_ge0"),
        sa.CheckConstraint(
            "decision_action_confidence BETWEEN 0 AND 1",
            name="chk_outcome_confidence_range",
        ),
        sa.CheckConstraint(
            "decision_action_time_horizon_min > 0",
            name="chk_outcome_time_horizon_gt0",
        ),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"]),
        sa.ForeignKeyConstraint(["opening_action_id"], ["decision_actions.id"]),
        sa.ForeignKeyConstraint(["opening_run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["closing_run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_outcomes_model_time", "outcomes", ["model_id", "created_at"])
    op.create_index(
        "idx_outcomes_confidence", "outcomes", ["model_id", "decision_action_confidence"]
    )
    op.create_index("idx_outcomes_action", "outcomes", ["opening_action_id"])

    # § 3.2.8 — baseline
    op.create_table(
        "baseline_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("baseline_name", sa.String(), nullable=False),
        sa.Column("config_json", postgresql.JSONB(), nullable=False),
        sa.Column("config_hash", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "baseline_name IN ('buy_and_hold','cash','naive_momentum_ema_20_50')",
            name="chk_baseline_config_name",
        ),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_id", "baseline_name", name="uq_baseline_config_exp_name"),
    )

    op.create_table(
        "baseline_equity_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("baseline_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("baseline_name", sa.String(), nullable=False),
        sa.Column("tick_id", sa.String(), nullable=False),
        sa.Column("tick_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("pnl_usd_cumulative", sa.Numeric(20, 8), nullable=False),
        sa.Column("raw_state", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "baseline_name IN ('buy_and_hold','cash','naive_momentum_ema_20_50')",
            name="chk_baseline_equity_snap_name",
        ),
        sa.CheckConstraint("equity_usd >= 0", name="chk_baseline_equity_snap_equity_ge0"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["baseline_config_id"], ["baseline_configs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_id",
            "baseline_name",
            "tick_id",
            name="uq_baseline_equity_snap_exp_name_tick",
        ),
    )
    op.create_index(
        "idx_baseline_name_time", "baseline_equity_snapshots", ["baseline_name", "tick_at"]
    )

    # § 3.2.9 — errors
    op.create_table(
        "errors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("error_kind", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("stack_trace", sa.Text(), nullable=True),
        sa.Column("context", postgresql.JSONB(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["decision_id"], ["decisions.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_errors_model_time", "errors", ["model_id", "occurred_at"])
    op.create_index("idx_errors_kind", "errors", ["error_kind", "occurred_at"])


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("errors")
    op.drop_table("baseline_equity_snapshots")
    op.drop_table("baseline_configs")
    op.drop_table("outcomes")
    op.drop_table("tax_sim_periods")
    op.drop_table("cost_events")
    op.drop_table("funding_events")
    op.drop_table("fee_events")
    op.drop_table("orders")
    op.drop_table("positions")
    op.drop_table("account_snapshots")
    op.drop_table("decision_actions")
    op.drop_table("decisions")
    op.drop_table("llm_invocations")
    op.drop_table("runs")
    op.drop_table("context_build_runs")
    op.drop_table("context_snapshots")
    op.drop_table("prompt_templates")
    op.drop_table("models")
    op.drop_table("experiments")
