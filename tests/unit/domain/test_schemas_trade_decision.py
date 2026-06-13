"""Tests for TradeDecision / ActionDecision schemas (§6.2, §9.2)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from aiat.domain.schemas import ActionDecision, TradeDecision

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HOLD_ACTION_BTC: dict = {
    "symbol": "BTC",
    "side": "HOLD",
    "leverage": "0",
    "size_pct": "0",
    "stop_loss_pct": None,
    "take_profit_pct": None,
    "entry_type": "none",
    "limit_price": None,
    "confidence": "0.5",
    "time_horizon_min": 60,
    "action_reasoning": "Market is ranging, no strong signal detected.",
    "action_key_signals": [],
}

LONG_ACTION: dict = {
    "side": "LONG",
    "leverage": "2",
    "size_pct": "0.10",
    "stop_loss_pct": "0.02",
    "take_profit_pct": "0.04",
    "entry_type": "market",
    "limit_price": None,
    "confidence": "0.65",
    "time_horizon_min": 30,
    "action_reasoning": "RSI oversold + MACD bullish crossover detected.",
    "action_key_signals": ["technical.rsi_extreme"],
}


def make_trade_decision(
    btc: dict, eth: dict | None = None, sol: dict | None = None
) -> dict:
    eth = eth or {**HOLD_ACTION_BTC, "symbol": "ETH"}
    sol = sol or {**HOLD_ACTION_BTC, "symbol": "SOL"}
    return {
        "portfolio_reasoning": "A" * 50,
        "risk_assessment": "B" * 30,
        "portfolio_confidence": "0.6",
        "actions": [btc, eth, sol],
    }


# ---------------------------------------------------------------------------
# Case 1: Valid — 3 actions BTC/ETH/SOL
# ---------------------------------------------------------------------------


def test_valid_3_actions_btc_eth_sol() -> None:
    td = TradeDecision(**make_trade_decision(HOLD_ACTION_BTC))
    symbols = {a.symbol for a in td.actions}
    assert symbols == {"BTC", "ETH", "SOL"}


# ---------------------------------------------------------------------------
# Case 2: Rifiuta 4 actions (min_length/max_length=3 + unique symbols)
# ---------------------------------------------------------------------------


def test_rejects_4_actions() -> None:
    extra_action = {**HOLD_ACTION_BTC, "symbol": "BTC"}  # duplicate BTC
    data = {
        "portfolio_reasoning": "A" * 50,
        "risk_assessment": "B" * 30,
        "actions": [
            HOLD_ACTION_BTC,
            {**HOLD_ACTION_BTC, "symbol": "ETH"},
            {**HOLD_ACTION_BTC, "symbol": "SOL"},
            extra_action,
        ],
    }
    with pytest.raises(ValidationError):
        TradeDecision(**data)


# ---------------------------------------------------------------------------
# Case 3: HOLD + size_pct > 0 — invalid
# ---------------------------------------------------------------------------


def test_hold_with_positive_size_raises() -> None:
    bad = {**HOLD_ACTION_BTC, "size_pct": "0.05"}
    with pytest.raises(ValidationError, match="HOLD/FLAT must have size_pct=0"):
        ActionDecision(**bad)


# ---------------------------------------------------------------------------
# Case 4: LONG without SL/TP — invalid (Figma F1)
# ---------------------------------------------------------------------------


def test_long_without_sl_tp_raises() -> None:
    bad = {
        **LONG_ACTION,
        "symbol": "BTC",
        "stop_loss_pct": None,
        "take_profit_pct": None,
    }
    with pytest.raises(ValidationError, match="must declare both SL and TP"):
        ActionDecision(**bad)


# ---------------------------------------------------------------------------
# Case 5: limit entry without limit_price — invalid
# ---------------------------------------------------------------------------


def test_limit_entry_without_limit_price_raises() -> None:
    bad = {
        **LONG_ACTION,
        "symbol": "BTC",
        "entry_type": "limit",
        "limit_price": None,
    }
    with pytest.raises(ValidationError, match="requires limit_price"):
        ActionDecision(**bad)


# ---------------------------------------------------------------------------
# Case 6: action_key_signals with unknown signal — invalid
# ---------------------------------------------------------------------------


def test_unknown_signal_raises() -> None:
    bad = {**HOLD_ACTION_BTC, "action_key_signals": ["rsi_is_overbought"]}
    with pytest.raises(ValidationError):
        ActionDecision(**bad)


# ---------------------------------------------------------------------------
# Case 7: confidence at boundaries (0 and 1 both valid)
# ---------------------------------------------------------------------------


def test_confidence_boundary_valid() -> None:
    low = ActionDecision(**{**HOLD_ACTION_BTC, "confidence": "0"})
    high = ActionDecision(**{**HOLD_ACTION_BTC, "confidence": "1"})
    assert low.confidence == Decimal("0")
    assert high.confidence == Decimal("1")


# ---------------------------------------------------------------------------
# Case 8: confidence outside [0, 1] — invalid
# ---------------------------------------------------------------------------


def test_confidence_out_of_range_raises() -> None:
    with pytest.raises(ValidationError):
        ActionDecision(**{**HOLD_ACTION_BTC, "confidence": "1.1"})
    with pytest.raises(ValidationError):
        ActionDecision(**{**HOLD_ACTION_BTC, "confidence": "-0.01"})


# ---------------------------------------------------------------------------
# Additional: market entry must NOT specify limit_price
# ---------------------------------------------------------------------------


def test_market_entry_with_limit_price_raises() -> None:
    bad = {**LONG_ACTION, "symbol": "BTC", "limit_price": "50000"}
    with pytest.raises(ValidationError, match="must not specify limit_price"):
        ActionDecision(**bad)


# ---------------------------------------------------------------------------
# Additional: FLAT also rejects size_pct > 0
# ---------------------------------------------------------------------------


def test_flat_with_positive_size_raises() -> None:
    bad = {**HOLD_ACTION_BTC, "side": "FLAT", "size_pct": "0.05"}
    with pytest.raises(ValidationError):
        ActionDecision(**bad)


# ---------------------------------------------------------------------------
# Additional: Decimal (not float) used for monetary fields
# ---------------------------------------------------------------------------


def test_decimal_types() -> None:
    action = ActionDecision(**{**LONG_ACTION, "symbol": "BTC"})
    assert isinstance(action.leverage, Decimal)
    assert isinstance(action.size_pct, Decimal)
    assert isinstance(action.confidence, Decimal)
