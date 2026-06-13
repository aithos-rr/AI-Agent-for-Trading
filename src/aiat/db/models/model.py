"""SQLAlchemy model for `models` (§3.2.1)."""

from decimal import Decimal

from sqlalchemy import CheckConstraint, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from aiat.db.models.base import Base, TimestampMixin


class Model(TimestampMixin, Base):
    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model_name_api: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    geography: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    wallet_address: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    pricing_input_usd_per_1m: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False
    )
    pricing_output_usd_per_1m: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False
    )
    pricing_reasoning_usd_per_1m: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default="0"
    )

    __table_args__ = (
        CheckConstraint("tier IN ('premium','cheap_alt')", name="chk_model_tier"),
        CheckConstraint("geography IN ('USA','CN')", name="chk_model_geography"),
        CheckConstraint(
            "pricing_input_usd_per_1m >= 0", name="chk_model_pricing_input_ge0"
        ),
        CheckConstraint(
            "pricing_output_usd_per_1m >= 0", name="chk_model_pricing_output_ge0"
        ),
        CheckConstraint(
            "pricing_reasoning_usd_per_1m >= 0",
            name="chk_model_pricing_reasoning_ge0",
        ),
    )
