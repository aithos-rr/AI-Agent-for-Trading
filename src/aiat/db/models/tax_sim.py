"""SQLAlchemy model for `tax_sim_periods` (§3.2.6)."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class TaxSimPeriod(TimestampMixin, Base):
    """Quarterly Italian tax simulation for one model (§3.2.6, §4.3)."""

    __tablename__ = "tax_sim_periods"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String, ForeignKey("models.id"), nullable=False)
    quarter_label: Mapped[str] = mapped_column(String, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_pnl_gross_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    total_fees_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    total_funding_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    taxable_base_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    tax_rate_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default="0.26"
    )
    tax_due_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    n_positions_closed: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "model_id", "quarter_label", name="uq_tax_sim_exp_model_quarter"
        ),
        CheckConstraint("period_end > period_start", name="chk_tax_sim_period_end_gt_start"),
        CheckConstraint("total_fees_usd >= 0", name="chk_tax_sim_fees_ge0"),
        CheckConstraint("tax_rate_pct >= 0 AND tax_rate_pct <= 1", name="chk_tax_sim_rate_range"),
        CheckConstraint("tax_due_usd >= 0", name="chk_tax_sim_due_ge0"),
        CheckConstraint("n_positions_closed >= 0", name="chk_tax_sim_n_positions_ge0"),
        Index("idx_tax_model", "model_id"),
    )
