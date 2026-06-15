"""Unit tests for HyperliquidClient ABC and MockHyperliquidClient (§7.5)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from aiat.domain.enums import CloseReason, EntryType, OrderKind, Side
from aiat.domain.schemas import ActionDecision, OpenPositionSummary, PortfolioState
from aiat.execution.hyperliquid_client import (
    HyperliquidClient,
    MockHyperliquidClient,
    OrderResult,
    PositionClosureInfo,
)
from aiat.execution.sizing import compute_position_sizing

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _long_action(symbol: str = "BTC") -> ActionDecision:
    return ActionDecision(
        symbol=symbol,  # type: ignore[arg-type]
        side=Side.LONG,
        leverage=Decimal("3"),
        size_pct=Decimal("0.10"),
        stop_loss_pct=Decimal("0.05"),
        take_profit_pct=Decimal("0.10"),
        entry_type=EntryType.MARKET,
        limit_price=None,
        confidence=Decimal("0.70"),
        time_horizon_min=60,
        action_reasoning="Market bullish with strong support levels and RSI oversold.",
        action_key_signals=[],
    )


def _short_action(symbol: str = "ETH") -> ActionDecision:
    return ActionDecision(
        symbol=symbol,  # type: ignore[arg-type]
        side=Side.SHORT,
        leverage=Decimal("2"),
        size_pct=Decimal("0.05"),
        stop_loss_pct=Decimal("0.03"),
        take_profit_pct=Decimal("0.08"),
        entry_type=EntryType.MARKET,
        limit_price=None,
        confidence=Decimal("0.65"),
        time_horizon_min=120,
        action_reasoning="Bearish divergence on MACD with funding rate extreme positive.",
        action_key_signals=[],
    )


def _hold_action(symbol: str = "BTC") -> ActionDecision:
    return ActionDecision(
        symbol=symbol,  # type: ignore[arg-type]
        side=Side.HOLD,
        leverage=Decimal("0"),
        size_pct=Decimal("0"),
        stop_loss_pct=None,
        take_profit_pct=None,
        entry_type=EntryType.NONE,
        limit_price=None,
        confidence=Decimal("0.50"),
        time_horizon_min=60,
        action_reasoning="No clear directional signal at this time, holding position.",
        action_key_signals=[],
    )


def _flat_action(symbol: str = "BTC") -> ActionDecision:
    return ActionDecision(
        symbol=symbol,  # type: ignore[arg-type]
        side=Side.FLAT,
        leverage=Decimal("0"),
        size_pct=Decimal("0"),
        stop_loss_pct=None,
        take_profit_pct=None,
        entry_type=EntryType.NONE,
        limit_price=None,
        confidence=Decimal("0.60"),
        time_horizon_min=60,
        action_reasoning="Closing position due to adverse market conditions changing.",
        action_key_signals=[],
    )


def _open_position(symbol: str = "BTC", side: str = "LONG") -> OpenPositionSummary:
    return OpenPositionSummary(
        symbol=symbol,  # type: ignore[arg-type]
        side=side,  # type: ignore[arg-type]
        entry_price=Decimal("30000"),
        current_price=Decimal("31000"),
        size_units=Decimal("0.1"),
        leverage=Decimal("3"),
        unrealized_pnl_usd=Decimal("100"),
        age_minutes=30,
    )


# ---------------------------------------------------------------------------
# OrderResult model validation
# ---------------------------------------------------------------------------


class TestOrderResult:
    def test_valid_filled_order(self) -> None:
        order = OrderResult(
            hl_order_id="hl-123",
            client_order_id="cl-456",
            order_kind=OrderKind.ENTRY,
            status="filled",
            requested_price=Decimal("30000"),
            filled_price=Decimal("30001"),
            requested_size_units=Decimal("0.1"),
            filled_size_units=Decimal("0.1"),
            slippage_bps=Decimal("3"),
            fee_usd=Decimal("0.50"),
            raw_response={"status": "ok"},
        )
        assert order.order_kind == OrderKind.ENTRY
        assert order.status == "filled"

    def test_valid_triggered_sl_order(self) -> None:
        order = OrderResult(
            hl_order_id="hl-sl",
            client_order_id="cl-sl",
            order_kind=OrderKind.STOP_LOSS,
            status="triggered",
            requested_price=None,
            filled_price=None,
            requested_size_units=Decimal("0.1"),
            filled_size_units=None,
            slippage_bps=None,
            fee_usd=None,
            raw_response={},
        )
        assert order.order_kind == OrderKind.STOP_LOSS
        assert order.filled_price is None

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            OrderResult(  # type: ignore[call-arg]
                hl_order_id="x",
                client_order_id="y",
                order_kind=OrderKind.ENTRY,
                status="filled",
                requested_price=None,
                filled_price=None,
                requested_size_units=Decimal("1"),
                filled_size_units=None,
                slippage_bps=None,
                fee_usd=None,
                raw_response={},
                unexpected_field="boom",
            )

    def test_all_valid_statuses(self) -> None:
        for status in ("pending", "filled", "partial", "cancelled", "rejected", "triggered"):
            order = OrderResult(
                hl_order_id="x",
                client_order_id="y",
                order_kind=OrderKind.CLOSE,
                status=status,  # type: ignore[arg-type]
                requested_price=None,
                filled_price=None,
                requested_size_units=Decimal("1"),
                filled_size_units=None,
                slippage_bps=None,
                fee_usd=None,
                raw_response={},
            )
            assert order.status == status


# ---------------------------------------------------------------------------
# PositionClosureInfo model validation
# ---------------------------------------------------------------------------


class TestPositionClosureInfo:
    def test_valid_closure(self) -> None:
        info = PositionClosureInfo(
            closed_at="2026-01-01T12:00:00Z",
            exit_price=Decimal("31500"),
            close_reason=CloseReason.TAKE_PROFIT,
            realized_pnl_usd=Decimal("150.00"),
        )
        assert info.close_reason == CloseReason.TAKE_PROFIT
        assert info.realized_pnl_usd == Decimal("150.00")

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            PositionClosureInfo(  # type: ignore[call-arg]
                closed_at="2026-01-01T12:00:00Z",
                exit_price=Decimal("31500"),
                close_reason=CloseReason.STOP_LOSS,
                realized_pnl_usd=Decimal("50.00"),
                unexpected="boom",
            )


# ---------------------------------------------------------------------------
# HyperliquidClient is abstract
# ---------------------------------------------------------------------------


class TestHyperliquidClientIsAbstract:
    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError):
            HyperliquidClient()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# MockHyperliquidClient — fetch_portfolio_state
# ---------------------------------------------------------------------------


class TestMockFetchPortfolioState:
    async def test_returns_default_portfolio(self) -> None:
        client = MockHyperliquidClient()
        state = await client.fetch_portfolio_state()
        assert isinstance(state, PortfolioState)
        assert state.equity_usd == Decimal("10000.00")

    async def test_returns_configured_portfolio(self) -> None:
        custom = PortfolioState(
            equity_usd=Decimal("5000"),
            available_usd=Decimal("4000"),
            margin_used_usd=Decimal("1000"),
            n_open_positions=1,
            unrealized_pnl_usd=Decimal("200"),
            open_positions=[_open_position()],
        )
        client = MockHyperliquidClient(portfolio_state=custom)
        state = await client.fetch_portfolio_state()
        assert state.equity_usd == Decimal("5000")
        assert state.n_open_positions == 1


# ---------------------------------------------------------------------------
# MockHyperliquidClient — HOLD → no-op
# ---------------------------------------------------------------------------


class TestMockExecuteHold:
    async def test_hold_no_position_returns_empty(self) -> None:
        client = MockHyperliquidClient()
        results = await client.execute_action(_hold_action(), "run-1", None)
        assert results == []

    async def test_hold_with_open_position_returns_empty(self) -> None:
        client = MockHyperliquidClient()
        results = await client.execute_action(_hold_action(), "run-1", _open_position())
        assert results == []


# ---------------------------------------------------------------------------
# MockHyperliquidClient — FLAT
# ---------------------------------------------------------------------------


class TestMockExecuteFlat:
    async def test_flat_no_position_returns_empty(self) -> None:
        client = MockHyperliquidClient()
        results = await client.execute_action(_flat_action(), "run-2", None)
        assert results == []

    async def test_flat_with_position_returns_close_order(self) -> None:
        client = MockHyperliquidClient()
        results = await client.execute_action(_flat_action(), "run-2", _open_position())
        assert len(results) == 1
        assert results[0].order_kind == OrderKind.CLOSE
        assert results[0].requested_size_units == Decimal("0.1")

    async def test_flat_close_order_has_filled_status(self) -> None:
        client = MockHyperliquidClient()
        results = await client.execute_action(_flat_action(), "run-2", _open_position())
        assert results[0].status == "filled"


# ---------------------------------------------------------------------------
# MockHyperliquidClient — LONG / SHORT open new position
# ---------------------------------------------------------------------------


class TestMockExecuteLongShortNoPosition:
    async def test_long_no_position_returns_entry_sl_tp(self) -> None:
        client = MockHyperliquidClient()
        results = await client.execute_action(_long_action(), "run-3", None)
        assert len(results) == 3
        kinds = [r.order_kind for r in results]
        assert OrderKind.ENTRY in kinds
        assert OrderKind.STOP_LOSS in kinds
        assert OrderKind.TAKE_PROFIT in kinds

    async def test_long_order_kinds_in_entry_sl_tp_order(self) -> None:
        client = MockHyperliquidClient()
        results = await client.execute_action(_long_action(), "run-3", None)
        assert results[0].order_kind == OrderKind.ENTRY
        assert results[1].order_kind == OrderKind.STOP_LOSS
        assert results[2].order_kind == OrderKind.TAKE_PROFIT

    async def test_short_no_position_returns_entry_sl_tp(self) -> None:
        client = MockHyperliquidClient()
        results = await client.execute_action(_short_action(), "run-4", None)
        assert len(results) == 3
        assert results[0].order_kind == OrderKind.ENTRY

    async def test_entry_order_size_is_leveraged_size_units(self) -> None:
        # ADR-0015: the entry order persists the leveraged executed quantity,
        # size_units = (equity * size_pct * leverage) / entry_price, NOT size_pct.
        action = _long_action()
        client = MockHyperliquidClient()
        expected = compute_position_sizing(
            equity_usd=Decimal("10000.00"),  # default mock equity
            size_pct=action.size_pct,
            entry_price=Decimal("100.00"),  # mock filled_price
            leverage=action.leverage,
            side=action.side,
            stop_loss_pct=action.stop_loss_pct,  # type: ignore[arg-type]
            take_profit_pct=action.take_profit_pct,  # type: ignore[arg-type]
        ).size_units
        results = await client.execute_action(action, "run-3", None)
        entry = results[0]
        assert entry.requested_size_units == expected
        assert entry.filled_size_units == expected
        assert expected != action.size_pct


# ---------------------------------------------------------------------------
# MockHyperliquidClient — same-side ignore
# ---------------------------------------------------------------------------


class TestMockExecuteSameSideIgnore:
    async def test_long_same_side_position_returns_empty(self) -> None:
        client = MockHyperliquidClient()
        pos = _open_position("BTC", "LONG")
        results = await client.execute_action(_long_action("BTC"), "run-5", pos)
        assert results == []

    async def test_short_same_side_position_returns_empty(self) -> None:
        client = MockHyperliquidClient()
        pos = _open_position("ETH", "SHORT")
        results = await client.execute_action(_short_action("ETH"), "run-5", pos)
        assert results == []


# ---------------------------------------------------------------------------
# MockHyperliquidClient — opposite-side: close then open
# ---------------------------------------------------------------------------


class TestMockExecuteOppositeSide:
    async def test_long_opposite_side_returns_close_then_entry_sl_tp(self) -> None:
        client = MockHyperliquidClient()
        pos = _open_position("BTC", "SHORT")  # currently SHORT
        results = await client.execute_action(_long_action("BTC"), "run-6", pos)
        assert len(results) == 4
        assert results[0].order_kind == OrderKind.CLOSE
        assert results[1].order_kind == OrderKind.ENTRY
        assert results[2].order_kind == OrderKind.STOP_LOSS
        assert results[3].order_kind == OrderKind.TAKE_PROFIT

    async def test_close_order_uses_current_position_size(self) -> None:
        client = MockHyperliquidClient()
        pos = _open_position("BTC", "SHORT")
        results = await client.execute_action(_long_action("BTC"), "run-6", pos)
        assert results[0].requested_size_units == pos.size_units


# ---------------------------------------------------------------------------
# MockHyperliquidClient — check_position_closure
# ---------------------------------------------------------------------------


class TestMockCheckPositionClosure:
    async def test_unknown_position_returns_none(self) -> None:
        client = MockHyperliquidClient()
        result = await client.check_position_closure("nonexistent-id")
        assert result is None

    async def test_known_position_returns_closure_info(self) -> None:
        closure = PositionClosureInfo(
            closed_at="2026-01-01T10:00:00Z",
            exit_price=Decimal("32000"),
            close_reason=CloseReason.TAKE_PROFIT,
            realized_pnl_usd=Decimal("200"),
        )
        client = MockHyperliquidClient(closed_positions={"pos-abc": closure})
        result = await client.check_position_closure("pos-abc")
        assert result is not None
        assert result.close_reason == CloseReason.TAKE_PROFIT
        assert result.realized_pnl_usd == Decimal("200")


# ---------------------------------------------------------------------------
# MockHyperliquidClient — executed_actions tracking
# ---------------------------------------------------------------------------


class TestMockExecutedActionsTracking:
    async def test_executed_actions_records_each_call(self) -> None:
        client = MockHyperliquidClient()
        action1 = _long_action("BTC")
        action2 = _hold_action("ETH")
        await client.execute_action(action1, "run-a", None)
        await client.execute_action(action2, "run-b", None)
        assert len(client.executed_actions) == 2
        assert client.executed_actions[0][0] is action1
        assert client.executed_actions[0][1] == "run-a"
        assert client.executed_actions[1][0] is action2

    async def test_executed_actions_records_current_position(self) -> None:
        client = MockHyperliquidClient()
        pos = _open_position()
        action = _flat_action()
        await client.execute_action(action, "run-c", pos)
        assert client.executed_actions[0][2] is pos
