"""SQLAlchemy model for `runs` (§3.2.3)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class Run(TimestampMixin, Base):
    """One run = one cron invocation per model per tick (§3.2.3)."""

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String, ForeignKey("models.id"), nullable=False)
    tick_id: Mapped[str] = mapped_column(String, nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    run_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    run_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    failure_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    last_completed_step: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    prompt_template_hash: Mapped[str] = mapped_column(
        String, ForeignKey("prompt_templates.sha256_hash"), nullable=False
    )
    rendered_prompt_hash: Mapped[str] = mapped_column(String, nullable=False)
    rendered_prompt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_snapshots.id"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    git_commit_sha: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "model_id", "scheduled_for", name="uq_runs_exp_model_sched"
        ),
        # Composite FK: ensures context_snapshot_id references a snapshot for the same
        # experiment_id and tick_id declared by this run (fix B.3 review-v2).
        ForeignKeyConstraint(
            ["context_snapshot_id", "experiment_id", "tick_id"],
            [
                "context_snapshots.id",
                "context_snapshots.experiment_id",
                "context_snapshots.tick_id",
            ],
            name="fk_runs_ctx_snap_composite",
        ),
        CheckConstraint(
            "status IN ('running','success','partial','failed','timeout','missed','skipped')",
            name="chk_run_status",
        ),
        CheckConstraint("retry_count >= 0", name="chk_run_retry_count_ge0"),
        Index("idx_runs_experiment_model_time", "experiment_id", "model_id", "scheduled_for"),
        Index("idx_runs_tick", "tick_id"),
    )
