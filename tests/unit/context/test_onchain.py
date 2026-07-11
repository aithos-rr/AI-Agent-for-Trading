"""Unit tests for OnchainCollector and HLPublicInfoClient (PRD §7.2, §6.3, M3-T05)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aiat.context.collectors.base import CollectorSourceError, CollectorTimeoutError
from aiat.context.collectors.onchain import HLPublicInfoClient, OnchainCollector
from aiat.domain.schemas import OnChainSnapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meta() -> dict[str, object]:
    return {
        "universe": [
            {"name": "BTC", "szDecimals": 3, "maxLeverage": 50},
            {"name": "ETH", "szDecimals": 4, "maxLeverage": 50},
            {"name": "SOL", "szDecimals": 2, "maxLeverage": 25},
        ]
    }


def _make_asset_ctxs() -> list[dict[str, object]]:
    return [
        {
            "funding": "0.0000125",
            "openInterest": "37854.1",
            "prevDayPx": "92000.0",
            "dayNtlVlm": "1234567890.0",
            "markPx": "91950.0",
            "premium": "-0.0002442114",
            "impactPxs": ["91960.0", "91940.0"],
        },
        {
            "funding": "-0.0000050",
            "openInterest": "245780.0",
            "prevDayPx": "3200.0",
            "dayNtlVlm": "567890123.0",
            "markPx": "3250.0",
            "premium": "0.0001500000",
            "impactPxs": ["3249.0", "3251.0"],
        },
        {
            "funding": "0.0000200",
            "openInterest": "1234567.0",
            "prevDayPx": "145.0",
            "dayNtlVlm": "89012345.0",
            "markPx": "148.0",
            "premium": "-0.0000500000",
            "impactPxs": ["148.1", "147.9"],
        },
    ]


def _mock_response(status_code: int = 200, body: object = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body if body is not None else [_make_meta(), _make_asset_ctxs()]
    resp.text = ""
    return resp


def _hl_client_from_mock(resp: MagicMock) -> HLPublicInfoClient:
    inner = MagicMock(spec=httpx.AsyncClient)
    inner.post = AsyncMock(return_value=resp)
    return HLPublicInfoClient(client=inner)


def _hl_client_raising(exc: Exception) -> HLPublicInfoClient:
    inner = MagicMock(spec=httpx.AsyncClient)
    inner.post = AsyncMock(side_effect=exc)
    return HLPublicInfoClient(client=inner)


def _collector(body: object = None, status: int = 200) -> OnchainCollector:
    return OnchainCollector(hl_client=_hl_client_from_mock(_mock_response(status, body)))


# ---------------------------------------------------------------------------
# HLPublicInfoClient tests
# ---------------------------------------------------------------------------


def _funding_record(coin: str = "BTC", usdc: str = "-0.31", rate: str = "0.0000125") -> dict:
    """A Hyperliquid ``userFunding`` record (real shape — finding B / ADR-0031)."""
    return {
        "time": 1_683_849_600_076,
        "hash": "0xabc",
        "delta": {
            "type": "funding",
            "coin": coin,
            "usdc": usdc,
            "szi": "1.0",
            "fundingRate": rate,
        },
    }


class TestHLPublicInfoClientUserFunding:
    @pytest.mark.asyncio
    async def test_returns_list_of_records(self) -> None:
        resp = _mock_response(body=[_funding_record()])
        hl = _hl_client_from_mock(resp)
        out = await hl.user_funding_history("0xdead", 1_683_800_000_000)
        assert isinstance(out, list)
        assert out[0]["delta"]["coin"] == "BTC"  # type: ignore[index]

    @pytest.mark.asyncio
    async def test_sends_userfunding_payload(self) -> None:
        inner = MagicMock(spec=httpx.AsyncClient)
        inner.post = AsyncMock(return_value=_mock_response(body=[]))
        hl = HLPublicInfoClient(network="testnet", client=inner)
        await hl.user_funding_history("0xwallet", 111, 222)
        payload = inner.post.call_args.kwargs["json"]
        assert payload == {
            "type": "userFunding",
            "user": "0xwallet",
            "startTime": 111,
            "endTime": 222,
        }

    @pytest.mark.asyncio
    async def test_non_200_raises(self) -> None:
        hl = _hl_client_from_mock(_mock_response(status_code=500))
        with pytest.raises(CollectorSourceError):
            await hl.user_funding_history("0xdead", 1)

    @pytest.mark.asyncio
    async def test_non_list_body_raises(self) -> None:
        hl = _hl_client_from_mock(_mock_response(body={"unexpected": "dict"}))
        with pytest.raises(CollectorSourceError):
            await hl.user_funding_history("0xdead", 1)


class TestHLPublicInfoClientUserFillsByTime:
    @pytest.mark.asyncio
    async def test_returns_fills_and_sends_payload(self) -> None:
        inner = MagicMock(spec=httpx.AsyncClient)
        inner.post = AsyncMock(
            return_value=_mock_response(body=[{"oid": 111, "fee": "1.5", "coin": "BTC"}])
        )
        hl = HLPublicInfoClient(network="testnet", client=inner)
        out = await hl.user_fills_by_time("0xwallet", 100, 200)
        assert out[0]["oid"] == 111
        assert inner.post.call_args.kwargs["json"] == {
            "type": "userFillsByTime",
            "user": "0xwallet",
            "startTime": 100,
            "endTime": 200,
        }

    @pytest.mark.asyncio
    async def test_non_200_raises(self) -> None:
        hl = _hl_client_from_mock(_mock_response(status_code=500))
        with pytest.raises(CollectorSourceError):
            await hl.user_fills_by_time("0xdead", 1)


class TestHLPublicInfoClientFetchMeta:
    @pytest.mark.asyncio
    async def test_returns_dict_with_universe(self) -> None:
        inner = MagicMock(spec=httpx.AsyncClient)
        resp = _mock_response(body=_make_meta())
        inner.post = AsyncMock(return_value=resp)
        hl = HLPublicInfoClient(client=inner)
        meta = await hl.fetch_meta()
        assert isinstance(meta, dict)
        assert "universe" in meta

    @pytest.mark.asyncio
    async def test_fetch_meta_non_200_raises_source_error(self) -> None:
        # fetch_meta() non-200 response → CollectorSourceError (line 57)
        inner = MagicMock(spec=httpx.AsyncClient)
        inner.post = AsyncMock(return_value=_mock_response(status_code=500))
        hl = HLPublicInfoClient(client=inner)
        with pytest.raises(CollectorSourceError):
            await hl.fetch_meta()

    @pytest.mark.asyncio
    async def test_testnet_url_contains_testnet(self) -> None:
        inner = MagicMock(spec=httpx.AsyncClient)
        resp = _mock_response(body=_make_meta())
        inner.post = AsyncMock(return_value=resp)
        hl = HLPublicInfoClient(network="testnet", client=inner)
        await hl.fetch_meta()
        call_url: str = inner.post.call_args[0][0]
        assert "testnet" in call_url

    @pytest.mark.asyncio
    async def test_mainnet_url_omits_testnet(self) -> None:
        inner = MagicMock(spec=httpx.AsyncClient)
        resp = _mock_response(body=_make_meta())
        inner.post = AsyncMock(return_value=resp)
        hl = HLPublicInfoClient(network="mainnet", client=inner)
        await hl.fetch_meta()
        call_url: str = inner.post.call_args[0][0]
        assert "testnet" not in call_url

    @pytest.mark.asyncio
    async def test_custom_base_url(self) -> None:
        inner = MagicMock(spec=httpx.AsyncClient)
        resp = _mock_response(body=_make_meta())
        inner.post = AsyncMock(return_value=resp)
        hl = HLPublicInfoClient(base_url="https://custom.example.com", client=inner)
        await hl.fetch_meta()
        call_url: str = inner.post.call_args[0][0]
        assert "custom.example.com" in call_url


# ---------------------------------------------------------------------------
# OnchainCollector happy-path tests
# ---------------------------------------------------------------------------


class TestOnchainCollectorHappyPath:
    @pytest.mark.asyncio
    async def test_returns_three_snapshots(self) -> None:
        result = await _collector().collect()
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_all_items_are_onchain_snapshots(self) -> None:
        result = await _collector().collect()
        for snap in result:
            assert isinstance(snap, OnChainSnapshot)

    @pytest.mark.asyncio
    async def test_symbols_in_correct_order(self) -> None:
        result = await _collector().collect()
        assert [s.symbol for s in result] == ["BTC", "ETH", "SOL"]

    @pytest.mark.asyncio
    async def test_all_numeric_fields_are_decimal(self) -> None:
        result = await _collector().collect()
        for snap in result:
            assert isinstance(snap.funding_rate_8h, Decimal)
            assert isinstance(snap.open_interest_usd, Decimal)
            assert isinstance(snap.premium, Decimal)
            assert isinstance(snap.liquidations_24h_usd, Decimal)

    @pytest.mark.asyncio
    async def test_btc_funding_rate_is_hourly_times_8(self) -> None:
        # HL funding is hourly; we store the 8h-equivalent (×8). ADR-0013.
        result = await _collector().collect()
        assert result[0].funding_rate_8h == Decimal("0.0000125") * 8  # == 0.0001

    @pytest.mark.asyncio
    async def test_eth_negative_funding_rate_times_8(self) -> None:
        result = await _collector().collect()
        assert result[1].funding_rate_8h == Decimal("-0.0000050") * 8  # == -0.00004

    @pytest.mark.asyncio
    async def test_btc_open_interest_usd_is_oi_times_mark(self) -> None:
        result = await _collector().collect()
        btc = result[0]
        expected = Decimal("37854.1") * Decimal("91950.0")
        assert btc.open_interest_usd == expected

    @pytest.mark.asyncio
    async def test_open_interest_is_positive(self) -> None:
        result = await _collector().collect()
        for snap in result:
            assert snap.open_interest_usd > 0

    @pytest.mark.asyncio
    async def test_btc_premium_from_ctx(self) -> None:
        # premium (perp vs oracle) replaces the meaningless impactPxs ratio. ADR-0013.
        result = await _collector().collect()
        assert result[0].premium == Decimal("-0.0002442114")

    @pytest.mark.asyncio
    async def test_btc_liquidations_from_day_ntl_vlm(self) -> None:
        result = await _collector().collect()
        btc = result[0]
        expected = Decimal("1234567890.0") * Decimal("0.001")
        assert btc.liquidations_24h_usd == expected

    @pytest.mark.asyncio
    async def test_missing_premium_raises_source_error(self) -> None:
        meta = _make_meta()
        ctxs = _make_asset_ctxs()
        del ctxs[0]["premium"]
        collector = OnchainCollector(
            hl_client=_hl_client_from_mock(_mock_response(body=[meta, ctxs]))
        )
        with pytest.raises(CollectorSourceError, match="Malformed asset context"):
            await collector.collect()


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestOnchainCollectorErrors:
    @pytest.mark.asyncio
    async def test_http_500_raises_source_error(self) -> None:
        with pytest.raises(CollectorSourceError):
            await _collector(status=500).collect()

    @pytest.mark.asyncio
    async def test_read_timeout_raises_timeout_error(self) -> None:
        collector = OnchainCollector(hl_client=_hl_client_raising(httpx.ReadTimeout("timeout")))
        with pytest.raises(CollectorTimeoutError):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_connect_error_raises_source_error(self) -> None:
        collector = OnchainCollector(
            hl_client=_hl_client_raising(httpx.ConnectError("conn failed"))
        )
        with pytest.raises(CollectorSourceError):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_missing_symbol_raises_source_error(self) -> None:
        meta = {"universe": [{"name": "BTC", "szDecimals": 3}, {"name": "ETH", "szDecimals": 4}]}
        ctxs = _make_asset_ctxs()[:2]
        collector = OnchainCollector(
            hl_client=_hl_client_from_mock(_mock_response(body=[meta, ctxs]))
        )
        with pytest.raises(CollectorSourceError, match="SOL"):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_missing_universe_raises_source_error(self) -> None:
        meta: dict[str, object] = {}
        ctxs = _make_asset_ctxs()
        collector = OnchainCollector(
            hl_client=_hl_client_from_mock(_mock_response(body=[meta, ctxs]))
        )
        with pytest.raises(CollectorSourceError):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_malformed_ctx_missing_funding_raises_source_error(self) -> None:
        meta = _make_meta()
        ctxs = _make_asset_ctxs()
        del ctxs[0]["funding"]
        collector = OnchainCollector(
            hl_client=_hl_client_from_mock(_mock_response(body=[meta, ctxs]))
        )
        with pytest.raises(CollectorSourceError):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_non_numeric_funding_raises_source_error(self) -> None:
        # A non-numeric numeric field must surface as CollectorSourceError, not a
        # raw decimal.InvalidOperation leaking out of the collector contract.
        meta = _make_meta()
        ctxs = _make_asset_ctxs()
        ctxs[0]["funding"] = "N/A"
        collector = OnchainCollector(
            hl_client=_hl_client_from_mock(_mock_response(body=[meta, ctxs]))
        )
        with pytest.raises(CollectorSourceError, match="Malformed asset context"):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_unexpected_meta_and_asset_ctxs_structure_raises_source_error(self) -> None:
        # Response with only 1 element (not the expected [meta, ctxs] pair) → line 82
        body: list[object] = [_make_meta()]
        collector = OnchainCollector(hl_client=_hl_client_from_mock(_mock_response(body=body)))
        with pytest.raises(CollectorSourceError, match="Unexpected"):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_asyncio_timeout_raises_collector_timeout_error(self) -> None:
        # asyncio.wait_for timeout propagates as TimeoutError → line 123
        collector = OnchainCollector(hl_client=_hl_client_raising(TimeoutError("timed out")))
        with pytest.raises(CollectorTimeoutError):
            await collector.collect()


# ---------------------------------------------------------------------------
# Configuration tests
# ---------------------------------------------------------------------------


class TestOnchainCollectorConfig:
    def test_default_timeout_is_10(self) -> None:
        collector = OnchainCollector(hl_client=_hl_client_from_mock(_mock_response()))
        assert collector.timeout_seconds == 10

    def test_custom_timeout(self) -> None:
        collector = OnchainCollector(
            hl_client=_hl_client_from_mock(_mock_response()), timeout_seconds=30
        )
        assert collector.timeout_seconds == 30

    def test_default_cache_ttl_is_60(self) -> None:
        collector = OnchainCollector(hl_client=_hl_client_from_mock(_mock_response()))
        assert collector.cache_ttl_seconds == 60
