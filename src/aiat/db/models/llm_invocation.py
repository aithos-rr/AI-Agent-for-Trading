"""SQLAlchemy model for `llm_invocations` (§3.2.3)."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class LLMInvocation(TimestampMixin, Base):
    """Snapshot of LLM nuisance variables per run (§3.2.3)."""

    __tablename__ = "llm_invocations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False, unique=True
    )
    model_id: Mapped[str] = mapped_column(String, ForeignKey("models.id"), nullable=False)
    provider_snapshot: Mapped[str] = mapped_column(String, nullable=False)
    model_name_api_snapshot: Mapped[str] = mapped_column(String, nullable=False)
    temperature: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    top_p: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "temperature IS NULL OR temperature >= 0", name="chk_llm_inv_temperature_ge0"
        ),
        CheckConstraint(
            "top_p IS NULL OR (top_p > 0 AND top_p <= 1)", name="chk_llm_inv_top_p_range"
        ),
        CheckConstraint("max_tokens IS NULL OR max_tokens > 0", name="chk_llm_inv_max_tokens_gt0"),
    )
