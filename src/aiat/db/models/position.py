"""SQLAlchemy model for `positions` (§3.2.4)."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class Position(TimestampMixin, Base):
    """Open/closed trade position with full lifecycle fields (§3.2.4)."""

    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String, ForeignKey("models.id"), nullable=False)
    opening_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    # opening fields
    opening_action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_actions.id"), nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    size_units: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    leverage: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    notional_value_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    initial_margin_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    stop_loss_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    take_profit_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    # closing fields (NULL while open)
    closing_action_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_actions.id"), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    realized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)

    __table_args__ = (
        CheckConstraint("side IN ('LONG','SHORT')", name="chk_position_side"),
        CheckConstraint("entry_price > 0", name="chk_position_entry_price_gt0"),
        CheckConstraint("size_units > 0", name="chk_position_size_units_gt0"),
        CheckConstraint("leverage >= 1", name="chk_position_leverage_ge1"),
        CheckConstraint("notional_value_usd > 0", name="chk_position_notional_gt0"),
        CheckConstraint("initial_margin_usd > 0", name="chk_position_initial_margin_gt0"),
        CheckConstraint("stop_loss_price > 0", name="chk_position_sl_price_gt0"),
        CheckConstraint("take_profit_price > 0", name="chk_position_tp_price_gt0"),
        CheckConstraint("exit_price IS NULL OR exit_price > 0", name="chk_position_exit_price_gt0"),
        CheckConstraint(
            "close_reason IN ('manual','stop_loss','take_profit','liquidated','model_close') "
            "OR close_reason IS NULL",
            name="chk_position_close_reason",
        ),
        # Ensures all closing fields are either all NULL (open) or all set (closed).
        # closing_action_id IS NOT NULL on the closed branch added by ADR-0027 fix (c)
        # (migration 004) to keep the model in sync with the DDL.
        CheckConstraint(
            "(closed_at IS NULL AND exit_price IS NULL AND realized_pnl_usd IS NULL "
            "AND close_reason IS NULL AND closing_action_id IS NULL) OR "
            "(closed_at IS NOT NULL AND exit_price IS NOT NULL AND realized_pnl_usd IS NOT NULL "
            "AND close_reason IS NOT NULL AND closing_action_id IS NOT NULL)",
            name="chk_position_closed_consistency",
        ),
        Index("uniq_positions_opening_action", "opening_action_id", unique=True),
        Index(
            "idx_positions_model_open",
            "model_id",
            "closed_at",
            postgresql_where=text("closed_at IS NULL"),
        ),
        Index("idx_positions_model_symbol", "model_id", "symbol", "opened_at"),
    )
