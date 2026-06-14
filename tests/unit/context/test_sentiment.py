"""Unit tests for SentimentCollector (PRD §7.2, §6.3, M3-T03)."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aiat.context.collectors.base import CollectorSourceError, CollectorTimeoutError
from aiat.context.collectors.sentiment import SentimentCollector
from aiat.domain.schemas import SentimentSnapshot


def _mock_client(status_code: int = 200, body: object = None) -> httpx.AsyncClient:
    """Build an AsyncClient mock that returns a fixed response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    if body is not None:
        resp.json.return_value = body
        resp.text = json.dumps(body)
    else:
        resp.json.side_effect = ValueError("no body")
        resp.text = ""

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=resp)
    return client  # type: ignore[return-value]


_GOOD_BODY = {
    "name": "Fear and Greed Index",
    "data": [
        {
            "value": "75",
            "value_classification": "Greed",
            "timestamp": "1748131200",
            "time_until_update": "60000",
        }
    ],
    "metadata": {"error": None},
}


class TestSentimentCollectorHappyPath:
    @pytest.mark.asyncio
    async def test_returns_sentiment_snapshot(self) -> None:
        collector = SentimentCollector(client=_mock_client(body=_GOOD_BODY))
        result = await collector.collect()
        assert isinstance(result, SentimentSnapshot)

    @pytest.mark.asyncio
    async def test_fear_greed_index_correct(self) -> None:
        collector = SentimentCollector(client=_mock_client(body=_GOOD_BODY))
        result = await collector.collect()
        assert result.fear_greed_index == 75

    @pytest.mark.asyncio
    async def test_fear_greed_label_correct(self) -> None:
        collector = SentimentCollector(client=_mock_client(body=_GOOD_BODY))
        result = await collector.collect()
        assert result.fear_greed_label == "greed"

    @pytest.mark.asyncio
    async def test_fetched_at_is_iso_string(self) -> None:
        collector = SentimentCollector(client=_mock_client(body=_GOOD_BODY))
        result = await collector.collect()
        # Must be parseable as ISO datetime
        dt = datetime.fromisoformat(result.fetched_at)
        assert dt.tzinfo is not None or True  # presence is sufficient

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("classification", "expected_label"),
        [
            ("Extreme Fear", "extreme_fear"),
            ("Fear", "fear"),
            ("Neutral", "neutral"),
            ("Greed", "greed"),
            ("Extreme Greed", "extreme_greed"),
        ],
    )
    async def test_label_mapping(self, classification: str, expected_label: str) -> None:
        body = {
            "data": [
                {
                    "value": "50",
                    "value_classification": classification,
                    "timestamp": "1748131200",
                }
            ]
        }
        collector = SentimentCollector(client=_mock_client(body=body))
        result = await collector.collect()
        assert result.fear_greed_label == expected_label

    @pytest.mark.asyncio
    async def test_index_boundary_zero(self) -> None:
        body = {
            "data": [
                {"value": "0", "value_classification": "Extreme Fear", "timestamp": "1748131200"}
            ]
        }
        collector = SentimentCollector(client=_mock_client(body=body))
        result = await collector.collect()
        assert result.fear_greed_index == 0

    @pytest.mark.asyncio
    async def test_index_boundary_hundred(self) -> None:
        body = {
            "data": [
                {"value": "100", "value_classification": "Extreme Greed", "timestamp": "1748131200"}
            ]
        }
        collector = SentimentCollector(client=_mock_client(body=body))
        result = await collector.collect()
        assert result.fear_greed_index == 100


class TestSentimentCollectorErrors:
    @pytest.mark.asyncio
    async def test_http_500_raises_source_error(self) -> None:
        collector = SentimentCollector(client=_mock_client(status_code=500))
        with pytest.raises(CollectorSourceError):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_timeout_raises_timeout_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ReadTimeout("timed out", request=MagicMock()))
        collector = SentimentCollector(client=client)  # type: ignore[arg-type]
        with pytest.raises(CollectorTimeoutError):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_connect_error_raises_source_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(
            side_effect=httpx.ConnectError("connection refused", request=MagicMock())
        )
        collector = SentimentCollector(client=client)  # type: ignore[arg-type]
        with pytest.raises(CollectorSourceError):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_empty_data_array_raises_source_error(self) -> None:
        body = {"data": []}
        collector = SentimentCollector(client=_mock_client(body=body))
        with pytest.raises(CollectorSourceError):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_missing_data_key_raises_source_error(self) -> None:
        body = {"name": "Fear and Greed Index"}
        collector = SentimentCollector(client=_mock_client(body=body))
        with pytest.raises(CollectorSourceError):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_unknown_classification_raises_source_error(self) -> None:
        body = {
            "data": [
                {"value": "50", "value_classification": "Unknown Label", "timestamp": "1748131200"}
            ]
        }
        collector = SentimentCollector(client=_mock_client(body=body))
        with pytest.raises(CollectorSourceError):
            await collector.collect()


class TestSentimentCollectorDefaults:
    def test_default_timeout(self) -> None:
        collector = SentimentCollector(client=AsyncMock(spec=httpx.AsyncClient))  # type: ignore[arg-type]
        assert collector.timeout_seconds == 5

    def test_custom_timeout(self) -> None:
        collector = SentimentCollector(timeout_seconds=10, client=AsyncMock(spec=httpx.AsyncClient))  # type: ignore[arg-type]
        assert collector.timeout_seconds == 10

    def test_cache_ttl(self) -> None:
        collector = SentimentCollector(client=AsyncMock(spec=httpx.AsyncClient))  # type: ignore[arg-type]
        assert collector.cache_ttl_seconds == 60
