"""SQLAlchemy model for `baseline_configs` (§3.2.8)."""

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin

_BASELINE_NAMES = "'buy_and_hold','cash','naive_momentum_ema_20_50'"


class BaselineConfig(TimestampMixin, Base):
    """Pre-registered non-LLM baseline configuration with config_hash (§3.2.8)."""

    __tablename__ = "baseline_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    baseline_name: Mapped[str] = mapped_column(String, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    config_hash: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("experiment_id", "baseline_name", name="uq_baseline_config_exp_name"),
        CheckConstraint(
            f"baseline_name IN ({_BASELINE_NAMES})",
            name="chk_baseline_config_name",
        ),
    )
