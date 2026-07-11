"""Unit tests for detect_chain_divergences (ADR-0025) — pure, DB-free.

Positions are duck-typed; SimpleNamespace stands in for the DB Position row (needs ``id``) and
the chain OpenPositionSummary (no id). The comparison nets DB rows PER COIN (HL nets per coin),
so multiple DB rows for one symbol are summed, never compared row-by-row.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from aiat.orchestration.chain_reconciliation import detect_chain_divergences


def _db(pid: str, symbol: str, side: str, size: str) -> SimpleNamespace:
    return SimpleNamespace(id=pid, symbol=symbol, side=side, size_units=Decimal(size))


def _chain(symbol: str, side: str, size: str) -> SimpleNamespace:
    return SimpleNamespace(symbol=symbol, side=side, size_units=Decimal(size))


class TestDetectChainDivergences:
    def test_in_sync_single_row(self) -> None:
        db = [_db("p1", "BTC", "LONG", "1.0")]
        chain = [_chain("BTC", "LONG", "1.0")]
        assert detect_chain_divergences(db, chain) == []

    def test_zombie_chain_flat(self) -> None:
        db = [_db("p1", "BTC", "LONG", "1.0")]
        (d,) = detect_chain_divergences(db, [])
        assert d.kind == "zombie_row"
        assert d.chain_side is None
        assert d.delta == Decimal("1.0")  # whole DB size should be 0
        assert [r.position_id for r in d.db_positions] == ["p1"]

    def test_real_cn_premium_zombie_two_db_rows_one_chain(self) -> None:
        """Empirical case (cn-premium, 2026-07-11): chain nets one BTC LONG 0.00425, but the DB
        holds TWO open BTC LONG rows (a 63403 zombie + the 62690.2 real one). A row-by-row check
        would miss it; the netted sum (0.01425) vs chain (0.00425) flags a zombie_row with
        delta = 0.01 and BOTH position_ids for manual repair."""
        db = [
            _db("zombie-63403", "BTC", "LONG", "0.01000"),  # closed on-chain, never closed in DB
            _db("real-62690", "BTC", "LONG", "0.00425"),
        ]
        chain = [_chain("BTC", "LONG", "0.00425")]
        (d,) = detect_chain_divergences(db, chain)
        assert d.kind == "zombie_row"
        assert d.chain_size == Decimal("0.00425")
        assert d.delta == Decimal("0.01000")  # 0.01425 (DB sum) − 0.00425 (chain)
        assert {r.position_id for r in d.db_positions} == {"zombie-63403", "real-62690"}

    def test_missing_row_chain_only(self) -> None:
        (d,) = detect_chain_divergences([], [_chain("SOL", "LONG", "30")])
        assert d.kind == "missing_row"
        assert d.chain_side == "LONG"
        assert d.chain_size == Decimal("30")
        assert d.db_positions == ()
        assert d.delta == Decimal("-30")

    def test_size_mismatch_chain_larger_than_db_sum(self) -> None:
        db = [_db("p1", "BTC", "LONG", "1.0")]
        chain = [_chain("BTC", "LONG", "2.0")]
        (d,) = detect_chain_divergences(db, chain)
        assert d.kind == "size_mismatch"  # chain bigger than DB → not a zombie over-count
        assert d.delta == Decimal("-1.0")

    def test_side_flip_is_size_mismatch(self) -> None:
        db = [_db("p1", "BTC", "LONG", "1.0")]
        chain = [_chain("BTC", "SHORT", "1.0")]
        (d,) = detect_chain_divergences(db, chain)
        assert d.kind == "size_mismatch"
        assert d.chain_side == "SHORT"
        assert d.db_positions[0].side == "LONG"

    def test_summed_within_tolerance_not_divergence(self) -> None:
        # DB rows sum to exactly the chain size ⇒ in sync even though split across two rows...
        # but two OPEN rows summing to chain is itself only "in sync" by size; here we assert the
        # tolerance path with a single row differing by 1e-6.
        db = [_db("p1", "BTC", "LONG", "1.000000")]
        chain = [_chain("BTC", "LONG", "1.000001")]
        assert detect_chain_divergences(db, chain) == []

    def test_deterministic_order_by_symbol(self) -> None:
        db = [_db("p1", "SOL", "LONG", "1"), _db("p2", "BTC", "LONG", "1")]
        result = detect_chain_divergences(db, [])
        assert [d.symbol for d in result] == ["BTC", "SOL"]

    def test_to_dict_is_json_safe_with_repair_data(self) -> None:
        db = [_db("p1", "BTC", "LONG", "0.01"), _db("p2", "BTC", "LONG", "0.00425")]
        (d,) = detect_chain_divergences(db, [_chain("BTC", "LONG", "0.00425")])
        dumped = d.to_dict()
        assert dumped["symbol"] == "BTC"
        assert dumped["kind"] == "zombie_row"
        assert dumped["chain_size"] == "0.00425"
        assert dumped["delta"] == "0.01000"  # Decimal keeps the operands' scale
        assert dumped["db_positions"] == [
            {"position_id": "p1", "side": "LONG", "size_units": "0.01"},
            {"position_id": "p2", "side": "LONG", "size_units": "0.00425"},
        ]
