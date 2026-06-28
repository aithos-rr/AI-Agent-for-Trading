"""Drop vestigial positions.hl_position_id (ADR-0016, confirmed by M4-T08).

Revision ID: 003
Revises: 002
Create Date: 2026-06-28

M4-T08 validated on real testnet that position identity = coin symbol
(check_position_closure resolves closures by symbol against the live SDK). The
positions.hl_position_id column was never populated with a real id and is now
removed. Re-adding it (String, nullable) is a trivial migration should a future
venue ever provide a stable position id.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("positions", "hl_position_id")


def downgrade() -> None:
    op.add_column(
        "positions",
        sa.Column("hl_position_id", sa.String(), nullable=True),
    )
