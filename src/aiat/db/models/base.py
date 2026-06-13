"""SQLAlchemy 2.x declarative base and common mixins (§3.2, §1.2)."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Project-wide declarative base."""


class TimestampMixin:
    """Adds `created_at` (server default now()) to a model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
