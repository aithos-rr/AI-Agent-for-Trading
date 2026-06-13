"""SQLAlchemy model for `decisions` (§3.2.3)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class Decision(TimestampMixin, Base):
    """Structured model output for one run; portfolio-level decision (§3.2.3)."""

    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False, unique=True
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String, ForeignKey("models.id"), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_response_id: Mapped[str | None] = mapped_column(String, nullable=True)
    portfolio_reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    risk_assessment: Mapped[str] = mapped_column(Text, nullable=False)
    portfolio_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint("latency_ms >= 0", name="chk_decision_latency_ms_ge0"),
        CheckConstraint(
            "portfolio_confidence IS NULL OR (portfolio_confidence BETWEEN 0 AND 1)",
            name="chk_decision_portfolio_confidence_range",
        ),
        Index("idx_decisions_model_time", "model_id", "decided_at"),
        Index("idx_decisions_experiment", "experiment_id", "decided_at"),
    )
