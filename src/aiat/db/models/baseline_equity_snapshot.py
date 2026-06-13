"""SQLAlchemy model for `baseline_equity_snapshots` (§3.2.8)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin

_BASELINE_NAMES = "'buy_and_hold','cash','naive_momentum_ema_20_50'"


class BaselineEquitySnapshot(TimestampMixin, Base):
    """Per-tick equity snapshot for a non-LLM baseline (§3.2.8)."""

    __tablename__ = "baseline_equity_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    baseline_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("baseline_configs.id"), nullable=False
    )
    baseline_name: Mapped[str] = mapped_column(String, nullable=False)
    tick_id: Mapped[str] = mapped_column(String, nullable=False)
    tick_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equity_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    pnl_usd_cumulative: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    raw_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "baseline_name",
            "tick_id",
            name="uq_baseline_equity_snap_exp_name_tick",
        ),
        CheckConstraint(
            f"baseline_name IN ({_BASELINE_NAMES})",
            name="chk_baseline_equity_snap_name",
        ),
        CheckConstraint("equity_usd >= 0", name="chk_baseline_equity_snap_equity_ge0"),
        Index("idx_baseline_name_time", "baseline_name", "tick_at"),
    )
