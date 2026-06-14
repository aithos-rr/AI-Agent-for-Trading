"""Tests that CONTROLLED_SIGNALS stays in sync with ControlledSignal Literal."""

from typing import get_args

import pytest

from aiat.context.controlled_signals import CONTROLLED_SIGNALS
from aiat.domain.schemas import ControlledSignal


def test_controlled_signals_matches_literal() -> None:
    """CONTROLLED_SIGNALS and ControlledSignal Literal must be identical sets."""
    assert set(get_args(ControlledSignal)) == CONTROLLED_SIGNALS


def test_controlled_signals_count() -> None:
    assert len(CONTROLLED_SIGNALS) == 18


def test_controlled_signals_categories() -> None:
    categories = {s.split(".")[0] for s in CONTROLLED_SIGNALS}
    assert categories == {"technical", "sentiment", "onchain", "market", "portfolio"}


@pytest.mark.parametrize(
    "signal",
    [
        "technical.rsi_extreme",
        "technical.macd_cross",
        "technical.ema_alignment",
        "technical.bollinger_squeeze",
        "technical.atr_spike",
        "technical.support_resistance",
        "sentiment.news_polarity",
        "sentiment.fear_greed",
        "sentiment.market_panic",
        "onchain.funding_rate_extreme",
        "onchain.open_interest_shift",
        "onchain.liquidation_cascade",
        "market.volatility_regime",
        "market.volume_anomaly",
        "market.basis_perp_spot",
        "portfolio.exposure_high",
        "portfolio.unrealized_pnl",
        "portfolio.position_aging",
    ],
)
def test_each_signal_present(signal: str) -> None:
    assert signal in CONTROLLED_SIGNALS


def test_signals_have_category_prefix() -> None:
    for signal in CONTROLLED_SIGNALS:
        parts = signal.split(".")
        assert len(parts) == 2, f"{signal!r} must be 'category.name'"
        assert parts[0], "category must be non-empty"
        assert parts[1], "name must be non-empty"
