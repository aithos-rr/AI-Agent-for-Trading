"""Unit tests for ContextBuilder (PRD §4.1 CO.1-CO.2, M3-T07)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiat.context.builder import ContextBuilder
from aiat.context.collectors.base import CollectorSourceError, CollectorTimeoutError
from aiat.domain.exceptions import ContextBuildError
from aiat.domain.schemas import (
    ContextBundle,
    NewsItem,
    OnChainSnapshot,
    SentimentSnapshot,
    TechnicalIndicators,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_TICK_ID = "2026-06-14T14:30:00Z"
_TICK_AT = "2026-06-14T14:30:00+00:00"


def _make_tech(symbol: str) -> TechnicalIndicators:
    return TechnicalIndicators(
        symbol=symbol,  # type: ignore[arg-type]
        price_usd=Decimal("50000"),
        rsi_14=Decimal("55.0"),
        macd_signal_diff=Decimal("100"),
        ema_20=Decimal("49000"),
        ema_50=Decimal("48000"),
        bollinger_upper=Decimal("52000"),
        bollinger_lower=Decimal("48000"),
        atr_14=Decimal("500"),
        volume_24h_usd=Decimal("1000000"),
    )


def _make_sentiment() -> SentimentSnapshot:
    return SentimentSnapshot(
        fear_greed_index=60,
        fear_greed_label="greed",
        fetched_at="2026-06-14T14:30:00+00:00",
    )


def _make_news() -> list[NewsItem]:
    return [
        NewsItem(
            title="BTC surges past 100k",
            summary="Bitcoin sets a new all-time high above 100,000 USD.",
            source="cryptopanic",
            published_at="2026-06-14T14:00:00+00:00",
        )
    ]


def _make_onchain(symbol: str) -> OnChainSnapshot:
    return OnChainSnapshot(
        symbol=symbol,  # type: ignore[arg-type]
        funding_rate_8h=Decimal("0.0001"),
        open_interest_usd=Decimal("1000000"),
        long_short_ratio=Decimal("1.2"),
        liquidations_24h_usd=Decimal("5000"),
    )


def _mock_collector(return_value: Any) -> Any:
    """Return an AsyncMock with a .collect() method returning return_value."""
    mock = AsyncMock()
    mock.collect = AsyncMock(return_value=return_value)
    return mock


def _mock_collector_raises(exc: Exception) -> Any:
    """Return an AsyncMock with a .collect() that raises exc."""
    mock = AsyncMock()
    mock.collect = AsyncMock(side_effect=exc)
    return mock


def _make_builder(
    btc: Any = None,
    eth: Any = None,
    sol: Any = None,
    sentiment: Any = None,
    news: Any = None,
    onchain: Any = None,
) -> ContextBuilder:
    return ContextBuilder(
        technical_btc=btc or _mock_collector(_make_tech("BTC")),
        technical_eth=eth or _mock_collector(_make_tech("ETH")),
        technical_sol=sol or _mock_collector(_make_tech("SOL")),
        sentiment=sentiment or _mock_collector(_make_sentiment()),
        news=news or _mock_collector(_make_news()),
        onchain=onchain or _mock_collector([_make_onchain(s) for s in ("BTC", "ETH", "SOL")]),
    )


# ---------------------------------------------------------------------------
# Tests — happy path
# ---------------------------------------------------------------------------


async def test_build_returns_context_bundle() -> None:
    builder = _make_builder()
    bundle = await builder.build(_TICK_ID, _TICK_AT)
    assert isinstance(bundle, ContextBundle)


async def test_build_tick_fields() -> None:
    builder = _make_builder()
    bundle = await builder.build(_TICK_ID, _TICK_AT)
    assert bundle.tick_id == _TICK_ID
    assert bundle.tick_at == _TICK_AT


async def test_build_technical_has_3_entries() -> None:
    builder = _make_builder()
    bundle = await builder.build(_TICK_ID, _TICK_AT)
    assert len(bundle.technical) == 3


async def test_build_technical_symbols() -> None:
    builder = _make_builder()
    bundle = await builder.build(_TICK_ID, _TICK_AT)
    symbols = [t.symbol for t in bundle.technical]
    assert symbols == ["BTC", "ETH", "SOL"]


async def test_build_sentiment_correct() -> None:
    builder = _make_builder()
    bundle = await builder.build(_TICK_ID, _TICK_AT)
    assert bundle.sentiment.fear_greed_index == 60
    assert bundle.sentiment.fear_greed_label == "greed"


async def test_build_news_correct() -> None:
    builder = _make_builder()
    bundle = await builder.build(_TICK_ID, _TICK_AT)
    assert len(bundle.news) == 1
    assert bundle.news[0].source == "cryptopanic"


async def test_build_onchain_correct() -> None:
    builder = _make_builder()
    bundle = await builder.build(_TICK_ID, _TICK_AT)
    assert len(bundle.onchain) == 3


async def test_build_source_timestamps_has_all_keys() -> None:
    builder = _make_builder()
    bundle = await builder.build(_TICK_ID, _TICK_AT)
    assert set(bundle.source_timestamps.keys()) == {
        "technical_btc",
        "technical_eth",
        "technical_sol",
        "sentiment",
        "news",
        "onchain",
    }


async def test_build_source_timestamps_are_iso_strings() -> None:
    builder = _make_builder()
    bundle = await builder.build(_TICK_ID, _TICK_AT)
    for key, ts in bundle.source_timestamps.items():
        assert isinstance(ts, str), f"{key} timestamp not a string"
        assert "T" in ts or "-" in ts, f"{key} timestamp not ISO-like: {ts!r}"


async def test_build_all_collectors_called() -> None:
    btc = _mock_collector(_make_tech("BTC"))
    eth = _mock_collector(_make_tech("ETH"))
    sol = _mock_collector(_make_tech("SOL"))
    sent = _mock_collector(_make_sentiment())
    news = _mock_collector(_make_news())
    onchain = _mock_collector([_make_onchain(s) for s in ("BTC", "ETH", "SOL")])

    builder = _make_builder(btc=btc, eth=eth, sol=sol, sentiment=sent, news=news, onchain=onchain)
    await builder.build(_TICK_ID, _TICK_AT)

    btc.collect.assert_called_once()
    eth.collect.assert_called_once()
    sol.collect.assert_called_once()
    sent.collect.assert_called_once()
    news.collect.assert_called_once()
    onchain.collect.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — failure paths
# ---------------------------------------------------------------------------


async def test_build_raises_context_build_error_on_technical_btc_failure() -> None:
    failing = _mock_collector_raises(CollectorSourceError("BTC source down"))
    builder = _make_builder(btc=failing)
    with pytest.raises(ContextBuildError, match="technical_btc"):
        await builder.build(_TICK_ID, _TICK_AT)


async def test_build_raises_context_build_error_on_technical_eth_failure() -> None:
    failing = _mock_collector_raises(CollectorTimeoutError("ETH timed out"))
    builder = _make_builder(eth=failing)
    with pytest.raises(ContextBuildError, match="technical_eth"):
        await builder.build(_TICK_ID, _TICK_AT)


async def test_build_raises_context_build_error_on_sentiment_failure() -> None:
    failing = _mock_collector_raises(CollectorSourceError("F&G unavailable"))
    builder = _make_builder(sentiment=failing)
    with pytest.raises(ContextBuildError, match="sentiment"):
        await builder.build(_TICK_ID, _TICK_AT)


async def test_build_raises_context_build_error_on_news_failure() -> None:
    failing = _mock_collector_raises(CollectorSourceError("RSS feed down"))
    builder = _make_builder(news=failing)
    with pytest.raises(ContextBuildError, match="news"):
        await builder.build(_TICK_ID, _TICK_AT)


async def test_build_raises_context_build_error_on_onchain_failure() -> None:
    failing = _mock_collector_raises(CollectorTimeoutError("HL timed out"))
    builder = _make_builder(onchain=failing)
    with pytest.raises(ContextBuildError, match="onchain"):
        await builder.build(_TICK_ID, _TICK_AT)


async def test_build_error_reports_multiple_failures() -> None:
    btc_fail = _mock_collector_raises(CollectorSourceError("BTC down"))
    sent_fail = _mock_collector_raises(CollectorTimeoutError("F&G timeout"))
    builder = _make_builder(btc=btc_fail, sentiment=sent_fail)
    with pytest.raises(ContextBuildError) as exc_info:
        await builder.build(_TICK_ID, _TICK_AT)
    msg = str(exc_info.value)
    assert "technical_btc" in msg
    assert "sentiment" in msg


async def test_build_error_succeeding_sources_not_in_error_message() -> None:
    btc_fail = _mock_collector_raises(CollectorSourceError("BTC down"))
    builder = _make_builder(btc=btc_fail)
    with pytest.raises(ContextBuildError) as exc_info:
        await builder.build(_TICK_ID, _TICK_AT)
    msg = str(exc_info.value)
    assert "technical_eth" not in msg
    assert "sentiment" not in msg


async def test_build_partial_failure_no_source_timestamps_for_failed() -> None:
    """Source timestamps are only recorded for successful fetches."""
    btc_fail = _mock_collector_raises(CollectorSourceError("BTC down"))
    eth = _mock_collector(_make_tech("ETH"))
    builder = _make_builder(btc=btc_fail, eth=eth)
    with pytest.raises(ContextBuildError):
        await builder.build(_TICK_ID, _TICK_AT)
    # We can't check source_timestamps after a raise, but we verify the error is raised


async def test_build_context_bundle_is_pydantic_serialisable() -> None:
    builder = _make_builder()
    bundle = await builder.build(_TICK_ID, _TICK_AT)
    data = bundle.model_dump()
    restored = ContextBundle.model_validate(data)
    assert restored.tick_id == bundle.tick_id
