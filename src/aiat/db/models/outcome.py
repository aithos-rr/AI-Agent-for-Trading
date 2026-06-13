"""SQLAlchemy model for `outcomes` (§3.2.7)."""

import uuid
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class Outcome(TimestampMixin, Base):
    """Closed position outcome; links confidence to realized PnL for Brier scoring (§3.2.7)."""

    __tablename__ = "outcomes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id"), nullable=False, unique=True
    )
    opening_action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_actions.id"), nullable=False
    )
    opening_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False
    )
    closing_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String, ForeignKey("models.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    realized_pnl_gross_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    sum_fees_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    sum_funding_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    pnl_net_fee_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    pnl_net_fee_funding_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    pnl_net_fee_funding_tax_sim_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    was_profitable_net: Mapped[bool] = mapped_column(Boolean, nullable=False)
    holding_duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_action_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    decision_action_time_horizon_min: Mapped[int] = mapped_column(Integer, nullable=False)
    horizon_met: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        CheckConstraint("sum_fees_usd >= 0", name="chk_outcome_sum_fees_ge0"),
        CheckConstraint("holding_duration_min >= 0", name="chk_outcome_holding_duration_ge0"),
        CheckConstraint(
            "decision_action_confidence BETWEEN 0 AND 1",
            name="chk_outcome_confidence_range",
        ),
        CheckConstraint(
            "decision_action_time_horizon_min > 0",
            name="chk_outcome_time_horizon_gt0",
        ),
        Index("idx_outcomes_model_time", "model_id", "created_at"),
        Index("idx_outcomes_confidence", "model_id", "decision_action_confidence"),
        Index("idx_outcomes_action", "opening_action_id"),
    )
