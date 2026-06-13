"""Tests for Pydantic roundtrip serialization (§6.3, §6.4, M1-T03)."""

import json
from decimal import Decimal

from aiat.domain.schemas import (
    ActionDecision,
    ContextBundle,
    CostEventData,
    NewsItem,
    OnChainSnapshot,
    SentimentSnapshot,
    TechnicalIndicators,
    TradeDecision,
)


def _make_action(symbol: str = "BTC") -> dict:
    return {
        "symbol": symbol,
        "side": "HOLD",
        "leverage": "0",
        "size_pct": "0",
        "stop_loss_pct": None,
        "take_profit_pct": None,
        "entry_type": "none",
        "limit_price": None,
        "confidence": "0.5000",
        "time_horizon_min": 60,
        "action_reasoning": "No strong signal detected at this time.",
        "action_key_signals": [],
    }


def _make_trade_decision() -> dict:
    return {
        "portfolio_reasoning": "A" * 50,
        "risk_assessment": "B" * 30,
        "portfolio_confidence": "0.5000",
        "actions": [_make_action("BTC"), _make_action("ETH"), _make_action("SOL")],
    }


def test_trade_decision_roundtrip() -> None:
    data = _make_trade_decision()
    td = TradeDecision(**data)
    serialized = td.model_dump()
    td2 = TradeDecision(**serialized)
    assert td == td2


def test_trade_decision_json_roundtrip() -> None:
    td = TradeDecision(**_make_trade_decision())
    json_str = td.model_dump_json()
    td2 = TradeDecision.model_validate_json(json_str)
    assert td.actions[0].symbol == td2.actions[0].symbol
    assert td.actions[0].confidence == td2.actions[0].confidence


def test_context_bundle_roundtrip() -> None:
    bundle = ContextBundle(
        tick_id="tick-001",
        tick_at="2026-01-01T00:00:00Z",
        technical=[
            TechnicalIndicators(
                symbol="BTC",
                price_usd=Decimal("50000"),
                rsi_14=Decimal("55"),
                macd_signal_diff=Decimal("10"),
                ema_20=Decimal("49000"),
                ema_50=Decimal("47000"),
                bollinger_upper=Decimal("52000"),
                bollinger_lower=Decimal("48000"),
                atr_14=Decimal("1000"),
                volume_24h_usd=Decimal("1e9"),
            )
        ],
        sentiment=SentimentSnapshot(
            fear_greed_index=55,
            fear_greed_label="greed",
            fetched_at="2026-01-01T00:00:00Z",
        ),
        news=[
            NewsItem(
                title="Bitcoin rises",
                summary="BTC up 5% today.",
                source="CryptoPanic",
                published_at="2026-01-01T00:00:00Z",
                sentiment_polarity=Decimal("0.5"),
            )
        ],
        onchain=[
            OnChainSnapshot(
                symbol="BTC",
                funding_rate_8h=Decimal("0.0001"),
                open_interest_usd=Decimal("1e10"),
                long_short_ratio=Decimal("1.2"),
                liquidations_24h_usd=Decimal("5e6"),
            )
        ],
        source_timestamps={"technical": "2026-01-01T00:00:00Z"},
    )
    data = bundle.model_dump()
    bundle2 = ContextBundle(**data)
    assert bundle2.tick_id == "tick-001"
    assert bundle2.technical[0].price_usd == Decimal("50000")


def test_cost_event_data_roundtrip() -> None:
    cost = CostEventData(
        input_tokens=1000,
        output_tokens=200,
        reasoning_tokens=50,
        cost_usd=Decimal("0.00123456"),
        pricing_snapshot={"input": Decimal("0.003"), "output": Decimal("0.015")},
        n_attempts=1,
    )
    data = cost.model_dump()
    cost2 = CostEventData(**data)
    assert cost2.cost_usd == Decimal("0.00123456")
    assert cost2.n_attempts == 1


def test_cost_event_data_decimal_precision() -> None:
    cost = CostEventData(
        input_tokens=0,
        output_tokens=0,
        cost_usd=Decimal("0.00000001"),
        pricing_snapshot={},
    )
    assert cost.cost_usd == Decimal("0.00000001")
    assert isinstance(cost.cost_usd, Decimal)
