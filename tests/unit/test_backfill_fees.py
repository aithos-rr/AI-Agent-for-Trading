"""Unit tests for the backfill_fees pure helpers (finding A repair utility).

The DB/HL orchestration in scripts/backfill_fees.py is integration-only (needs real HL fill
history) and is dry-run-by-default; these cover the pure, deterministic parts: building the
oid→Σfee map from HL fills and the fee_type mapping.
"""

from __future__ import annotations

from decimal import Decimal

from scripts.backfill_fees import build_oid_fee_map, fee_type_for


class TestBuildOidFeeMap:
    def test_sums_partial_fills_by_oid(self) -> None:
        fills = [
            {"oid": 111, "fee": "0.90"},
            {"oid": 111, "fee": "0.60"},  # same order, second partial
            {"oid": 222, "fee": "0.10"},
        ]
        out = build_oid_fee_map(fills)
        assert out == {"111": Decimal("1.50"), "222": Decimal("0.10")}

    def test_oid_coerced_to_str(self) -> None:
        # HL returns oid as int; the map is str-keyed to match OrderResult.hl_order_id (str).
        assert set(build_oid_fee_map([{"oid": 111, "fee": "1.0"}])) == {"111"}

    def test_skips_malformed_records(self) -> None:
        fills = [
            "not-a-dict",
            {"oid": None, "fee": "1.0"},
            {"oid": 5, "fee": None},
            {"oid": 6, "fee": "garbage"},
            {"oid": 7, "fee": "2.5"},
        ]
        assert build_oid_fee_map(fills) == {"7": Decimal("2.5")}

    def test_empty(self) -> None:
        assert build_oid_fee_map([]) == {}


class TestFeeTypeFor:
    def test_entry_is_taker_open(self) -> None:
        assert fee_type_for("entry") == "taker_open"

    def test_close_and_triggers_are_taker_close(self) -> None:
        assert fee_type_for("close") == "taker_close"
        assert fee_type_for("stop_loss") == "taker_close"
        assert fee_type_for("take_profit") == "taker_close"
