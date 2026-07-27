"""Unit tests for the pure baseline strategy math (RESEARCH §3.3, ADR-0036).

Every case uses hand-computed prices so the arithmetic is verifiable by inspection. Momentum
tests seed clean $1000 books (not the $1000/3 init) so open/close/funding numbers are exact.
Equity is compared quantized to 8dp (as the runner stores it).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from aiat.baselines.compute import (
    SymbolTick,
    TickMarket,
    compute_buy_and_hold,
    compute_cash,
    compute_momentum,
)


def _q(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def _market(
    btc: tuple[str, str, str, str],
    eth: tuple[str, str, str, str] = ("100", "100", "100", "0"),
    sol: tuple[str, str, str, str] = ("100", "100", "100", "0"),
) -> TickMarket:
    """Build a TickMarket; each tuple is (price, ema20, ema50, funding_rate_8h) as strings.

    ETH/SOL default to flat EMAs (ema20==ema50) so they never cross — isolating BTC.
    """

    def _t(v: tuple[str, str, str, str]) -> SymbolTick:
        return SymbolTick(Decimal(v[0]), Decimal(v[1]), Decimal(v[2]), Decimal(v[3]))

    return {"BTC": _t(btc), "ETH": _t(eth), "SOL": _t(sol)}


_FLAT_BOOKS = {s: {"realized": "1000", "position": None} for s in ("BTC", "ETH", "SOL")}


def _prev(btc_ema: tuple[str, str], books: dict[str, Any] | None = None) -> dict[str, Any]:
    """A momentum prev_raw with clean $1000 books and controllable BTC prev EMAs."""
    return {
        "books": books if books is not None else _FLAT_BOOKS,
        "prev_ema": {
            "BTC": {"ema20": btc_ema[0], "ema50": btc_ema[1]},
            "ETH": {"ema20": "100", "ema50": "100"},
            "SOL": {"ema20": "100", "ema50": "100"},
        },
    }


# --------------------------------------------------------------------------- #
# cash                                                                        #
# --------------------------------------------------------------------------- #


class TestCash:
    def test_constant_equity_zero_pnl(self) -> None:
        r = compute_cash()
        assert r.equity_usd == Decimal("1000")
        assert r.pnl_usd_cumulative == Decimal("0")
        assert r.raw_state == {"strategy": "cash"}


# --------------------------------------------------------------------------- #
# buy & hold                                                                  #
# --------------------------------------------------------------------------- #


class TestBuyAndHold:
    def test_first_tick_allocates_equal_weight_equity_1000(self) -> None:
        r = compute_buy_and_hold(None, _market(("100", "0", "0", "0")))
        assert _q(r.equity_usd) == Decimal("1000.00000000")
        assert _q(r.pnl_usd_cumulative) == Decimal("0E-8")
        # $1000/3 into BTC @100 -> 3.3333.. units
        assert Decimal(r.raw_state["units"]["BTC"]) == Decimal("1000") / 3 / Decimal("100")

    def test_second_tick_marks_to_market(self) -> None:
        first = compute_buy_and_hold(None, _market(("100", "0", "0", "0")))
        # BTC +10%, ETH/SOL flat at 100 -> equity = (1000/3)*3.1 = 1033.333...
        r = compute_buy_and_hold(first.raw_state, _market(("110", "0", "0", "0")))
        assert _q(r.equity_usd) == Decimal("1033.33333333")
        assert _q(r.pnl_usd_cumulative) == Decimal("33.33333333")

    def test_units_persist_no_rebalancing(self) -> None:
        first = compute_buy_and_hold(None, _market(("100", "0", "0", "0")))
        second = compute_buy_and_hold(first.raw_state, _market(("110", "0", "0", "0")))
        assert second.raw_state["units"] == first.raw_state["units"]


# --------------------------------------------------------------------------- #
# naive momentum                                                              #
# --------------------------------------------------------------------------- #


class TestMomentumSignals:
    def test_first_tick_no_prev_stays_flat(self) -> None:
        r = compute_momentum(None, _market(("100", "102", "101", "0")))
        assert _q(r.equity_usd) == Decimal("1000.00000000")
        for s in ("BTC", "ETH", "SOL"):
            assert r.raw_state["books"][s]["position"] is None
        # prev_ema recorded for next tick
        assert r.raw_state["prev_ema"]["BTC"]["ema20"] == "102"

    def test_up_cross_opens_long(self) -> None:
        # prev diff -1 (100-101), cur diff +1 (102-101) -> up-cross
        r = compute_momentum(_prev(("100", "101")), _market(("1000", "102", "101", "0")))
        pos = r.raw_state["books"]["BTC"]["position"]
        assert pos["direction"] == "LONG"
        assert Decimal(pos["entry"]) == Decimal("1000")
        assert Decimal(pos["notional"]) == Decimal("600")  # 0.20*1000*3
        assert Decimal(pos["units"]) == Decimal("0.6")
        assert Decimal(pos["sl"]) == Decimal("970")  # -3%
        assert Decimal(pos["tp"]) == Decimal("1060")  # +6%
        # realized = 1000 - open_fee(600*0.00045=0.27); unrealized 0 at entry
        assert Decimal(r.raw_state["books"]["BTC"]["realized"]) == Decimal("999.73")
        assert _q(r.equity_usd) == Decimal("2999.73000000")  # 999.73 + 1000 + 1000

    def test_down_cross_opens_short(self) -> None:
        # prev diff +1, cur diff -1 -> down-cross
        r = compute_momentum(_prev(("101", "100")), _market(("1000", "99", "100", "0")))
        pos = r.raw_state["books"]["BTC"]["position"]
        assert pos["direction"] == "SHORT"
        assert Decimal(pos["sl"]) == Decimal("1030")  # +3% (short SL above)
        assert Decimal(pos["tp"]) == Decimal("940")  # -6% (short TP below)


def _long_book(entry: str = "1000") -> dict[str, Any]:
    """BTC book holding an open LONG (realized 999.73 after the 0.27 open fee), ETH/SOL flat."""
    return {
        "BTC": {
            "realized": "999.73",
            "position": {
                "direction": "LONG",
                "entry": entry,
                "notional": "600",
                "units": "0.6",
                "sl": "970",
                "tp": "1060",
            },
        },
        "ETH": {"realized": "1000", "position": None},
        "SOL": {"realized": "1000", "position": None},
    }


class TestMomentumExits:
    def test_take_profit_fills_at_level(self) -> None:
        # LONG open; no inverse cross (ema stays 102/101 aligned); close 1070 >= tp 1060
        r = compute_momentum(
            _prev(("102", "101"), _long_book()), _market(("1070", "103", "101", "0"))
        )
        assert r.raw_state["books"]["BTC"]["position"] is None
        # gross = 600*(1060/1000-1)=36; close_fee=0.6*1060*0.00045=0.2862
        # realized = 999.73 + 36 - 0.2862 = 1035.4438
        assert Decimal(r.raw_state["books"]["BTC"]["realized"]) == Decimal("1035.4438")
        assert _q(r.equity_usd) == Decimal("3035.44380000")

    def test_stop_loss_fills_at_level(self) -> None:
        # close 960 <= sl 970 -> fill at 970
        r = compute_momentum(
            _prev(("102", "101"), _long_book()), _market(("960", "103", "101", "0"))
        )
        assert r.raw_state["books"]["BTC"]["position"] is None
        # gross = 600*(970/1000-1) = -18; close_fee = 0.6*970*0.00045 = 0.2619
        # realized = 999.73 - 18 - 0.2619 = 981.4681
        assert Decimal(r.raw_state["books"]["BTC"]["realized"]) == Decimal("981.4681")

    def test_inverse_cross_closes_and_flips_to_short(self) -> None:
        # LONG open, down-cross (prev +1, cur -2) -> close at CLOSE price then open SHORT
        r = compute_momentum(
            _prev(("102", "101"), _long_book()), _market(("1000", "99", "101", "0"))
        )
        pos = r.raw_state["books"]["BTC"]["position"]
        assert pos is not None and pos["direction"] == "SHORT"
        # close LONG at 1000: gross 0, fee 0.6*1000*0.00045=0.27 -> realized 999.46
        # open SHORT: margin 0.20*999.46=199.892, notional 599.676, fee 599.676*0.00045=0.2698542
        # realized = 999.46 - 0.2698542 = 999.1901458
        assert Decimal(r.raw_state["books"]["BTC"]["realized"]) == Decimal("999.1901458")
        assert Decimal(pos["entry"]) == Decimal("1000")
        assert Decimal(pos["notional"]) == Decimal("599.676")

    def test_inverse_cross_takes_precedence_over_sltp(self) -> None:
        # close 1070 would be a TP, but a down-cross fires first -> exit at close 1070, then flip
        r = compute_momentum(
            _prev(("102", "101"), _long_book()), _market(("1070", "99", "101", "0"))
        )
        pos = r.raw_state["books"]["BTC"]["position"]
        assert pos is not None and pos["direction"] == "SHORT"  # flipped, not TP-closed
        # LONG closed at 1070 (close price, not tp level): gross 600*0.07=42
        # close_fee 0.6*1070*0.00045=0.2889 -> realized 999.73+42-0.2889 = 1041.4411
        # then SHORT opens: margin 0.2*1041.4411=208.288220, notional 624.86466,
        #   fee 624.86466*0.00045=0.281189097 -> realized 1041.4411-0.281189097=1041.159910903
        assert Decimal(r.raw_state["books"]["BTC"]["realized"]) == Decimal("1041.159910903")


class TestMomentumFundingAndHold:
    def test_position_held_accrues_funding(self) -> None:
        # LONG open, no cross, no SL/TP; funding_rate_8h=0.01 -> per-tick 600*0.01/32=0.1875
        r = compute_momentum(
            _prev(("102", "101"), _long_book()), _market(("1000", "103", "101", "0.01"))
        )
        pos = r.raw_state["books"]["BTC"]["position"]
        assert pos is not None and pos["direction"] == "LONG"  # still open (no overlap re-open)
        # realized = 999.73 - 0.1875 (funding, LONG pays positive rate)
        assert Decimal(r.raw_state["books"]["BTC"]["realized"]) == Decimal("999.5425")
        # unrealized 0 at entry price -> book equity = realized
        assert _q(r.equity_usd) == Decimal("2999.54250000")

    def test_short_receives_positive_funding(self) -> None:
        short_book = {
            "BTC": {
                "realized": "1000",
                "position": {
                    "direction": "SHORT",
                    "entry": "1000",
                    "notional": "600",
                    "units": "0.6",
                    "sl": "1030",
                    "tp": "940",
                },
            },
            "ETH": {"realized": "1000", "position": None},
            "SOL": {"realized": "1000", "position": None},
        }
        # SHORT, no cross (ema 99/101 both diffs negative), funding 0.01 -> SHORT receives 0.1875
        r = compute_momentum(
            _prev(("99", "101"), short_book), _market(("1000", "98", "101", "0.01"))
        )
        assert Decimal(r.raw_state["books"]["BTC"]["realized"]) == Decimal("1000.1875")


def _short_book() -> dict[str, Any]:
    """BTC book holding an open SHORT (entry 1000, sl 1030, tp 940), ETH/SOL flat."""
    return {
        "BTC": {
            "realized": "999.73",
            "position": {
                "direction": "SHORT",
                "entry": "1000",
                "notional": "600",
                "units": "0.6",
                "sl": "1030",
                "tp": "940",
            },
        },
        "ETH": {"realized": "1000", "position": None},
        "SOL": {"realized": "1000", "position": None},
    }


class TestMomentumShortExits:
    def test_short_take_profit_fills_at_level(self) -> None:
        # SHORT open; no up-cross (ema stays below 98/100); close 930 <= tp 940 -> fill at 940
        r = compute_momentum(
            _prev(("99", "100"), _short_book()), _market(("930", "98", "100", "0"))
        )
        assert r.raw_state["books"]["BTC"]["position"] is None
        # gross = 600*(940/1000-1)*(-1) = +36; close_fee = 0.6*940*0.00045 = 0.2538
        assert Decimal(r.raw_state["books"]["BTC"]["realized"]) == Decimal("1035.4762")

    def test_short_stop_loss_fills_at_level(self) -> None:
        # price 1040 >= sl 1030 (SHORT SL is ABOVE entry); ema below -> no up-cross
        r = compute_momentum(
            _prev(("99", "100"), _short_book()), _market(("1040", "98", "100", "0"))
        )
        assert r.raw_state["books"]["BTC"]["position"] is None
        # gross = 600*(1030/1000-1)*(-1) = -18; close_fee = 0.6*1030*0.00045 = 0.2781
        assert Decimal(r.raw_state["books"]["BTC"]["realized"]) == Decimal("981.4519")


class TestMomentumCrossoverIsATrueCross:
    """Pins that entry needs a real CROSS, not a naive state rule (ema20>ema50)."""

    def test_already_above_no_cross_stays_flat(self) -> None:
        # prev ABOVE (102/101) and still ABOVE (103/101): no up-cross -> a state rule would
        # (wrongly) open LONG; a true cross must NOT.
        r = compute_momentum(_prev(("102", "101")), _market(("1000", "103", "101", "0")))
        assert r.raw_state["books"]["BTC"]["position"] is None

    def test_already_below_no_cross_stays_flat(self) -> None:
        r = compute_momentum(_prev(("99", "101")), _market(("1000", "98", "101", "0")))
        assert r.raw_state["books"]["BTC"]["position"] is None


class TestMomentumNoOverlap:
    def test_open_position_not_reopened_without_cross(self) -> None:
        # LONG open, price moves to 1010, EMAs stay above (no cross), funding 0:
        # the position must be HELD unchanged (same entry/notional), never re-sized/re-opened.
        r = compute_momentum(
            _prev(("102", "101"), _long_book()), _market(("1010", "103", "101", "0"))
        )
        pos = r.raw_state["books"]["BTC"]["position"]
        assert pos is not None and pos["direction"] == "LONG"
        assert Decimal(pos["entry"]) == Decimal("1000")  # NOT re-opened at 1010
        assert Decimal(pos["notional"]) == Decimal("600")  # NOT re-sized
        assert Decimal(r.raw_state["books"]["BTC"]["realized"]) == Decimal("999.73")  # funding 0


class TestMomentumIndependentBooks:
    def test_three_symbols_trade_independently(self) -> None:
        # BTC up-cross -> LONG; ETH down-cross -> SHORT; SOL flat. Each book is independent.
        prev = {
            "books": {s: {"realized": "1000", "position": None} for s in ("BTC", "ETH", "SOL")},
            "prev_ema": {
                "BTC": {"ema20": "100", "ema50": "101"},  # below -> up-cross next
                "ETH": {"ema20": "101", "ema50": "100"},  # above -> down-cross next
                "SOL": {"ema20": "100", "ema50": "100"},
            },
        }
        market = _market(
            ("1000", "102", "101", "0"),  # BTC up-cross
            ("2000", "99", "100", "0"),  # ETH down-cross
            ("100", "100", "100", "0"),  # SOL flat
        )
        r = compute_momentum(prev, market)
        btc = r.raw_state["books"]["BTC"]["position"]
        eth = r.raw_state["books"]["ETH"]["position"]
        assert btc["direction"] == "LONG" and Decimal(btc["entry"]) == Decimal("1000")
        assert Decimal(btc["notional"]) == Decimal("600")
        assert eth["direction"] == "SHORT" and Decimal(eth["entry"]) == Decimal("2000")
        assert Decimal(eth["notional"]) == Decimal("600")  # 0.20*1000*3, ETH's own book
        assert r.raw_state["books"]["SOL"]["position"] is None


class TestMomentumStateRoundTrip:
    def test_raw_state_is_json_serialisable_and_reconsumable(self) -> None:
        import json

        r1 = compute_momentum(_prev(("100", "101")), _market(("1000", "102", "101", "0")))
        # survives a JSON round-trip (as it does through JSONB) and feeds the next tick
        reloaded = json.loads(json.dumps(r1.raw_state))
        r2 = compute_momentum(reloaded, _market(("1000", "103", "101", "0")))
        # BTC still LONG, held (no new cross), no funding -> realized unchanged
        assert r2.raw_state["books"]["BTC"]["position"]["direction"] == "LONG"
        assert Decimal(r2.raw_state["books"]["BTC"]["realized"]) == Decimal("999.73")
