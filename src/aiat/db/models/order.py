"""SQLAlchemy model for `orders` (§3.2.5)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class Order(TimestampMixin, Base):
    """Individual order submitted to Hyperliquid for a decision action (§3.2.5)."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_actions.id"), nullable=False
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String, ForeignKey("models.id"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    order_kind: Mapped[str] = mapped_column(String, nullable=False)
    hl_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    requested_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    filled_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    requested_size_units: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    filled_size_units: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    slippage_bps: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    raw_order_response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "order_kind IN ('entry','stop_loss','take_profit','close')",
            name="chk_order_kind",
        ),
        CheckConstraint(
            "status IN ('pending','filled','partial','cancelled','rejected','triggered')",
            name="chk_order_status",
        ),
        CheckConstraint(
            "requested_price IS NULL OR requested_price > 0",
            name="chk_order_requested_price_gt0",
        ),
        CheckConstraint(
            "filled_price IS NULL OR filled_price > 0",
            name="chk_order_filled_price_gt0",
        ),
        CheckConstraint("requested_size_units > 0", name="chk_order_requested_size_gt0"),
        CheckConstraint(
            "filled_size_units IS NULL OR filled_size_units >= 0",
            name="chk_order_filled_size_ge0",
        ),
        Index("idx_orders_action", "decision_action_id"),
        Index("idx_orders_model_time", "model_id", "submitted_at"),
        Index(
            "idx_orders_status",
            "status",
            postgresql_where=text("status IN ('pending','partial')"),
        ),
    )
