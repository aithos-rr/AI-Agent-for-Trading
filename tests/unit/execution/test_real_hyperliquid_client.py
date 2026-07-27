"""Unit tests for RealHyperliquidClient (§7.5, M4-T08 code path).

The hyperliquid-python-sdk is MOCKED throughout — no real network calls are made
(the devcontainer is firewalled from Hyperliquid; live testnet verification is the
human-gated M4-T08). These tests have teeth: each asserts behaviour that would fail
if the production logic were wrong (testnet enforcement, ADR-0015 sizing, side
semantics parity with the Mock, SDK error mapping, Decimal discipline).
"""

import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiat.domain.enums import CloseReason, EntryType, OrderKind, Side
from aiat.domain.exceptions import ExecutionRejectedError, ExecutionTimeoutError
from aiat.domain.schemas import ActionDecision, OpenPositionSummary
from aiat.execution.hyperliquid_client import (
    _HL_TESTNET_API_URL,
    HyperliquidClient,
    MockHyperliquidClient,
    OrderResult,
    PositionClosureInfo,
    RealHyperliquidClient,
    _parse_order_response,
    _ParsedOrder,
    build_hl_client,
    detect_autonomous_closure,
)
from aiat.execution.sizing import compute_position_sizing

# ---------------------------------------------------------------------------
# Action / position helpers
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
        entry_price=Decimal("100"),
        current_price=Decimal("101"),
        size_units=Decimal("0.5"),
        leverage=Decimal("3"),
        unrealized_pnl_usd=Decimal("10"),
        age_minutes=30,
    )


# ---------------------------------------------------------------------------
# SDK response builders (canonical Hyperliquid shapes)
# ---------------------------------------------------------------------------


def _filled_resp(total_sz: object = "30.0", avg_px: object = "101.0", oid: int = 111) -> dict:
    return {
        "status": "ok",
        "response": {
            "type": "order",
            "data": {"statuses": [{"filled": {"totalSz": total_sz, "avgPx": avg_px, "oid": oid}}]},
        },
    }


def _fill(
    *,
    oid: int = 111,
    coin: str = "BTC",
    fee: object = "1.5",
    closed_pnl: object = "0.0",
    px: object = "101.0",
    sz: object = "30.0",
    dir_: str = "Open Long",
    time_ms: int = 1_700_000_000_000,
) -> dict:
    """A Hyperliquid ``user_fills`` record — the shape the real venue returns, where the
    taker fee lives under ``"fee"`` (finding A). Used to exercise fee reconciliation by oid.
    """
    return {
        "coin": coin,
        "oid": oid,
        "dir": dir_,
        "px": px,
        "sz": sz,
        "fee": fee,
        "closedPnl": closed_pnl,
        "time": time_ms,
    }


def _resting_resp(oid: int = 222) -> dict:
    return {
        "status": "ok",
        "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": oid}}]}},
    }


def _err_resp(msg: str = "Insufficient margin to place order") -> dict:
    return {"status": "err", "response": msg}


def _status_error_resp(msg: str = "Order has invalid size") -> dict:
    return {
        "status": "ok",
        "response": {"type": "order", "data": {"statuses": [{"error": msg}]}},
    }


def _asset_pos(
    coin: str = "BTC",
    szi: object = "0.5",
    entry: object = "100.0",
    upnl: object = "50.0",
    lev: int = 3,
) -> dict:
    return {
        "type": "oneWay",
        "position": {
            "coin": coin,
            "szi": szi,
            "entryPx": entry,
            "unrealizedPnl": upnl,
            "leverage": {"type": "cross", "value": lev},
        },
    }


def _user_state(
    account_value: object = "10000.0",
    withdrawable: object = "9500.0",
    total_margin: object = "500.0",
    positions: list | None = None,
) -> dict:
    return {
        "marginSummary": {
            "accountValue": account_value,
            "totalMarginUsed": total_margin,
            "totalNtlPos": "0.0",
            "totalRawUsd": "0.0",
        },
        "withdrawable": withdrawable,
        "assetPositions": positions if positions is not None else [],
    }


_MIDS = {"BTC": "100.0", "ETH": "50.0", "SOL": "20.0"}


def _client(
    *, exchange: MagicMock | None = None, info: MagicMock | None = None, timeout: float = 60.0
) -> RealHyperliquidClient:
    return RealHyperliquidClient(
        exchange=exchange if exchange is not None else MagicMock(),
        info=info if info is not None else MagicMock(),
        account_address="0xabc",
        network="testnet",
        timeout_seconds=timeout,
    )


def _open_ready_sdk() -> tuple[MagicMock, MagicMock]:
    """An exchange+info pair primed for a successful position open."""
    info = MagicMock()
    info.user_state.return_value = _user_state()
    info.all_mids.return_value = dict(_MIDS)
    # szDecimals lookup (ADR-0017): every symbol → asset 0 → 5 decimals (BTC perp).
    info.name_to_asset.return_value = 0
    info.asset_to_sz_decimals = {0: 5}
    # user_fills carries the per-fill fee (finding A). Real HL fill shape — key `fee`
    # alongside closedPnl/px/sz/oid/dir/time. oid 111 matches the default filled oid of
    # both market_open and market_close (see `_filled_resp`), so entry/close reconcile here.
    info.user_fills.return_value = [_fill(oid=111, fee="1.5")]
    exchange = MagicMock()
    exchange.update_leverage.return_value = {"status": "ok"}
    exchange.market_open.return_value = _filled_resp(total_sz="30.0", avg_px="101.0")
    exchange.order.return_value = _resting_resp()
    exchange.market_close.return_value = _filled_resp(total_sz="0.5", avg_px="98.0")
    return exchange, info


# ---------------------------------------------------------------------------
# Construction + invariant #9 (no mainnet)
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_is_hyperliquid_client_subclass(self) -> None:
        client = _client()
        assert isinstance(client, HyperliquidClient)

    def test_testnet_constructs(self) -> None:
        client = _client()
        assert client is not None

    @pytest.mark.parametrize("bad_network", ["mainnet", "MAINNET", "", "prod"])
    def test_non_testnet_network_raises(self, bad_network: str) -> None:
        with pytest.raises(RuntimeError, match="testnet"):
            RealHyperliquidClient(
                exchange=MagicMock(),
                info=MagicMock(),
                account_address="0xabc",
                network=bad_network,
            )

    def test_from_settings_rejects_non_testnet(self) -> None:
        """Invariant #9: from_settings must refuse mainnet before touching the SDK."""
        fake_settings = SimpleNamespace(
            network="mainnet",
            hl_wallet_private_key=SimpleNamespace(get_secret_value=lambda: "0x" + "1" * 64),
            hl_wallet_address="0x" + "0" * 40,
            hard_timeout_seconds=180,
        )
        with pytest.raises(RuntimeError, match="testnet"):
            RealHyperliquidClient.from_settings(fake_settings)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _parse_order_response — pure parser with teeth
# ---------------------------------------------------------------------------


class TestParseOrderResponse:
    def test_filled(self) -> None:
        parsed = _parse_order_response(_filled_resp(total_sz="0.3", avg_px="100.5", oid=9))
        assert parsed == _ParsedOrder(
            status="filled", oid="9", avg_px=Decimal("100.5"), total_sz=Decimal("0.3")
        )
        assert isinstance(parsed.avg_px, Decimal)
        assert isinstance(parsed.total_sz, Decimal)

    def test_resting(self) -> None:
        parsed = _parse_order_response(_resting_resp(oid=42))
        assert parsed.status == "resting"
        assert parsed.oid == "42"
        assert parsed.avg_px is None
        assert parsed.total_sz is None

    def test_top_level_err_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError, match="Insufficient margin"):
            _parse_order_response(_err_resp())

    def test_status_item_error_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError, match="invalid size"):
            _parse_order_response(_status_error_resp())

    def test_sdk_error_envelope_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError, match="Could not parse"):
            _parse_order_response({"error": "Could not parse JSON: <html>"})

    def test_malformed_structure_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError, match="malformed"):
            _parse_order_response({"status": "ok", "response": {"type": "order"}})

    def test_empty_statuses_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError, match="empty"):
            _parse_order_response(
                {"status": "ok", "response": {"type": "order", "data": {"statuses": []}}}
            )

    def test_non_dict_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError):
            _parse_order_response("boom")

    def test_unrecognized_status_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError, match="unrecognized"):
            _parse_order_response(
                {
                    "status": "ok",
                    "response": {"type": "order", "data": {"statuses": [{"weird": 1}]}},
                }
            )

    def test_non_dict_status_item_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError, match="invalid status item"):
            _parse_order_response(
                {"status": "ok", "response": {"type": "order", "data": {"statuses": ["nope"]}}}
            )

    def _filled_with(self, **filled: object) -> dict:
        return {
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"filled": filled}]}},
        }

    def test_filled_missing_avgpx_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError, match="missing oid/avgPx/totalSz"):
            _parse_order_response(self._filled_with(totalSz="30.0", oid=1))

    def test_filled_missing_totalsz_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError, match="missing oid/avgPx/totalSz"):
            _parse_order_response(self._filled_with(avgPx="100.0", oid=1))

    def test_filled_missing_oid_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError, match="missing oid/avgPx/totalSz"):
            _parse_order_response(self._filled_with(avgPx="100.0", totalSz="30.0"))

    def test_filled_null_avgpx_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError, match="missing oid/avgPx/totalSz"):
            _parse_order_response(self._filled_with(avgPx=None, totalSz="30.0", oid=1))

    def test_filled_unparseable_numeric_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError, match="unparseable"):
            _parse_order_response(self._filled_with(avgPx="not-a-number", totalSz="30.0", oid=1))

    def test_resting_missing_oid_raises(self) -> None:
        with pytest.raises(ExecutionRejectedError, match="resting status missing oid"):
            _parse_order_response(
                {
                    "status": "ok",
                    "response": {"type": "order", "data": {"statuses": [{"resting": {}}]}},
                }
            )


# ---------------------------------------------------------------------------
# execute_action — semantic parity with MockHyperliquidClient
# ---------------------------------------------------------------------------


class TestExecuteActionHold:
    async def test_hold_returns_empty_and_no_sdk_calls(self) -> None:
        exchange, info = _open_ready_sdk()
        client = _client(exchange=exchange, info=info)
        results = await client.execute_action(_hold_action(), "run-1", None)
        assert results == []
        exchange.market_open.assert_not_called()
        exchange.market_close.assert_not_called()
        exchange.order.assert_not_called()
        exchange.update_leverage.assert_not_called()

    async def test_hold_with_position_returns_empty(self) -> None:
        exchange, info = _open_ready_sdk()
        client = _client(exchange=exchange, info=info)
        results = await client.execute_action(_hold_action(), "run-1", _open_position())
        assert results == []
        exchange.market_close.assert_not_called()


class TestExecuteActionFlat:
    async def test_flat_no_position_returns_empty_no_calls(self) -> None:
        exchange, info = _open_ready_sdk()
        client = _client(exchange=exchange, info=info)
        results = await client.execute_action(_flat_action(), "run-2", None)
        assert results == []
        exchange.market_close.assert_not_called()

    async def test_flat_with_position_closes(self) -> None:
        exchange, info = _open_ready_sdk()
        client = _client(exchange=exchange, info=info)
        pos = _open_position("BTC", "LONG")
        results = await client.execute_action(_flat_action("BTC"), "run-2", pos)
        assert len(results) == 1
        close = results[0]
        assert close.order_kind == OrderKind.CLOSE
        assert close.status == "filled"
        assert close.filled_price == Decimal("98.0")
        # close uses the EXISTING position size, not the SDK-reported fill size
        assert close.requested_size_units == pos.size_units
        exchange.market_close.assert_called_once_with("BTC")
        exchange.market_open.assert_not_called()


class TestExecuteActionOpen:
    async def test_long_open_returns_entry_sl_tp_in_order(self) -> None:
        exchange, info = _open_ready_sdk()
        client = _client(exchange=exchange, info=info)
        results = await client.execute_action(_long_action("BTC"), "run-3", None)
        assert [r.order_kind for r in results] == [
            OrderKind.ENTRY,
            OrderKind.STOP_LOSS,
            OrderKind.TAKE_PROFIT,
        ]
        # leverage set as integer before opening
        exchange.update_leverage.assert_called_once_with(3, "BTC", True)
        exchange.market_open.assert_called_once()
        assert exchange.order.call_count == 2  # SL + TP

    async def test_long_entry_is_buy_true_sl_tp_opposite(self) -> None:
        exchange, info = _open_ready_sdk()
        client = _client(exchange=exchange, info=info)
        await client.execute_action(_long_action("BTC"), "run-3", None)
        # market_open(name, is_buy, sz) — LONG ⇒ buy
        assert exchange.market_open.call_args.args[1] is True
        # both trigger orders are sells (reduce-only opposite side)
        for call in exchange.order.call_args_list:
            assert call.args[1] is False  # is_buy
            assert call.args[5] is True  # reduce_only

    async def test_short_entry_is_buy_false_sl_tp_buy(self) -> None:
        exchange, info = _open_ready_sdk()
        client = _client(exchange=exchange, info=info)
        await client.execute_action(_short_action("ETH"), "run-4", None)
        assert exchange.market_open.call_args.args[1] is False  # SHORT ⇒ sell
        for call in exchange.order.call_args_list:
            assert call.args[1] is True  # SL/TP buy to close a short

    async def test_same_side_position_ignored_no_calls(self) -> None:
        exchange, info = _open_ready_sdk()
        client = _client(exchange=exchange, info=info)
        pos = _open_position("BTC", "LONG")
        results = await client.execute_action(_long_action("BTC"), "run-5", pos)
        assert results == []
        exchange.market_open.assert_not_called()
        exchange.market_close.assert_not_called()

    async def test_opposite_side_closes_then_opens(self) -> None:
        exchange, info = _open_ready_sdk()
        client = _client(exchange=exchange, info=info)
        pos = _open_position("BTC", "SHORT")  # currently short, action is long
        results = await client.execute_action(_long_action("BTC"), "run-6", pos)
        assert [r.order_kind for r in results] == [
            OrderKind.CLOSE,
            OrderKind.ENTRY,
            OrderKind.STOP_LOSS,
            OrderKind.TAKE_PROFIT,
        ]
        exchange.market_close.assert_called_once()
        exchange.market_open.assert_called_once()

    async def test_sl_tp_order_result_fields(self) -> None:
        """The SL/TP OrderResults themselves (not just the SDK calls) must be well-formed."""
        exchange, info = _open_ready_sdk()
        client = _client(exchange=exchange, info=info)
        _entry, sl, tp = await client.execute_action(_long_action("BTC"), "run-3", None)

        for prot, kind, trigger in (
            (sl, OrderKind.STOP_LOSS, "95"),
            (tp, OrderKind.TAKE_PROFIT, "110"),
        ):
            assert prot.order_kind == kind
            assert prot.status == "triggered"
            assert prot.filled_price is None
            assert prot.filled_size_units is None
            assert prot.slippage_bps is None
            # sized to the ACTUAL fill (30 units), not size_pct
            assert prot.requested_size_units == Decimal("30.0")
            # requested_price records the trigger price (more faithful than Mock's None)
            assert prot.requested_price == Decimal(trigger)
            assert prot.hl_order_id == "222"  # from resting oid


# ---------------------------------------------------------------------------
# ADR-0015 — leveraged size_units convention (the critical correctness test)
# ---------------------------------------------------------------------------


class TestAdr0015Sizing:
    async def test_entry_uses_leveraged_size_units(self) -> None:
        exchange, info = _open_ready_sdk()
        client = _client(exchange=exchange, info=info)
        action = _long_action("BTC")

        expected = compute_position_sizing(
            equity_usd=Decimal("10000.0"),  # from _user_state accountValue
            size_pct=action.size_pct,
            entry_price=Decimal("100.0"),  # from _MIDS["BTC"]
            leverage=action.leverage,
            side=action.side,
            stop_loss_pct=action.stop_loss_pct,  # type: ignore[arg-type]
            take_profit_pct=action.take_profit_pct,  # type: ignore[arg-type]
        )
        # equity 10000 * size_pct 0.10 * leverage 3 / price 100 = 30 units (NOT 0.10)
        assert expected.size_units == Decimal("30")
        assert expected.size_units != action.size_pct

        results = await client.execute_action(action, "run-3", None)
        entry = results[0]

        # the SDK is asked to open the LEVERAGED size (float only at the boundary)
        assert exchange.market_open.call_args.args[2] == float(expected.size_units)
        # the persisted OrderResult carries the leveraged size as Decimal
        assert entry.requested_size_units == expected.size_units
        assert entry.filled_size_units == Decimal("30.0")  # from SDK totalSz
        assert entry.filled_price == Decimal("101.0")  # from SDK avgPx

    async def test_trigger_prices_match_sizing(self) -> None:
        exchange, info = _open_ready_sdk()
        client = _client(exchange=exchange, info=info)
        action = _long_action("BTC")  # sl 5%, tp 10% off entry 100
        await client.execute_action(action, "run-3", None)

        # order(name, is_buy, sz, limit_px, order_type, reduce_only)
        sl_call, tp_call = exchange.order.call_args_list
        assert sl_call.args[4]["trigger"]["triggerPx"] == 95.0  # 100*(1-0.05)
        assert sl_call.args[4]["trigger"]["tpsl"] == "sl"
        assert sl_call.args[4]["trigger"]["isMarket"] is True
        assert tp_call.args[4]["trigger"]["triggerPx"] == 110.0  # 100*(1+0.10)
        assert tp_call.args[4]["trigger"]["tpsl"] == "tp"
        # triggers sized to the actual fill (30 units)
        assert sl_call.args[2] == 30.0
        assert tp_call.args[2] == 30.0

    async def test_slippage_bps_computed_from_mark(self) -> None:
        exchange, info = _open_ready_sdk()  # avgPx 101 vs mid 100 ⇒ 100 bps
        client = _client(exchange=exchange, info=info)
        results = await client.execute_action(_long_action("BTC"), "run-3", None)
        assert results[0].slippage_bps == Decimal("100")


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    async def test_open_rejected_raises(self) -> None:
        exchange, info = _open_ready_sdk()
        exchange.market_open.return_value = _err_resp()
        client = _client(exchange=exchange, info=info)
        with pytest.raises(ExecutionRejectedError, match="Insufficient margin"):
            await client.execute_action(_long_action("BTC"), "run-3", None)

    async def test_entry_not_filled_raises(self) -> None:
        exchange, info = _open_ready_sdk()
        exchange.market_open.return_value = _resting_resp()  # never filled
        client = _client(exchange=exchange, info=info)
        with pytest.raises(ExecutionRejectedError, match="did not fill"):
            await client.execute_action(_long_action("BTC"), "run-3", None)

    async def test_close_not_filled_raises(self) -> None:
        exchange, info = _open_ready_sdk()
        exchange.market_close.return_value = _resting_resp()
        client = _client(exchange=exchange, info=info)
        with pytest.raises(ExecutionRejectedError, match="did not fill"):
            await client.execute_action(_flat_action("BTC"), "run-2", _open_position())

    async def test_close_missing_totalsz_raises(self) -> None:
        """Close validation is symmetric with open: a fill without totalSz is rejected."""
        exchange, info = _open_ready_sdk()
        exchange.market_close.return_value = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {"statuses": [{"filled": {"avgPx": "98.0", "oid": 5}}]},
            },
        }
        client = _client(exchange=exchange, info=info)
        # parser rejects the malformed filled body (missing totalSz) before _close_order
        with pytest.raises(ExecutionRejectedError):
            await client.execute_action(_flat_action("BTC"), "run-2", _open_position())

    async def test_missing_mark_price_raises(self) -> None:
        exchange, info = _open_ready_sdk()
        info.all_mids.return_value = {}  # no price for BTC
        client = _client(exchange=exchange, info=info)
        with pytest.raises(ExecutionRejectedError, match="mark price"):
            await client.execute_action(_long_action("BTC"), "run-3", None)

    async def test_timeout_maps_to_execution_timeout(self) -> None:
        def _slow(*_args: object, **_kwargs: object) -> dict:
            time.sleep(0.5)
            return _user_state()

        info = MagicMock()
        info.user_state = _slow
        client = _client(exchange=MagicMock(), info=info, timeout=0.05)
        with pytest.raises(ExecutionTimeoutError, match="timed out"):
            await client.fetch_portfolio_state()


# ---------------------------------------------------------------------------
# Decimal discipline (invariant #12) — no float reaches the domain objects
# ---------------------------------------------------------------------------


class TestNoFloatLeak:
    async def test_order_result_fields_are_decimal_from_float_sdk(self) -> None:
        exchange, info = _open_ready_sdk()
        # SDK returns native floats, not strings
        exchange.market_open.return_value = _filled_resp(total_sz=30.0, avg_px=101.0)
        client = _client(exchange=exchange, info=info)
        entry = (await client.execute_action(_long_action("BTC"), "run-3", None))[0]
        for value in (entry.filled_price, entry.filled_size_units, entry.requested_size_units):
            assert isinstance(value, Decimal)
        assert isinstance(entry.slippage_bps, Decimal)
        assert not isinstance(entry.filled_price, float)

    async def test_portfolio_state_fields_are_decimal(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(
            account_value=10000.0, positions=[_asset_pos("BTC", szi=0.5, entry=100.0, upnl=50.0)]
        )
        info.all_mids.return_value = {"BTC": 101.0}
        client = _client(exchange=MagicMock(), info=info)
        state = await client.fetch_portfolio_state()
        assert isinstance(state.equity_usd, Decimal)
        assert isinstance(state.open_positions[0].size_units, Decimal)
        assert isinstance(state.open_positions[0].current_price, Decimal)


# ---------------------------------------------------------------------------
# fetch_portfolio_state mapping
# ---------------------------------------------------------------------------


class TestFetchPortfolioState:
    async def test_maps_summary_and_positions(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(
            account_value="12000.0",
            withdrawable="8000.0",
            total_margin="4000.0",
            positions=[
                _asset_pos("BTC", szi="0.5", entry="100.0", upnl="50.0", lev=3),
                _asset_pos("ETH", szi="-2.0", entry="50.0", upnl="-10.0", lev=2),
            ],
        )
        info.all_mids.return_value = dict(_MIDS)
        client = _client(exchange=MagicMock(), info=info)
        state = await client.fetch_portfolio_state()

        assert state.equity_usd == Decimal("12000.0")
        assert state.available_usd == Decimal("8000.0")
        assert state.margin_used_usd == Decimal("4000.0")
        assert state.n_open_positions == 2
        assert state.unrealized_pnl_usd == Decimal("40.0")  # 50 + (-10)

        by_symbol = {p.symbol: p for p in state.open_positions}
        assert by_symbol["BTC"].side == "LONG"  # positive szi
        assert by_symbol["BTC"].size_units == Decimal("0.5")
        assert by_symbol["BTC"].current_price == Decimal("100.0")
        assert by_symbol["BTC"].leverage == Decimal("3")
        assert by_symbol["ETH"].side == "SHORT"  # negative szi
        assert by_symbol["ETH"].size_units == Decimal("2.0")  # absolute value

    async def test_filters_unsupported_and_zero_positions(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(
            positions=[
                _asset_pos("DOGE", szi="1000.0"),  # unsupported symbol
                _asset_pos("BTC", szi="0"),  # flat (zero) position
                _asset_pos("SOL", szi="3.0"),  # kept
            ]
        )
        info.all_mids.return_value = dict(_MIDS)
        client = _client(exchange=MagicMock(), info=info)
        state = await client.fetch_portfolio_state()
        assert state.n_open_positions == 1
        assert state.open_positions[0].symbol == "SOL"

    async def test_empty_account(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(positions=[])
        info.all_mids.return_value = dict(_MIDS)
        client = _client(exchange=MagicMock(), info=info)
        state = await client.fetch_portfolio_state()
        assert state.n_open_positions == 0
        assert state.open_positions == []


# ---------------------------------------------------------------------------
# check_position_closure
# ---------------------------------------------------------------------------


class TestCheckPositionClosure:
    async def test_open_position_returns_none(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(positions=[_asset_pos("BTC", szi="0.5")])
        client = _client(exchange=MagicMock(), info=info)
        assert await client.check_position_closure("BTC") is None
        info.user_fills.assert_not_called()

    async def test_closed_position_returns_closure_info(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(positions=[])  # BTC gone ⇒ closed
        info.user_fills.return_value = [
            {
                "coin": "BTC",
                "dir": "Close Long",
                "closedPnl": "25.0",
                "px": "110.0",
                "time": 1_700_000_000_000,
            }
        ]
        client = _client(exchange=MagicMock(), info=info)
        closure = await client.check_position_closure("BTC")
        assert closure is not None
        assert closure.exit_price == Decimal("110.0")
        assert closure.realized_pnl_usd == Decimal("25.0")
        assert closure.close_reason == CloseReason.MODEL_CLOSE
        assert isinstance(closure.realized_pnl_usd, Decimal)

    async def test_liquidation_flagged(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(positions=[])
        info.user_fills.return_value = [
            {
                "coin": "BTC",
                "dir": "Close Long",
                "closedPnl": "-500.0",
                "px": "80.0",
                "time": 1_700_000_000_000,
                "liquidation": True,
            }
        ]
        client = _client(exchange=MagicMock(), info=info)
        closure = await client.check_position_closure("BTC")
        assert closure is not None
        assert closure.close_reason == CloseReason.LIQUIDATED

    async def test_no_close_fill_returns_none(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(positions=[])
        info.user_fills.return_value = [{"coin": "BTC", "dir": "Open Long", "px": "100.0"}]
        client = _client(exchange=MagicMock(), info=info)
        assert await client.check_position_closure("BTC") is None

    async def test_other_open_positions_do_not_mask_closure(self) -> None:
        """A live position in a DIFFERENT coin must not be read as 'still open'."""
        info = MagicMock()
        info.user_state.return_value = _user_state(positions=[_asset_pos("ETH", szi="1.0")])
        info.user_fills.return_value = [
            {"coin": "BTC", "dir": "Close Long", "closedPnl": "5.0", "px": "120.0", "time": 1}
        ]
        client = _client(exchange=MagicMock(), info=info)
        closure = await client.check_position_closure("BTC")
        assert closure is not None
        assert closure.exit_price == Decimal("120.0")

    async def test_fee_and_pnl_use_only_the_latest_close_order_oid(self) -> None:
        """SL/TP fee-inflation fix: user_fills is a rolling wallet-wide window, so it holds the
        coin's EARLIER closures and other coins' fills too. Only the fills of the single most-
        recent closing order (partials share one oid) must be summed — otherwise the taker fee is
        inflated 10-50x. Fails on the pre-fix code, which summed every ETH close fill."""
        info = MagicMock()
        info.user_state.return_value = _user_state(positions=[])  # ETH closed
        info.user_fills.return_value = [
            # most-recent ETH closure (oid 200): two partials of the SAME order → both summed
            {
                "coin": "ETH",
                "dir": "Close Long",
                "closedPnl": "5.0",
                "px": "1828.6",
                "fee": "0.1",
                "oid": 200,
                "time": 2001,
            },
            {
                "coin": "ETH",
                "dir": "Close Long",
                "closedPnl": "2.0",
                "px": "1828.6",
                "fee": "0.052",
                "oid": 200,
                "time": 2000,
            },
            # an EARLIER ETH closure (oid 100) — must NOT be folded in
            {
                "coin": "ETH",
                "dir": "Close Long",
                "closedPnl": "99.0",
                "px": "1800.0",
                "fee": "8.0",
                "oid": 100,
                "time": 1000,
            },
            # a different coin's close — excluded by the coin filter regardless
            {
                "coin": "BTC",
                "dir": "Close Long",
                "closedPnl": "50.0",
                "px": "65000",
                "fee": "5.0",
                "oid": 300,
                "time": 1500,
            },
        ]
        client = _client(exchange=MagicMock(), info=info)
        closure = await client.check_position_closure("ETH")
        assert closure is not None
        assert closure.fee_usd == Decimal("0.152")  # 0.1 + 0.052, NOT + 8.0
        assert closure.realized_pnl_usd == Decimal("7.0")  # 5.0 + 2.0, NOT + 99.0
        assert closure.exit_price == Decimal("1828.6")

    async def test_null_closed_pnl_coalesces_not_crashes(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(positions=[])
        info.user_fills.return_value = [
            {"coin": "BTC", "dir": "Close Long", "closedPnl": None, "px": "120.0", "time": 1}
        ]
        client = _client(exchange=MagicMock(), info=info)
        closure = await client.check_position_closure("BTC")
        assert closure is not None
        assert closure.realized_pnl_usd == Decimal("0")  # null PnL coalesced, no crash

    async def test_missing_exit_price_returns_none(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(positions=[])
        info.user_fills.return_value = [
            {"coin": "BTC", "dir": "Close Long", "closedPnl": "5.0", "time": 1}  # no px
        ]
        client = _client(exchange=MagicMock(), info=info)
        # exit_price 0 would violate the positions.exit_price>0 CHECK ⇒ refuse to fabricate
        assert await client.check_position_closure("BTC") is None

    async def test_nonpositive_exit_price_returns_none(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(positions=[])
        info.user_fills.return_value = [
            {"coin": "BTC", "dir": "Close Long", "closedPnl": "5.0", "px": "0", "time": 1}
        ]
        client = _client(exchange=MagicMock(), info=info)
        assert await client.check_position_closure("BTC") is None

    async def test_unparseable_fill_numeric_returns_none(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(positions=[])
        info.user_fills.return_value = [
            {"coin": "BTC", "dir": "Close Long", "closedPnl": "5.0", "px": "garbage", "time": 1}
        ]
        client = _client(exchange=MagicMock(), info=info)
        # malformed numerics must not crash the loop — closure is deferred, not fabricated
        assert await client.check_position_closure("BTC") is None

    async def test_misses_close_when_symbol_reopened_same_tick(self) -> None:
        """ROOT CAUSE of the cn-premium zombie (T4b, ADR-0025 timeline): when the model reopens
        BTC in the SAME tick an SL fired, the chain's user_state shows BTC OPEN again (szi != 0 —
        the just-reopened position), so the szi short-circuit (hyperliquid_client.py:786) returns
        None BEFORE inspecting the SL close fill in user_fills. The prior close is missed and the
        old DB row becomes a zombie. The old `_check_pending_closures` ran AFTER `_execute_actions`
        (step 9 after step 8) and was symbol-keyed, so both DB BTC rows hit this same short-circuit.

        This test pins the legacy (buggy) behaviour of `check_position_closure`, now SUPERSEDED and
        no longer called in production: the root-cause fix lives in `detect_autonomous_closure` +
        the orchestrator-level `ClosureReconciler` (per-position oid match, runs before execution —
        ADR-0038). The netted DB↔chain reconciliation (detect_chain_divergences → zombie_row) stays
        as the downstream safety net (ADR-0025).
        """
        info = MagicMock()
        # Chain shows BTC OPEN again — the LONG the model just reopened (oid 56301522722).
        info.user_state.return_value = _user_state(positions=[_asset_pos("BTC", szi="0.00425")])
        # user_fills DOES contain the SL close fill from 01:06 UTC (oid 56298713468), but it is
        # never consulted because szi != 0 short-circuits first.
        info.user_fills.return_value = [
            {
                "coin": "BTC",
                "dir": "Close Long",
                "closedPnl": "-5.72",
                "px": "62500",
                "time": 1_752_282_360_000,
                "fee": "0.1",
            }
        ]
        client = _client(exchange=MagicMock(), info=info)
        assert await client.check_position_closure("BTC") is None  # SL close MISSED
        info.user_fills.assert_not_called()  # short-circuit — fills never inspected


# ---------------------------------------------------------------------------
# build_hl_client factory
# ---------------------------------------------------------------------------


class TestBuildHlClient:
    def test_defaults_to_mock(self) -> None:
        settings = SimpleNamespace()  # no hl_client_impl attribute
        client = build_hl_client(settings)  # type: ignore[arg-type]
        assert isinstance(client, MockHyperliquidClient)

    def test_explicit_mock(self) -> None:
        settings = SimpleNamespace(hl_client_impl="mock")
        assert isinstance(build_hl_client(settings), MockHyperliquidClient)  # type: ignore[arg-type]

    def test_real_delegates_to_from_settings(self) -> None:
        settings = SimpleNamespace(hl_client_impl="real")
        sentinel = MagicMock(spec=RealHyperliquidClient)
        with patch.object(RealHyperliquidClient, "from_settings", return_value=sentinel) as mk:
            result = build_hl_client(settings)  # type: ignore[arg-type]
        mk.assert_called_once_with(settings)
        assert result is sentinel


class TestFromSettings:
    def test_builds_testnet_pinned_client(self) -> None:
        """from_settings wires the SDK against the testnet URL only (invariant #9)."""
        settings = SimpleNamespace(
            network="testnet",
            hl_wallet_private_key=SimpleNamespace(get_secret_value=lambda: "0x" + "1" * 64),
            hl_wallet_address="0x" + "a" * 40,
            hard_timeout_seconds=180,
        )
        with (
            patch("eth_account.Account") as account,
            patch("hyperliquid.exchange.Exchange") as exchange_cls,
            patch("hyperliquid.info.Info") as info_cls,
        ):
            client = RealHyperliquidClient.from_settings(settings)  # type: ignore[arg-type]

        assert isinstance(client, RealHyperliquidClient)
        account.from_key.assert_called_once_with("0x" + "1" * 64)
        # base_url hard-pinned to testnet for both SDK clients
        assert info_cls.call_args.kwargs["base_url"] == _HL_TESTNET_API_URL
        assert exchange_cls.call_args.kwargs["base_url"] == _HL_TESTNET_API_URL
        assert exchange_cls.call_args.kwargs["account_address"] == "0x" + "a" * 40


# ---------------------------------------------------------------------------
# OrderResult shape sanity for downstream PositionsRepository consumption
# ---------------------------------------------------------------------------


class TestOrderResultDownstreamShape:
    async def test_entry_order_has_fields_positions_repo_requires(self) -> None:
        """PositionsRepository.open_position requires ENTRY with filled_price/size."""
        exchange, info = _open_ready_sdk()
        client = _client(exchange=exchange, info=info)
        results = await client.execute_action(_long_action("BTC"), "run-3", None)
        entry = next(o for o in results if o.order_kind == OrderKind.ENTRY)
        assert isinstance(entry, OrderResult)
        assert entry.filled_price is not None
        assert entry.filled_size_units is not None
        assert entry.hl_order_id == "111"  # from SDK oid
        assert entry.client_order_id  # non-empty uuid

    async def test_returns_position_closure_info_type(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(positions=[])
        info.user_fills.return_value = [
            {"coin": "BTC", "dir": "Close Long", "closedPnl": "1.0", "px": "1.0", "time": 1}
        ]
        client = _client(exchange=MagicMock(), info=info)
        closure = await client.check_position_closure("BTC")
        assert isinstance(closure, PositionClosureInfo)


# ---------------------------------------------------------------------------
# Size quantization to szDecimals (ADR-0017) — the float_to_wire bug from M4-T08
# ---------------------------------------------------------------------------


class TestSizeQuantization:
    def _client_with_szdec(self, sz_decimals: int) -> RealHyperliquidClient:
        info = MagicMock()
        info.name_to_asset.return_value = 0
        info.asset_to_sz_decimals = {0: sz_decimals}
        return _client(exchange=MagicMock(), info=info)

    def test_quantizes_to_sz_decimals_round_down(self) -> None:
        # The exact size that crashed the SDK on the first real testnet run.
        client = self._client_with_szdec(5)
        assert client._quantize_size("BTC", Decimal("0.0012814444551819603")) == Decimal("0.00128")

    def test_round_down_never_rounds_up(self) -> None:
        # 0.129 → 0.12 (ROUND_DOWN), never 0.13 — preserves max_size_pct (inv #8).
        client = self._client_with_szdec(2)
        assert client._quantize_size("BTC", Decimal("0.129")) == Decimal("0.12")

    def test_already_valid_size_unchanged(self) -> None:
        client = self._client_with_szdec(5)
        assert client._quantize_size("BTC", Decimal("30")) == Decimal("30")

    def test_unknown_symbol_raises(self) -> None:
        info = MagicMock()
        info.name_to_asset.side_effect = KeyError("DOGE")
        client = _client(exchange=MagicMock(), info=info)
        with pytest.raises(ExecutionRejectedError, match="szDecimals"):
            client._quantize_size("DOGE", Decimal("1"))

    async def test_open_size_quantizing_to_zero_raises(self) -> None:
        # szDecimals=0 (integer sizes) + tiny notional ⇒ size_units < 1 ⇒ 0 ⇒ reject,
        # never submit a 0-unit order.
        info = MagicMock()
        info.user_state.return_value = _user_state(account_value="100.0")
        info.all_mids.return_value = {"BTC": "1000.0"}  # high price ⇒ tiny size_units
        info.name_to_asset.return_value = 0
        info.asset_to_sz_decimals = {0: 0}
        exchange = MagicMock()
        exchange.update_leverage.return_value = {"status": "ok"}
        client = _client(exchange=exchange, info=info)
        with pytest.raises(ExecutionRejectedError, match="quantizes to 0"):
            await client.execute_action(_long_action("BTC"), "run-z", None)
        exchange.market_open.assert_not_called()
        exchange.update_leverage.assert_not_called()  # guard fires before leverage set

    async def test_entry_submits_quantized_size_to_sdk(self) -> None:
        # End-to-end through execute_action: the size sent to market_open is quantized,
        # and the entry OrderResult.requested_size_units carries the quantized value.
        exchange, info = _open_ready_sdk()
        info.asset_to_sz_decimals = {0: 3}  # 3 decimals
        info.user_state.return_value = _user_state(account_value="10000.0")
        info.all_mids.return_value = {"BTC": "100.0"}
        # equity 10000 * size_pct 0.10 * leverage 3 / 100 = 30.000 → quantizes cleanly
        client = _client(exchange=exchange, info=info)
        results = await client.execute_action(_long_action("BTC"), "run-q", None)
        submitted = exchange.market_open.call_args.args[2]
        assert submitted == 30.0
        assert results[0].requested_size_units == Decimal("30.000")


# ---------------------------------------------------------------------------
# Trigger price quantization to HL's perp rule (ADR-0018) — the Invalid TP/SL bug
# ---------------------------------------------------------------------------


class TestPriceQuantization:
    def _client_with_szdec(self, sz_decimals: int) -> RealHyperliquidClient:
        info = MagicMock()
        info.name_to_asset.return_value = 0
        info.asset_to_sz_decimals = {0: sz_decimals}
        return _client(exchange=MagicMock(), info=info)

    def test_btc_price_5_sig_figs_one_decimal(self) -> None:
        # BTC szDecimals=5 ⇒ 1 decimal, 5 sig figs ⇒ ~$73k is effectively integer.
        client = self._client_with_szdec(5)
        assert client._quantize_price("BTC", Decimal("73118.456789")) == Decimal("73118.0")

    def test_two_decimals(self) -> None:
        client = self._client_with_szdec(2)  # 6-2 = 4 decimals, 5 sig figs
        assert client._quantize_price("SOL", Decimal("180.456789")) == Decimal("180.46")

    def test_one_decimal_rounds_to_nearest(self) -> None:
        client = self._client_with_szdec(5)  # 1 decimal
        assert client._quantize_price("BTC", Decimal("1.2814444")) == Decimal("1.3")

    def test_integer_price_unchanged(self) -> None:
        client = self._client_with_szdec(5)
        assert client._quantize_price("BTC", Decimal("100")) == Decimal("100")

    def test_unknown_symbol_raises(self) -> None:
        info = MagicMock()
        info.name_to_asset.side_effect = KeyError("DOGE")
        client = _client(exchange=MagicMock(), info=info)
        with pytest.raises(ExecutionRejectedError, match="szDecimals"):
            client._quantize_price("DOGE", Decimal("1"))

    @pytest.mark.parametrize(
        ("price", "sz_decimals"),
        [
            ("73118.456789", 5),
            ("180.456789", 2),
            ("1.2814444", 5),
            ("61750.0", 5),
            ("71500.0", 5),
        ],
    )
    def test_output_passes_float_to_wire(self, price: str, sz_decimals: int) -> None:
        # The real guarantee: the quantized price clears HL's own float_to_wire check
        # (the native validation that rejected the raw price on testnet).
        from hyperliquid.utils.signing import float_to_wire

        client = self._client_with_szdec(sz_decimals)
        quantized = client._quantize_price("BTC", Decimal(price))
        float_to_wire(float(quantized))  # must not raise

    async def test_trigger_order_sends_quantized_price(self) -> None:
        # End-to-end through execute_action: the SL trigger price sent to the SDK is
        # quantized (1.1728), not the raw sizing value (1.172832).
        info = MagicMock()
        info.user_state.return_value = _user_state(account_value="10000.0")
        info.all_mids.return_value = {"BTC": "1.23456"}  # low price ⇒ fractional SL/TP
        info.name_to_asset.return_value = 0
        info.asset_to_sz_decimals = {0: 2}  # 4 decimals
        info.user_fills.return_value = [_fill(oid=111, fee="0.0")]  # entry-fee reconcile
        exchange = MagicMock()
        exchange.update_leverage.return_value = {"status": "ok"}
        exchange.market_open.return_value = _filled_resp(total_sz="2430.0", avg_px="1.23")
        exchange.order.return_value = _resting_resp()
        client = _client(exchange=exchange, info=info)

        await client.execute_action(_long_action("BTC"), "run-p", None)

        sl_call = exchange.order.call_args_list[0]
        # SL = 1.23456 * (1 - 0.05) = 1.172832 → quantized to 1.1728
        assert sl_call.args[4]["trigger"]["triggerPx"] == 1.1728
        assert sl_call.args[3] == 1.1728  # limit_px also quantized
        assert sl_call.args[4]["trigger"]["triggerPx"] != 1.172832  # not the raw value


# ---------------------------------------------------------------------------
# Fee reconciliation from user_fills (finding A, ADR-0027) — the missing tripwire
# ---------------------------------------------------------------------------


class TestFeeReconciliation:
    """The order-placement response never carries the fee; it must be reconciled from
    user_fills by oid. These tests have TEETH: each asserts a POSITIVE fee lands on the
    OrderResult / PositionClosureInfo — every one FAILS against the pre-fix code that
    hard-coded ``fee_usd=None`` (which is exactly what left 189 outcomes at sum_fees=0).
    """

    async def test_entry_fee_reconciled_from_user_fills(self) -> None:
        exchange, info = _open_ready_sdk()  # user_fills → [_fill(oid=111, fee="1.5")]
        client = _client(exchange=exchange, info=info)
        entry, sl, tp = await client.execute_action(_long_action("BTC"), "run-3", None)
        # TRIPWIRE: entry (taker_open) fee is the summed fill fee, not None.
        assert entry.fee_usd == Decimal("1.5")
        assert isinstance(entry.fee_usd, Decimal)
        # SL/TP rest reduce-only and unfilled → no fee yet (correctly None).
        assert sl.fee_usd is None
        assert tp.fee_usd is None

    async def test_close_fee_reconciled_from_user_fills(self) -> None:
        exchange, info = _open_ready_sdk()  # market_close oid 111 → matches the fill
        client = _client(exchange=exchange, info=info)
        (close,) = await client.execute_action(_flat_action("BTC"), "run-2", _open_position())
        assert close.order_kind == OrderKind.CLOSE
        # TRIPWIRE: close (taker_close) fee reconciled from user_fills, not None.
        assert close.fee_usd == Decimal("1.5")

    async def test_fee_summed_across_partial_fills(self) -> None:
        exchange, info = _open_ready_sdk()
        # One order (oid 111) filled in two partials; a stale fill for a different oid
        # must NOT be counted.
        info.user_fills.return_value = [
            _fill(oid=111, fee="0.90", sz="20.0"),
            _fill(oid=111, fee="0.60", sz="10.0"),
            _fill(oid=999, fee="5.00", sz="99.0"),  # different order — ignored
        ]
        client = _client(exchange=exchange, info=info)
        entry = (await client.execute_action(_long_action("BTC"), "run-3", None))[0]
        assert entry.fee_usd == Decimal("1.50")  # 0.90 + 0.60, oid 999 excluded

    async def test_fee_none_when_no_matching_fill_after_retries(self) -> None:
        exchange, info = _open_ready_sdk()
        info.user_fills.return_value = [_fill(oid=777, fee="9.9")]  # never matches oid 111
        client = _client(exchange=exchange, info=info)
        # Graceful degradation: fee stays None (no fee_event), loop is not blocked/crashed.
        # Patch sleep so the retry budget doesn't slow the suite.
        with patch("aiat.execution.hyperliquid_client.asyncio.sleep", new=AsyncMock()) as slept:
            entry = (await client.execute_action(_long_action("BTC"), "run-3", None))[0]
        assert entry.fee_usd is None
        assert slept.await_count == 2  # _FEE_RECONCILE_ATTEMPTS - 1 gaps

    async def test_fee_is_decimal_from_float_sdk(self) -> None:
        """Inv #12: a float fee from the SDK is reconverted to Decimal, never left a float."""
        exchange, info = _open_ready_sdk()
        info.user_fills.return_value = [_fill(oid=111, fee=1.5)]  # native float
        client = _client(exchange=exchange, info=info)
        entry = (await client.execute_action(_long_action("BTC"), "run-3", None))[0]
        assert entry.fee_usd == Decimal("1.5")
        assert isinstance(entry.fee_usd, Decimal)
        assert not isinstance(entry.fee_usd, float)

    async def test_check_position_closure_extracts_fee(self) -> None:
        info = MagicMock()
        info.user_state.return_value = _user_state(positions=[])  # BTC closed
        info.user_fills.return_value = [
            _fill(oid=111, coin="BTC", dir_="Close Long", fee="0.75", closed_pnl="25.0", px="110.0")
        ]
        client = _client(exchange=MagicMock(), info=info)
        closure = await client.check_position_closure("BTC")
        assert closure is not None
        # TRIPWIRE: the autonomous-close fee is captured on the closure info.
        assert closure.fee_usd == Decimal("0.75")


class TestDetectAutonomousClosure:
    """Per-POSITION autonomous SL/TP detection by trigger oid (T4b root-cause, ADR-0038).

    ``detect_autonomous_closure`` is the pure matcher the orchestrator-level ClosureReconciler
    uses instead of the old per-symbol ``check_position_closure``. It answers exactly one
    question — did THIS position's own stop_loss/take_profit trigger order fire? — by matching
    ``fills[*].oid`` against the position's trigger oids. There is deliberately NO ``szi``
    short-circuit, so a same-symbol reopen never masks the prior position's close.
    """

    def test_no_trigger_oids_returns_none(self) -> None:
        # A position with no recorded trigger oids can never be matched → still open.
        assert detect_autonomous_closure([_fill(oid=111)], set()) is None

    def test_no_matching_fill_returns_none(self) -> None:
        # The wallet traded (fills exist) but none carry this position's trigger oid → open.
        assert detect_autonomous_closure([_fill(oid=111)], {"999"}) is None

    def test_matched_trigger_builds_closure(self) -> None:
        closure = detect_autonomous_closure(
            [
                _fill(
                    oid=555, dir_="Close Long", fee="0.75", closed_pnl="25.0", px="110.0", sz="30.0"
                )
            ],
            {"555"},
        )
        assert closure is not None
        assert closure.exit_price == Decimal("110.0")
        assert closure.realized_pnl_usd == Decimal("25.0")
        assert closure.fee_usd == Decimal("0.75")
        # SL/TP attribution is the caller's job (ADR-0030); the raw closure is MODEL_CLOSE.
        assert closure.close_reason == CloseReason.MODEL_CLOSE

    def test_partials_of_the_fired_oid_sum_and_vwap(self) -> None:
        # Two partial fills of the SAME fired trigger oid: fee/pnl sum, exit is size-weighted VWAP.
        fills = [
            _fill(oid=555, fee="0.4", closed_pnl="10.0", px="100.0", sz="10.0", time_ms=2_000),
            _fill(oid=555, fee="0.6", closed_pnl="20.0", px="120.0", sz="30.0", time_ms=2_500),
        ]
        closure = detect_autonomous_closure(fills, {"555"})
        assert closure is not None
        assert closure.fee_usd == Decimal("1.0")
        assert closure.realized_pnl_usd == Decimal("30.0")
        # VWAP = (100*10 + 120*30) / 40 = 4600/40 = 115.
        assert closure.exit_price == Decimal("115")

    def test_only_the_most_recent_fired_oid_is_summed(self) -> None:
        # Both the SL and TP oids appear (abnormal): only the most-recent fired order is used,
        # never summed across the two triggers.
        fills = [
            _fill(oid=100, fee="0.5", closed_pnl="5.0", px="90.0", sz="30.0", time_ms=1_000),
            _fill(oid=200, fee="0.9", closed_pnl="40.0", px="130.0", sz="30.0", time_ms=9_000),
        ]
        closure = detect_autonomous_closure(fills, {"100", "200"})
        assert closure is not None
        assert closure.exit_price == Decimal("130.0")  # oid 200 (time 9000) wins
        assert closure.realized_pnl_usd == Decimal("40.0")
        assert closure.fee_usd == Decimal("0.9")

    def test_liquidation_flag_marks_liquidated(self) -> None:
        fill = _fill(oid=555, closed_pnl="-50.0", px="80.0")
        fill["liquidation"] = {"liquidatedUser": "0xabc"}  # truthy liquidation marker
        closure = detect_autonomous_closure([fill], {"555"})
        assert closure is not None
        assert closure.close_reason == CloseReason.LIQUIDATED

    def test_nonpositive_exit_price_returns_none(self) -> None:
        # A zero/garbage exit price is unusable → treat as not-detected rather than book bad data.
        closure = detect_autonomous_closure([_fill(oid=555, px="0", sz="0")], {"555"})
        assert closure is None

    def test_oid_matched_as_string_across_int_and_str(self) -> None:
        # Fills carry oid as int; trigger_oids are stored as str (hl_order_id) — match is by str().
        closure = detect_autonomous_closure([_fill(oid=555)], {"555"})
        assert closure is not None
