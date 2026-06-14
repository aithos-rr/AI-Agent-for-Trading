"""Position sizing utilities (§9.2, invariant #12).

All arithmetic uses Decimal — never float.

Formulae:
    initial_margin_usd  = equity_usd * size_pct
    size_units          = initial_margin_usd / entry_price
    notional_value_usd  = entry_price * size_units * leverage
                        = initial_margin_usd * leverage

Stop-loss / take-profit prices:
    LONG  SL = entry_price * (1 - sl_pct)   TP = entry_price * (1 + tp_pct)
    SHORT SL = entry_price * (1 + sl_pct)   TP = entry_price * (1 - tp_pct)
"""

from dataclasses import dataclass
from decimal import Decimal

from aiat.domain.enums import Side


@dataclass(frozen=True)
class PositionSizing:
    """Immutable value object returned by :func:`compute_position_sizing`."""

    entry_price: Decimal
    leverage: Decimal
    size_units: Decimal
    notional_value_usd: Decimal
    initial_margin_usd: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal


def compute_position_sizing(
    *,
    equity_usd: Decimal,
    size_pct: Decimal,
    entry_price: Decimal,
    leverage: Decimal,
    side: Side,
    stop_loss_pct: Decimal,
    take_profit_pct: Decimal,
) -> PositionSizing:
    """Compute all position sizing values from raw decision parameters.

    Args:
        equity_usd: Total equity available to the model (margin base).
        size_pct: Fraction of equity to allocate as margin [0, 1].
        entry_price: Current market price for the asset.
        leverage: Leverage multiplier (≥ 1).
        side: LONG or SHORT (determines SL/TP direction).
        stop_loss_pct: Fractional distance for SL from entry (> 0).
        take_profit_pct: Fractional distance for TP from entry (> 0).

    Returns:
        :class:`PositionSizing` with all Decimal fields set.
    """
    initial_margin_usd = equity_usd * size_pct
    size_units = initial_margin_usd / entry_price
    notional_value_usd = entry_price * size_units * leverage

    one = Decimal("1")
    if side == Side.LONG:
        stop_loss_price = entry_price * (one - stop_loss_pct)
        take_profit_price = entry_price * (one + take_profit_pct)
    else:  # SHORT
        stop_loss_price = entry_price * (one + stop_loss_pct)
        take_profit_price = entry_price * (one - take_profit_pct)

    return PositionSizing(
        entry_price=entry_price,
        leverage=leverage,
        size_units=size_units,
        notional_value_usd=notional_value_usd,
        initial_margin_usd=initial_margin_usd,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
    )
