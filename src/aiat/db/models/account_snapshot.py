"""SQLAlchemy model for `account_snapshots` (§3.2.4)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class AccountSnapshot(TimestampMixin, Base):
    """Per-run wallet state snapshot; carries portfolio_state_hash for audit (§3.2.4)."""

    __tablename__ = "account_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False, unique=True
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String, ForeignKey("models.id"), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equity_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    available_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    margin_used_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    n_open_positions: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_position_value_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, server_default="0"
    )
    unrealized_pnl_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, server_default="0"
    )
    portfolio_state_hash: Mapped[str] = mapped_column(String, nullable=False)
    raw_account_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint("equity_usd >= 0", name="chk_acc_snap_equity_ge0"),
        CheckConstraint("available_usd >= 0", name="chk_acc_snap_available_ge0"),
        CheckConstraint("margin_used_usd >= 0", name="chk_acc_snap_margin_used_ge0"),
        CheckConstraint("n_open_positions >= 0", name="chk_acc_snap_n_open_pos_ge0"),
        CheckConstraint("total_position_value_usd >= 0", name="chk_acc_snap_total_pos_value_ge0"),
        Index("idx_snapshots_model_time", "model_id", "snapshot_at"),
    )
