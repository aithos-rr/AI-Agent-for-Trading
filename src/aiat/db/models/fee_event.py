"""SQLAlchemy model for `fee_events` (§3.2.6)."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class FeeEvent(TimestampMixin, Base):
    """Trading fee incurred on order fill (§3.2.6)."""

    __tablename__ = "fee_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id"), nullable=False
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String, ForeignKey("models.id"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False
    )
    fee_type: Mapped[str] = mapped_column(String, nullable=False)
    fee_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "fee_type IN ('taker_open','taker_close','maker_open','maker_close')",
            name="chk_fee_event_fee_type",
        ),
        CheckConstraint("fee_usd >= 0", name="chk_fee_event_fee_usd_ge0"),
        Index("idx_fee_events_model_time", "model_id", "occurred_at"),
    )
