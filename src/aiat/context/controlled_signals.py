"""Finalized controlled-signal vocabulary — D4 closes ADR-0012 (§6.2, §15.4).

``CONTROLLED_SIGNALS`` is the canonical set that must stay byte-identical with
``ControlledSignal = Literal[...]`` in ``domain/schemas.py`` (inv #6).  Both
are kept in sync by ``test_controlled_signals.py``; divergence breaks CI.

The prompt_template_hash (§3.2.1) depends on this vocabulary — do NOT alter
values after the experiment seed runs (M7 step 4).
"""

CONTROLLED_SIGNALS: frozenset[str] = frozenset(
    {
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
    }
)
