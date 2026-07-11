"""Unit tests for detect_chain_divergences (ADR-0025) — pure, DB-free.

Positions are duck-typed (symbol/side/size_units), so SimpleNamespace stands in for both the
DB Position ORM row and the chain OpenPositionSummary.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from aiat.orchestration.chain_reconciliation import detect_chain_divergences


def _pos(symbol: str, side: str, size: str) -> SimpleNamespace:
    return SimpleNamespace(symbol=symbol, side=side, size_units=Decimal(size))


class TestDetectChainDivergences:
    def test_in_sync_no_divergence(self) -> None:
        db = [_pos("BTC", "LONG", "1.0"), _pos("ETH", "SHORT", "2.0")]
        chain = [_pos("BTC", "LONG", "1.0"), _pos("ETH", "SHORT", "2.0")]
        assert detect_chain_divergences(db, chain) == []

    def test_missing_on_chain(self) -> None:
        db = [_pos("BTC", "LONG", "1.0")]
        (d,) = detect_chain_divergences(db, [])
        assert d.kind == "missing_on_chain"
        assert d.symbol == "BTC"
        assert d.db_side == "LONG"
        assert d.chain_side is None

    def test_missing_in_db(self) -> None:
        chain = [_pos("SOL", "LONG", "30")]
        (d,) = detect_chain_divergences([], chain)
        assert d.kind == "missing_in_db"
        assert d.chain_side == "LONG"
        assert d.db_side is None

    def test_side_mismatch(self) -> None:
        db = [_pos("BTC", "LONG", "1.0")]
        chain = [_pos("BTC", "SHORT", "1.0")]
        (d,) = detect_chain_divergences(db, chain)
        assert d.kind == "side_mismatch"
        assert d.db_side == "LONG"
        assert d.chain_side == "SHORT"

    def test_size_mismatch(self) -> None:
        db = [_pos("BTC", "LONG", "1.0")]
        chain = [_pos("BTC", "LONG", "2.0")]
        (d,) = detect_chain_divergences(db, chain)
        assert d.kind == "size_mismatch"
        assert d.db_size == Decimal("1.0")
        assert d.chain_size == Decimal("2.0")

    def test_size_within_tolerance_is_not_divergence(self) -> None:
        # diff 1e-6 vs tol = 1e-6 + 0.005*1 ⇒ well within band, no false positive
        db = [_pos("BTC", "LONG", "1.000000")]
        chain = [_pos("BTC", "LONG", "1.000001")]
        assert detect_chain_divergences(db, chain) == []

    def test_deterministic_order_by_symbol(self) -> None:
        db = [_pos("SOL", "LONG", "1"), _pos("BTC", "LONG", "1")]
        chain: list[SimpleNamespace] = []
        result = detect_chain_divergences(db, chain)
        assert [d.symbol for d in result] == ["BTC", "SOL"]

    def test_to_dict_is_json_safe(self) -> None:
        db = [_pos("BTC", "LONG", "1.5")]
        (d,) = detect_chain_divergences(db, [])
        assert d.to_dict() == {
            "symbol": "BTC",
            "kind": "missing_on_chain",
            "db_side": "LONG",
            "chain_side": None,
            "db_size": "1.5",
            "chain_size": None,
        }
