"""SQLAlchemy model for `context_build_runs` (§3.2.2)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class ContextBuildRun(TimestampMixin, Base):
    __tablename__ = "context_build_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tick_id: Mapped[str] = mapped_column(String, nullable=False)
    tick_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    failure_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    error_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("context_snapshots.id"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('running','success','partial','failed','timeout')",
            name="chk_ctx_build_run_status",
        ),
        UniqueConstraint("experiment_id", "tick_id", name="uq_context_build_runs_exp_tick"),
        Index("idx_context_build_runs_tick", "tick_at", postgresql_ops={"tick_at": "DESC"}),
        Index(
            "idx_context_build_runs_status",
            "status",
            postgresql_where="status IN ('failed','timeout','partial')",
        ),
    )
