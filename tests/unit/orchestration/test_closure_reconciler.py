"""Unit tests for ClosureReconciler pure logic (ADR-0038).

Covers the per-side ``close_reason`` attribution heuristic (moved unchanged from decision_loop,
ADR-0030) and the DB-driven paths of ``reconcile`` that don't need a real Postgres: the empty
plan (no open positions) short-circuit and per-model error isolation. The full DB-backed scenarios
(a)/(b)/(c)/(d) live in ``tests/integration/test_closure_reconciler.py``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aiat.domain.enums import CloseReason
from aiat.execution.hyperliquid_client import PositionClosureInfo
from aiat.orchestration.closure_reconciler import ClosureReconciler, _attribute_close_reason

RUN_ID = str(uuid.uuid4())
EXPERIMENT_ID = str(uuid.uuid4())


def _pos(side: str, entry_price: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), symbol="BTC", side=side, entry_price=Decimal(entry_price)
    )


def _closure(exit_price: str, close_reason: CloseReason) -> PositionClosureInfo:
    return PositionClosureInfo(
        closed_at="2026-06-14T15:00:00+00:00",
        exit_price=Decimal(exit_price),
        close_reason=close_reason,
        realized_pnl_usd=Decimal("0"),
    )


class TestAttributeCloseReason:
    """Per-side SL/TP attribution for autonomous closures (ADR-0030, moved by ADR-0038).

    The heuristic keys off the side of exit_price relative to entry_price: for a LONG the SL sits
    strictly below entry and the TP strictly above (SHORT inverted); a liquidation (flagged on the
    fill) takes priority over the SL/TP heuristic.
    """

    def test_long_exit_below_entry_is_stop_loss(self) -> None:
        reason = _attribute_close_reason(
            _pos("LONG", "100"), _closure("95", CloseReason.MODEL_CLOSE), RUN_ID
        )
        assert reason == CloseReason.STOP_LOSS

    def test_long_exit_above_entry_is_take_profit(self) -> None:
        reason = _attribute_close_reason(
            _pos("LONG", "100"), _closure("105", CloseReason.MODEL_CLOSE), RUN_ID
        )
        assert reason == CloseReason.TAKE_PROFIT

    def test_short_exit_above_entry_is_stop_loss(self) -> None:
        # SHORT SL sits ABOVE entry (inverted from LONG).
        reason = _attribute_close_reason(
            _pos("SHORT", "100"), _closure("105", CloseReason.MODEL_CLOSE), RUN_ID
        )
        assert reason == CloseReason.STOP_LOSS

    def test_short_exit_below_entry_is_take_profit(self) -> None:
        reason = _attribute_close_reason(
            _pos("SHORT", "100"), _closure("95", CloseReason.MODEL_CLOSE), RUN_ID
        )
        assert reason == CloseReason.TAKE_PROFIT

    def test_liquidation_has_priority_over_side_heuristic(self) -> None:
        # A LONG liquidation fills below entry (would look like SL by side), but the liquidation
        # flag wins and the heuristic is not applied.
        reason = _attribute_close_reason(
            _pos("LONG", "100"), _closure("90", CloseReason.LIQUIDATED), RUN_ID
        )
        assert reason == CloseReason.LIQUIDATED

    def test_long_exit_equals_entry_resolves_to_stop_loss(self) -> None:
        # Structurally impossible for a real trigger; tie resolves deterministically to SL.
        reason = _attribute_close_reason(
            _pos("LONG", "100"), _closure("100", CloseReason.MODEL_CLOSE), RUN_ID
        )
        assert reason == CloseReason.STOP_LOSS

    def test_short_exit_equals_entry_resolves_to_stop_loss(self) -> None:
        reason = _attribute_close_reason(
            _pos("SHORT", "100"), _closure("100", CloseReason.MODEL_CLOSE), RUN_ID
        )
        assert reason == CloseReason.STOP_LOSS


def _factory_yielding(session: object) -> MagicMock:
    """A session_factory MagicMock whose ``async with factory()`` yields ``session``."""
    factory = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = cm
    return factory


class TestReconcileNoOpenPositions:
    @pytest.mark.asyncio
    async def test_empty_plan_visits_no_models_and_never_fetches_fills(self) -> None:
        """No open positions → the plan is empty, the fills source is never called."""
        session = AsyncMock()
        # _models_with_open_positions runs `select(...).all()` → no rows.
        empty = MagicMock()
        empty.all.return_value = []
        session.execute = AsyncMock(return_value=empty)
        source = AsyncMock()

        result = await ClosureReconciler(
            _factory_yielding(session), source, EXPERIMENT_ID
        ).reconcile(1_700_000_000_000)

        assert result.closed == 0 and result.models == 0
        source.user_fills_by_time.assert_not_awaited()
