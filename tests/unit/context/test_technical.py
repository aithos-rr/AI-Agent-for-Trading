"""Unit tests for TechnicalCollector (PRD §7.2, §6.3, inv #12)."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aiat.context.collectors.base import CollectorSourceError, CollectorTimeoutError
from aiat.context.collectors.technical import TechnicalCollector
from aiat.domain.schemas import TechnicalIndicators


def _make_candles(n: int = 200, base_price: float = 50000.0) -> list[dict[str, object]]:
    """Synthetic 15m candles with gently rising prices for stable indicators."""
    candles: list[dict[str, object]] = []
    for i in range(n):
        t = 1_700_000_000_000 + i * 15 * 60 * 1000
        price = base_price + i * 10.0
        candles.append(
            {
                "T": t + 15 * 60 * 1000 - 1,
                "c": str(price),
                "h": str(price * 1.001),
                "i": "15m",
                "l": str(price * 0.999),
                "n": 100,
                "o": str(price),
                "s": "BTC",
                "t": t,
                "v": "10.5",
            }
        )
    return candles


def _mock_client(candles: list[dict[str, object]]) -> httpx.AsyncClient:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = candles
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=resp)
    return client  # type: ignore[return-value]


async def test_collect_returns_technical_indicators() -> None:
    collector = TechnicalCollector(symbol="BTC", client=_mock_client(_make_candles()))
    result = await collector.collect()
    assert isinstance(result, TechnicalIndicators)
    assert result.symbol == "BTC"


async def test_all_fields_are_decimal() -> None:
    collector = TechnicalCollector(symbol="BTC", client=_mock_client(_make_candles()))
    result = await collector.collect()
    for field in (
        "price_usd",
        "rsi_14",
        "macd_signal_diff",
        "ema_20",
        "ema_50",
        "bollinger_upper",
        "bollinger_lower",
        "atr_14",
        "volume_24h_usd",
    ):
        assert isinstance(getattr(result, field), Decimal), f"{field} is not Decimal"


async def test_price_is_last_close() -> None:
    candles = _make_candles()
    collector = TechnicalCollector(symbol="BTC", client=_mock_client(candles))
    result = await collector.collect()
    expected = Decimal(str(candles[-1]["c"]))  # type: ignore[arg-type]
    assert result.price_usd == expected


async def test_price_usd_preserves_full_precision() -> None:
    """price_usd must come from the raw API string, not a lossy float round-trip (inv #12)."""
    candles = _make_candles()
    candles[-1]["c"] = "67123.123456789012"  # 17 sig digits > float64 precision
    collector = TechnicalCollector(symbol="BTC", client=_mock_client(candles))
    result = await collector.collect()
    assert result.price_usd == Decimal("67123.123456789012")


async def test_volume_24h_positive() -> None:
    collector = TechnicalCollector(symbol="ETH", client=_mock_client(_make_candles()))
    result = await collector.collect()
    assert result.volume_24h_usd > Decimal("0")


async def test_bollinger_upper_above_lower() -> None:
    collector = TechnicalCollector(symbol="SOL", client=_mock_client(_make_candles()))
    result = await collector.collect()
    assert result.bollinger_upper > result.bollinger_lower


async def test_ema50_below_ema20_in_rising_market() -> None:
    """In a steadily rising market EMA20 trails faster → EMA20 > EMA50."""
    collector = TechnicalCollector(symbol="BTC", client=_mock_client(_make_candles()))
    result = await collector.collect()
    assert result.ema_20 > result.ema_50


async def test_symbol_uppercased() -> None:
    collector = TechnicalCollector(symbol="btc", client=_mock_client(_make_candles()))
    result = await collector.collect()
    assert result.symbol == "BTC"


async def test_raises_source_error_on_http_500() -> None:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 500
    resp.text = "Internal Server Error"
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=resp)
    collector = TechnicalCollector(symbol="BTC", client=client)  # type: ignore[arg-type]
    with pytest.raises(CollectorSourceError):
        await collector.collect()


async def test_raises_source_error_on_empty_candles() -> None:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = []
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=resp)
    collector = TechnicalCollector(symbol="BTC", client=client)  # type: ignore[arg-type]
    with pytest.raises(CollectorSourceError):
        await collector.collect()


async def test_raises_timeout_error() -> None:
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
    collector = TechnicalCollector(symbol="BTC", client=client)  # type: ignore[arg-type]
    with pytest.raises(CollectorTimeoutError):
        await collector.collect()


async def test_raises_source_error_on_network_failure() -> None:
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    collector = TechnicalCollector(symbol="BTC", client=client)  # type: ignore[arg-type]
    with pytest.raises(CollectorSourceError):
        await collector.collect()


async def test_raises_source_error_on_unsupported_symbol() -> None:
    client = _mock_client(_make_candles())
    collector = TechnicalCollector(symbol="DOGE", client=client)
    with pytest.raises(CollectorSourceError, match="Unsupported symbol"):
        await collector.collect()


async def test_raises_source_error_on_invalid_json() -> None:
    # resp.json() raises an exception → CollectorSourceError (lines 93-94)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.text = "not json"
    resp.json.side_effect = ValueError("invalid json")
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=resp)
    collector = TechnicalCollector(symbol="BTC", client=client)  # type: ignore[arg-type]
    with pytest.raises(CollectorSourceError):
        await collector.collect()


async def test_raises_source_error_on_malformed_candle_keys() -> None:
    # Candles missing "o" key → KeyError caught at lines 117-118
    bad_candles: list[dict[str, object]] = [
        {"c": "50000", "h": "50001", "l": "49999", "v": "10"} for _ in range(100)
    ]
    collector = TechnicalCollector(symbol="BTC", client=_mock_client(bad_candles))
    with pytest.raises(CollectorSourceError, match="Malformed candle"):
        await collector.collect()


async def test_raises_source_error_on_insufficient_candles() -> None:
    # 49 candles < required 50 (line 121)
    collector = TechnicalCollector(symbol="BTC", client=_mock_client(_make_candles(n=49)))
    with pytest.raises(CollectorSourceError, match="Insufficient candles"):
        await collector.collect()


def test_default_timeout_is_10() -> None:
    # PRD §4.1: technical source timeout is 10s.
    collector = TechnicalCollector(symbol="BTC", client=MagicMock(spec=httpx.AsyncClient))
    assert collector.timeout_seconds == 10


def test_custom_timeout() -> None:
    collector = TechnicalCollector(
        symbol="BTC", client=MagicMock(spec=httpx.AsyncClient), timeout_seconds=5
    )
    assert collector.timeout_seconds == 5
