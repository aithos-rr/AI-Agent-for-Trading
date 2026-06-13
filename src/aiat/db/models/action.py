"""SQLAlchemy model for `decision_actions` (§3.2.3)."""

import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class DecisionAction(TimestampMixin, Base):
    """Per-symbol action within a decision; carries action-level confidence/sizing (§3.2.3)."""

    __tablename__ = "decision_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String, ForeignKey("models.id"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    # action-level output
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    time_horizon_min: Mapped[int] = mapped_column(Integer, nullable=False)
    action_reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    action_key_signals: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("jsonb_build_array()")
    )
    # raw decision from model
    side_requested: Mapped[str] = mapped_column(String, nullable=False)
    leverage_requested: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    size_pct_requested: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    stop_loss_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    take_profit_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    entry_type: Mapped[str] = mapped_column(String, nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    # post-guardrail values
    side_executed: Mapped[str] = mapped_column(String, nullable=False)
    leverage_executed: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    size_pct_executed: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    # guardrail flags
    leverage_clamped: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    size_pct_clamped: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    forced_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    original_side: Mapped[str | None] = mapped_column(String, nullable=True)
    # execution result
    execution_status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    executed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    execution_error: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("decision_id", "symbol", name="uniq_action_decision_symbol"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="chk_action_confidence_range"),
        CheckConstraint("time_horizon_min > 0", name="chk_action_time_horizon_gt0"),
        CheckConstraint(
            "side_requested IN ('LONG','SHORT','FLAT','HOLD')",
            name="chk_action_side_requested",
        ),
        CheckConstraint("leverage_requested >= 0", name="chk_action_leverage_requested_ge0"),
        CheckConstraint(
            "size_pct_requested >= 0 AND size_pct_requested <= 1",
            name="chk_action_size_pct_requested_range",
        ),
        CheckConstraint("stop_loss_pct IS NULL OR stop_loss_pct > 0", name="chk_action_sl_pct_gt0"),
        CheckConstraint(
            "take_profit_pct IS NULL OR take_profit_pct > 0", name="chk_action_tp_pct_gt0"
        ),
        CheckConstraint("entry_type IN ('market','limit','none')", name="chk_action_entry_type"),
        CheckConstraint(
            "limit_price IS NULL OR limit_price > 0", name="chk_action_limit_price_gt0"
        ),
        CheckConstraint(
            "side_executed IN ('LONG','SHORT','FLAT','HOLD')",
            name="chk_action_side_executed",
        ),
        CheckConstraint("leverage_executed >= 0", name="chk_action_leverage_executed_ge0"),
        CheckConstraint(
            "size_pct_executed >= 0 AND size_pct_executed <= 1",
            name="chk_action_size_pct_executed_range",
        ),
        CheckConstraint(
            "execution_status IN "
            "('not_applicable','pending','filled','partial','failed','cancelled')",
            name="chk_action_execution_status",
        ),
        # Semantic CHECK constraints (fix punto 6)
        CheckConstraint(
            "side_requested NOT IN ('HOLD','FLAT') OR "
            "(size_pct_requested = 0 AND leverage_requested = 0 AND entry_type = 'none' "
            "AND stop_loss_pct IS NULL AND take_profit_pct IS NULL)",
            name="chk_hold_flat_no_sizing",
        ),
        CheckConstraint(
            "side_requested NOT IN ('LONG','SHORT') OR "
            "(size_pct_requested > 0 AND leverage_requested >= 1 "
            "AND entry_type IN ('market','limit') "
            "AND stop_loss_pct IS NOT NULL AND take_profit_pct IS NOT NULL)",
            name="chk_open_close_has_sizing",
        ),
        CheckConstraint(
            "entry_type != 'limit' OR limit_price IS NOT NULL",
            name="chk_limit_requires_price",
        ),
        CheckConstraint(
            "entry_type NOT IN ('market','none') OR limit_price IS NULL",
            name="chk_market_no_limit_price",
        ),
        Index("idx_actions_symbol_time", "symbol", "created_at"),
        Index("idx_actions_model_time", "model_id", "created_at"),
    )
