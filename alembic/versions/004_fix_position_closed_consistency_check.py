"""Fix chk_position_closed_consistency: condition closing_action_id on close_reason.

Revision ID: 004
Revises: 003
Create Date: 2026-07-06

ADR-0027 fix (c) + ADR-0030 Problema 1 (revised IN-PLACE — migration 004 not yet
pushed). The pre-004 closed branch verified closed_at / exit_price / realized_pnl_usd /
close_reason but NOT closing_action_id, so a closed position could pass with
closing_action_id NULL (observed on SOL in M5-T14, breaking decision->closure
traceability). ADR-0027 first added an unconditional `closing_action_id IS NOT NULL`;
ADR-0030 refined it: autonomous closures (SL/TP trigger, liquidation) are executed by
the exchange with NO model decision_action, so they legitimately have closing_action_id
NULL. The closed branch is therefore CONDITIONAL on close_reason:
  - model_close                          -> closing_action_id IS NOT NULL (model FLAT decided)
  - stop_loss / take_profit / liquidated -> closing_action_id IS NULL     (autonomous)
('manual' is produced by no automatic path and is intentionally NOT admitted on the
closed branch — see ADR-0030.)

Precondition (scelta A, ADR-0027): the DB must have NO orphan closed positions (closed
under model_close with closing_action_id NULL) — the recreate would fail otherwise. The
test DB must be reseeded before applying this migration.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "chk_position_closed_consistency"

_NEW_CHECK = (
    "(closed_at IS NULL AND exit_price IS NULL AND realized_pnl_usd IS NULL "
    "AND close_reason IS NULL AND closing_action_id IS NULL) OR "
    "(closed_at IS NOT NULL AND exit_price IS NOT NULL AND realized_pnl_usd IS NOT NULL "
    "AND close_reason IS NOT NULL AND ("
    "(close_reason = 'model_close' AND closing_action_id IS NOT NULL) OR "
    "(close_reason IN ('stop_loss','take_profit','liquidated') AND closing_action_id IS NULL)"
    "))"
)

_OLD_CHECK = (
    "(closed_at IS NULL AND exit_price IS NULL AND realized_pnl_usd IS NULL "
    "AND close_reason IS NULL AND closing_action_id IS NULL) OR "
    "(closed_at IS NOT NULL AND exit_price IS NOT NULL AND realized_pnl_usd IS NOT NULL "
    "AND close_reason IS NOT NULL)"
)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "positions", type_="check")
    op.create_check_constraint(_CONSTRAINT, "positions", _NEW_CHECK)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "positions", type_="check")
    op.create_check_constraint(_CONSTRAINT, "positions", _OLD_CHECK)
