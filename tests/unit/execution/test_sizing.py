"""Unit tests for execution/sizing.py — TDD (M4-T01).

Verifies:
- Decimal precision: no float arithmetic anywhere
- notional_value_usd = price * size_units * leverage
- SL/TP price derivation for LONG and SHORT
"""

from decimal import Decimal

import pytest

from aiat.domain.enums import Side
from aiat.execution.sizing import compute_position_sizing


class TestComputePositionSizing:
    """Core sizing formula tests."""

    def test_notional_equals_price_times_size_units_times_leverage(self) -> None:
        """notional_value_usd = price * size_units * leverage (PRD §9.2)."""
        result = compute_position_sizing(
            equity_usd=Decimal("10000"),
            size_pct=Decimal("0.10"),
            entry_price=Decimal("50000"),
            leverage=Decimal("5"),
            side=Side.LONG,
            stop_loss_pct=Decimal("0.03"),
            take_profit_pct=Decimal("0.06"),
        )
        expected_notional = result.entry_price * result.size_units * result.leverage
        assert result.notional_value_usd == expected_notional

    def test_initial_margin_is_equity_times_size_pct(self) -> None:
        result = compute_position_sizing(
            equity_usd=Decimal("10000"),
            size_pct=Decimal("0.20"),
            entry_price=Decimal("2000"),
            leverage=Decimal("3"),
            side=Side.LONG,
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
        )
        assert result.initial_margin_usd == Decimal("10000") * Decimal("0.20")

    def test_size_units_is_margin_divided_by_price(self) -> None:
        equity = Decimal("1000")
        size_pct = Decimal("0.10")
        price = Decimal("100")
        leverage = Decimal("2")
        result = compute_position_sizing(
            equity_usd=equity,
            size_pct=size_pct,
            entry_price=price,
            leverage=leverage,
            side=Side.LONG,
            stop_loss_pct=Decimal("0.03"),
            take_profit_pct=Decimal("0.06"),
        )
        # size_units = (equity * size_pct) / price
        expected_units = (equity * size_pct) / price
        assert result.size_units == expected_units

    def test_notional_concrete_values(self) -> None:
        """Concrete spot-check: $1000 equity, 10% size, $100 price, 2x leverage.

        margin = 100, size_units = 1, notional = 1 * 100 * 2 = 200.
        """
        result = compute_position_sizing(
            equity_usd=Decimal("1000"),
            size_pct=Decimal("0.10"),
            entry_price=Decimal("100"),
            leverage=Decimal("2"),
            side=Side.LONG,
            stop_loss_pct=Decimal("0.03"),
            take_profit_pct=Decimal("0.06"),
        )
        assert result.initial_margin_usd == Decimal("100")
        assert result.size_units == Decimal("1")
        assert result.notional_value_usd == Decimal("200")


class TestSlTpPrices:
    """SL/TP price derivation tests."""

    def test_long_stop_loss_price(self) -> None:
        """LONG SL: entry_price * (1 - sl_pct)."""
        result = compute_position_sizing(
            equity_usd=Decimal("1000"),
            size_pct=Decimal("0.10"),
            entry_price=Decimal("100"),
            leverage=Decimal("2"),
            side=Side.LONG,
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
        )
        assert result.stop_loss_price == Decimal("100") * (Decimal("1") - Decimal("0.05"))

    def test_long_take_profit_price(self) -> None:
        """LONG TP: entry_price * (1 + tp_pct)."""
        result = compute_position_sizing(
            equity_usd=Decimal("1000"),
            size_pct=Decimal("0.10"),
            entry_price=Decimal("100"),
            leverage=Decimal("2"),
            side=Side.LONG,
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
        )
        assert result.take_profit_price == Decimal("100") * (Decimal("1") + Decimal("0.10"))

    def test_short_stop_loss_price(self) -> None:
        """SHORT SL: entry_price * (1 + sl_pct) — loss is in upward direction."""
        result = compute_position_sizing(
            equity_usd=Decimal("1000"),
            size_pct=Decimal("0.10"),
            entry_price=Decimal("100"),
            leverage=Decimal("2"),
            side=Side.SHORT,
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
        )
        assert result.stop_loss_price == Decimal("100") * (Decimal("1") + Decimal("0.05"))

    def test_short_take_profit_price(self) -> None:
        """SHORT TP: entry_price * (1 - tp_pct) — profit is in downward direction."""
        result = compute_position_sizing(
            equity_usd=Decimal("1000"),
            size_pct=Decimal("0.10"),
            entry_price=Decimal("100"),
            leverage=Decimal("2"),
            side=Side.SHORT,
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
        )
        assert result.take_profit_price == Decimal("100") * (Decimal("1") - Decimal("0.10"))

    def test_sl_tp_prices_are_positive(self) -> None:
        """Even very large SL/TP pct values must yield positive prices."""
        result = compute_position_sizing(
            equity_usd=Decimal("1000"),
            size_pct=Decimal("0.10"),
            entry_price=Decimal("100"),
            leverage=Decimal("1"),
            side=Side.LONG,
            stop_loss_pct=Decimal("0.50"),
            take_profit_pct=Decimal("0.50"),
        )
        assert result.stop_loss_price > Decimal("0")
        assert result.take_profit_price > Decimal("0")


class TestDecimalPrecision:
    """Verify no float arithmetic leaks through (invariant #12)."""

    def test_result_types_are_decimal(self) -> None:
        result = compute_position_sizing(
            equity_usd=Decimal("1234.56"),
            size_pct=Decimal("0.1500"),
            entry_price=Decimal("45678.90"),
            leverage=Decimal("3"),
            side=Side.LONG,
            stop_loss_pct=Decimal("0.03"),
            take_profit_pct=Decimal("0.06"),
        )
        assert isinstance(result.size_units, Decimal), "size_units must be Decimal"
        assert isinstance(result.notional_value_usd, Decimal), "notional_value_usd must be Decimal"
        assert isinstance(result.initial_margin_usd, Decimal), "initial_margin_usd must be Decimal"
        assert isinstance(result.stop_loss_price, Decimal), "stop_loss_price must be Decimal"
        assert isinstance(result.take_profit_price, Decimal), "take_profit_price must be Decimal"
        assert isinstance(result.entry_price, Decimal), "entry_price must be Decimal"
        assert isinstance(result.leverage, Decimal), "leverage must be Decimal"

    def test_notional_formula_pure_decimal(self) -> None:
        """Cross-verify that notional = price * size_units * leverage holds exactly."""
        result = compute_position_sizing(
            equity_usd=Decimal("7777.77"),
            size_pct=Decimal("0.1300"),
            entry_price=Decimal("3333.33"),
            leverage=Decimal("4"),
            side=Side.SHORT,
            stop_loss_pct=Decimal("0.04"),
            take_profit_pct=Decimal("0.08"),
        )
        assert result.notional_value_usd == result.entry_price * result.size_units * result.leverage

    def test_input_entry_price_echoed_back(self) -> None:
        """entry_price stored verbatim so caller can verify referential integrity."""
        price = Decimal("12345.6789")
        result = compute_position_sizing(
            equity_usd=Decimal("5000"),
            size_pct=Decimal("0.05"),
            entry_price=price,
            leverage=Decimal("2"),
            side=Side.LONG,
            stop_loss_pct=Decimal("0.02"),
            take_profit_pct=Decimal("0.04"),
        )
        assert result.entry_price == price


class TestPositionSizingDataclass:
    """PositionSizing is an immutable value object."""

    def test_is_frozen(self) -> None:
        result = compute_position_sizing(
            equity_usd=Decimal("1000"),
            size_pct=Decimal("0.10"),
            entry_price=Decimal("100"),
            leverage=Decimal("2"),
            side=Side.LONG,
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
        )
        with pytest.raises((AttributeError, TypeError)):
            result.size_units = Decimal("99")  # type: ignore[misc]
