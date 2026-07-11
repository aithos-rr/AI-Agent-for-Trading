"""Unit tests for the funding-record parser (finding B / ADR-0031).

Pure, DB-free tests of ``_parse_funding_record`` against the REAL Hyperliquid
``userFunding`` record shape. The end-to-end reconcile pass (real Postgres) lives in
tests/e2e/test_funding_reconciler.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from aiat.orchestration.funding_reconciler import _parse_funding_record


def _rec(**delta_overrides: object) -> dict:
    delta: dict[str, object] = {
        "type": "funding",
        "coin": "BTC",
        "usdc": "-0.310121",
        "szi": "1.0",
        "fundingRate": "0.0000125",
    }
    delta.update(delta_overrides)
    return {"time": 1_683_849_600_076, "hash": "0xabc", "delta": delta}


class TestParseFundingRecord:
    def test_parses_real_shape(self) -> None:
        parsed = _parse_funding_record(_rec())
        assert parsed is not None
        assert parsed.coin == "BTC"
        # money via Decimal(str(...)) — inv #12, exact textual value preserved
        assert parsed.usdc == Decimal("-0.310121")
        assert parsed.rate == Decimal("0.0000125")
        assert isinstance(parsed.usdc, Decimal)
        assert parsed.period_end == datetime.fromtimestamp(1_683_849_600.076, tz=UTC)

    def test_skips_non_funding_delta(self) -> None:
        assert _parse_funding_record(_rec(type="liquidation")) is None

    def test_skips_unsupported_coin(self) -> None:
        assert _parse_funding_record(_rec(coin="DOGE")) is None

    def test_skips_missing_usdc(self) -> None:
        rec = _rec()
        del rec["delta"]["usdc"]  # type: ignore[attr-defined]
        assert _parse_funding_record(rec) is None

    def test_skips_unparseable_numeric(self) -> None:
        assert _parse_funding_record(_rec(usdc="not-a-number")) is None

    def test_skips_malformed_top_level(self) -> None:
        assert _parse_funding_record({"delta": {}, "time": "not-int"}) is None
        assert _parse_funding_record({"time": 1}) is None  # no delta
