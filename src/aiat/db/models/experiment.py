"""SQLAlchemy model for `experiments` (§3.2.1)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class Experiment(TimestampMixin, Base):
    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    git_commit_sha: Mapped[str] = mapped_column(String, nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
