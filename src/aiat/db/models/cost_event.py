"""SQLAlchemy model for `cost_events` (§3.2.6)."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class CostEvent(TimestampMixin, Base):
    """LLM API cost for one decision; persisted atomically after decisions (inv #4, §3.2.6)."""

    __tablename__ = "cost_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decisions.id"), nullable=False, unique=True
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String, ForeignKey("models.id"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False
    )
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    n_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    pricing_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint("input_tokens >= 0", name="chk_cost_event_input_tokens_ge0"),
        CheckConstraint("output_tokens >= 0", name="chk_cost_event_output_tokens_ge0"),
        CheckConstraint("reasoning_tokens >= 0", name="chk_cost_event_reasoning_tokens_ge0"),
        CheckConstraint("n_attempts >= 1", name="chk_cost_event_n_attempts_ge1"),
        CheckConstraint("cost_usd >= 0", name="chk_cost_event_cost_usd_ge0"),
        Index("idx_cost_model_time", "model_id", "created_at"),
    )
