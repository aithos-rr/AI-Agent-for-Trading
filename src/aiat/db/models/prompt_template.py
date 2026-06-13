"""SQLAlchemy model for `prompt_templates` (§3.2.1)."""

from typing import Any

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class PromptTemplate(TimestampMixin, Base):
    __tablename__ = "prompt_templates"

    sha256_hash: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_def: Mapped[str] = mapped_column(Text, nullable=False)
    controlled_signals: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
