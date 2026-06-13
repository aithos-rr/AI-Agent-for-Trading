"""SQLAlchemy model for `funding_events` (§3.2.6)."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class FundingEvent(TimestampMixin, Base):
    """Periodic funding payment on an open position; no run_id (§3.2.6, §3.3)."""

    __tablename__ = "funding_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id"), nullable=False
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String, ForeignKey("models.id"), nullable=False)
    funding_rate: Mapped[Decimal] = mapped_column(Numeric(10, 8), nullable=False)
    funding_amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    funding_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    funding_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "funding_period_end > funding_period_start",
            name="chk_funding_period_end_gt_start",
        ),
        Index("idx_funding_model_time", "model_id", "funding_period_end"),
    )
