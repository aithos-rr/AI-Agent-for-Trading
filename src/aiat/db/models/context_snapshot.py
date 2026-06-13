"""SQLAlchemy model for `context_snapshots` (§3.2.2)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class ContextSnapshot(TimestampMixin, Base):
    __tablename__ = "context_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tick_id: Mapped[str] = mapped_column(String, nullable=False)
    tick_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    context_hash: Mapped[str] = mapped_column(String, nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_timestamps: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    build_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "build_duration_ms >= 0", name="chk_ctx_snap_build_duration_ge0"
        ),
        UniqueConstraint(
            "experiment_id", "tick_id", name="uq_context_snapshots_exp_tick"
        ),
        UniqueConstraint(
            "id", "experiment_id", "tick_id", name="uq_context_snapshots_id_exp_tick"
        ),
        Index("idx_context_tick", "tick_at", postgresql_ops={"tick_at": "DESC"}),
    )
