"""Pure per-tick computation for the 3 pre-registered non-LLM baselines (RESEARCH §3.3).

No I/O. Each strategy is a pure function ``(prev_raw_state, tick_market) -> BaselineResult``
so it is exhaustively unit-testable with hand-computed prices, and the same code drives both
the live orchestrator step and the ``scripts/compute_baselines.py`` catch-up (ADR-0036).

State is carried between ticks as the JSON-serialisable ``BaselineResult.raw_state`` — persisted
verbatim into ``baseline_equity_snapshots.raw_state`` and fed back as ``prev_raw`` on the next
tick. All money is ``Decimal`` (inv #12); the raw_state stores full-precision Decimal strings
(no quantization) to prevent drift as state compounds. Only the stored ``equity_usd`` /
``pnl_usd_cumulative`` columns are quantized (by the runner), never the carried state.

Definitions (pre-registered, binding — RESEARCH §3.3):
  * **cash**: equity constant $1000, PnL 0, cost 0.
  * **buy_and_hold**: $1000/3 into each of BTC/ETH/SOL at the first tick, fractional units held,
    no rebalancing, no fee; marked-to-market on each tick's price.
  * **naive_momentum_ema_20_50**: BTC/ETH/SOL as 3 independent $1000/3 books. EMA(20)×EMA(50)
    cross on 15m candles (both already in the snapshot). LONG on up-cross, SHORT on down-cross;
    size = 20% of the book's equity as margin, leverage 3× (notional = 0.6×equity); SL 3% / TP 6%;
    early exit on inverse cross (takes precedence over SL/TP — RESEARCH "anche prima di SL/TP");
    one position per symbol (no overlap). Taker fee on open+close and funding (from
    ``funding_rate_8h``) are deducted from equity (§3.3 cost parity); tax-sim is a separate
    analysis layer (ADR-0033), never in the equity curve.

    SL/TP are evaluated on the tick CLOSE only (the snapshot carries no intra-tick high/low and
    the live path must not call HL): a breach is detected when the close is beyond the level and
    the fill is booked AT the level. Intra-candle wicks that revert by the close are therefore
    not captured — a conservative approximation, and an asymmetry vs the LLM models whose on-chain
    SL/TP fire intra-tick. Documented in ADR-0036 (§3.3 high/low extension = future enhancement).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# Baseline names (must match the CHECK vocabulary in baseline_configs / baseline_equity_snapshots).
CASH = "cash"
BUY_AND_HOLD = "buy_and_hold"
MOMENTUM = "naive_momentum_ema_20_50"
BASELINE_NAMES: tuple[str, ...] = (CASH, BUY_AND_HOLD, MOMENTUM)

SYMBOLS: tuple[str, ...] = ("BTC", "ETH", "SOL")

_ZERO = Decimal("0")
INITIAL_CAPITAL = Decimal("1000")
PER_SYMBOL_ALLOC = INITIAL_CAPITAL / Decimal(len(SYMBOLS))  # $1000/3, full precision

# naive_momentum_ema_20_50 pre-registered params (RESEARCH §3.3; mirror seed_experiment.py).
SIZE_PCT = Decimal("0.20")  # margin = 20% of the book's equity (aligned to the LLM guardrail)
LEVERAGE = Decimal("3")
STOP_LOSS_PCT = Decimal("0.03")
TAKE_PROFIT_PCT = Decimal("0.06")
# 15-minute ticks over the 8-hour funding period → 32 ticks; funding_rate_8h accrues pro-rata.
FUNDING_TICKS_PER_8H = Decimal("32")
# HL perp taker fee (4.5 bps), validated empirically on the experiment's own fee_events /
# userFills: fee_usd / notional over 518 taker_open rows ≈ 0.000450 (ADR-0036). The one modeling
# constant not derivable from the snapshot; applied on open + close for the leveraged momentum
# baseline only (buy&hold/cash are fee-free per §3.3).
TAKER_FEE_RATE = Decimal("0.00045")


@dataclass(frozen=True)
class SymbolTick:
    """The per-symbol market inputs a baseline needs at one tick (from the context snapshot)."""

    price: Decimal  # last-close spot/mark price (TechnicalIndicators.price_usd)
    ema20: Decimal
    ema50: Decimal
    funding_rate_8h: Decimal


@dataclass(frozen=True)
class BaselineResult:
    """One baseline's outcome at a tick: stored equity/PnL + the state to carry forward."""

    equity_usd: Decimal  # full precision; the runner quantizes for storage
    pnl_usd_cumulative: Decimal
    raw_state: dict[str, Any]  # JSON-serialisable; becomes next tick's prev_raw


TickMarket = dict[str, SymbolTick]


def _require_symbols(market: TickMarket) -> None:
    missing = [s for s in SYMBOLS if s not in market]
    if missing:
        raise ValueError(f"tick market missing symbols: {missing}")


# --------------------------------------------------------------------------- #
# cash                                                                        #
# --------------------------------------------------------------------------- #


def compute_cash() -> BaselineResult:
    """Cash / no-trade: constant $1000, zero PnL (RESEARCH §3.3)."""
    return BaselineResult(INITIAL_CAPITAL, _ZERO, {"strategy": CASH})


# --------------------------------------------------------------------------- #
# buy & hold                                                                  #
# --------------------------------------------------------------------------- #


def compute_buy_and_hold(prev_raw: dict[str, Any] | None, market: TickMarket) -> BaselineResult:
    """Equal-weight BTC/ETH/SOL, bought once at the first tick, marked-to-market thereafter.

    Args:
        prev_raw: previous tick's raw_state (holds fractional ``units`` per symbol), or None on
            the first tick (allocate $1000/3 per symbol at the current price, no fee).
        market: this tick's per-symbol data (only ``price`` is used).

    Returns:
        BaselineResult with equity = Σ units·price and pnl = equity − $1000.
    """
    _require_symbols(market)
    if prev_raw is None or "units" not in prev_raw:
        units = {s: PER_SYMBOL_ALLOC / market[s].price for s in SYMBOLS}
    else:
        units = {s: Decimal(prev_raw["units"][s]) for s in SYMBOLS}

    equity = sum((units[s] * market[s].price for s in SYMBOLS), _ZERO)
    raw_state = {"units": {s: str(units[s]) for s in SYMBOLS}}
    return BaselineResult(equity, equity - INITIAL_CAPITAL, raw_state)


# --------------------------------------------------------------------------- #
# naive momentum (EMA 20/50 cross)                                            #
# --------------------------------------------------------------------------- #


@dataclass
class _Position:
    direction: str  # "LONG" | "SHORT"
    entry: Decimal
    notional: Decimal
    units: Decimal
    sl: Decimal
    tp: Decimal

    def sign(self) -> Decimal:
        return Decimal(1) if self.direction == "LONG" else Decimal(-1)

    def unrealized(self, price: Decimal) -> Decimal:
        return self.notional * (price / self.entry - 1) * self.sign()

    def to_dict(self) -> dict[str, str]:
        return {
            "direction": self.direction,
            "entry": str(self.entry),
            "notional": str(self.notional),
            "units": str(self.units),
            "sl": str(self.sl),
            "tp": str(self.tp),
        }

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> _Position:
        return cls(
            direction=d["direction"],
            entry=Decimal(d["entry"]),
            notional=Decimal(d["notional"]),
            units=Decimal(d["units"]),
            sl=Decimal(d["sl"]),
            tp=Decimal(d["tp"]),
        )


@dataclass
class _Book:
    realized: Decimal
    position: _Position | None

    def equity(self, price: Decimal) -> Decimal:
        return self.realized + (self.position.unrealized(price) if self.position else _ZERO)


def _open(book: _Book, direction: str, price: Decimal) -> None:
    """Open a leveraged position sized at 20% margin × 3× (RESEARCH §3.3), booking the open fee."""
    margin = SIZE_PCT * book.realized
    notional = margin * LEVERAGE
    units = notional / price
    book.realized -= notional * TAKER_FEE_RATE  # taker open fee
    if direction == "LONG":
        sl = price * (1 - STOP_LOSS_PCT)
        tp = price * (1 + TAKE_PROFIT_PCT)
    else:
        sl = price * (1 + STOP_LOSS_PCT)
        tp = price * (1 - TAKE_PROFIT_PCT)
    book.position = _Position(direction, price, notional, units, sl, tp)


def _close(book: _Book, exit_price: Decimal) -> None:
    """Realise the open position's PnL at ``exit_price`` and book the taker close fee."""
    pos = book.position
    assert pos is not None
    gross = pos.notional * (exit_price / pos.entry - 1) * pos.sign()
    close_fee = pos.units * exit_price * TAKER_FEE_RATE
    book.realized += gross - close_fee
    book.position = None


def _accrue_funding(book: _Book, funding_rate_8h: Decimal) -> None:
    """Pro-rata funding for one 15m tick; LONG pays when the rate is positive (§3.2.6 sign)."""
    pos = book.position
    assert pos is not None
    book.realized -= pos.notional * funding_rate_8h / FUNDING_TICKS_PER_8H * pos.sign()


def compute_momentum(prev_raw: dict[str, Any] | None, market: TickMarket) -> BaselineResult:
    """Naive EMA(20)×EMA(50) cross baseline over 3 independent symbol books (RESEARCH §3.3).

    Per symbol, per tick: detect a cross vs the previous tick's EMAs; if a position is open,
    an inverse cross closes it first (then flips), else SL/TP is checked on the close (fill at
    level), else funding accrues; if flat, a cross opens LONG (up) / SHORT (down). One position
    per symbol. Equity = Σ (book.realized + open-position unrealized). See module docstring.

    Args:
        prev_raw: previous tick's raw_state (books + prev_ema), or None to initialise 3 flat
            $1000/3 books.
        market: this tick's per-symbol price / ema20 / ema50 / funding_rate_8h.

    Returns:
        BaselineResult with equity, pnl = equity − $1000, and the carried books + prev_ema.
    """
    _require_symbols(market)
    prev_books = (prev_raw or {}).get("books", {})
    prev_ema = (prev_raw or {}).get("prev_ema", {})

    books: dict[str, _Book] = {}
    for s in SYMBOLS:
        if s in prev_books:
            pos_d = prev_books[s].get("position")
            books[s] = _Book(
                realized=Decimal(prev_books[s]["realized"]),
                position=_Position.from_dict(pos_d) if pos_d else None,
            )
        else:
            books[s] = _Book(realized=PER_SYMBOL_ALLOC, position=None)

    for s in SYMBOLS:
        book = books[s]
        tick = market[s]
        up_cross, down_cross = _cross_signals(prev_ema.get(s), tick.ema20, tick.ema50)

        if book.position is not None:
            pos = book.position
            inverse = (pos.direction == "LONG" and down_cross) or (
                pos.direction == "SHORT" and up_cross
            )
            if inverse:
                _close(book, tick.price)  # signal exit fills at the close price
            else:
                level = _sltp_level(pos, tick.price)
                if level is not None:
                    _close(book, level)  # detected on close, filled at the SL/TP level
                else:
                    _accrue_funding(book, tick.funding_rate_8h)

        if book.position is None:
            want = "LONG" if up_cross else ("SHORT" if down_cross else None)
            if want is not None:
                _open(book, want, tick.price)

    equity = sum((books[s].equity(market[s].price) for s in SYMBOLS), _ZERO)
    books_state: dict[str, Any] = {}
    for s in SYMBOLS:
        bpos = books[s].position
        books_state[s] = {
            "realized": str(books[s].realized),
            "position": bpos.to_dict() if bpos is not None else None,
        }
    raw_state: dict[str, Any] = {
        "books": books_state,
        "prev_ema": {
            s: {"ema20": str(market[s].ema20), "ema50": str(market[s].ema50)} for s in SYMBOLS
        },
    }
    return BaselineResult(equity, equity - INITIAL_CAPITAL, raw_state)


def _cross_signals(
    prev_ema: dict[str, str] | None, ema20: Decimal, ema50: Decimal
) -> tuple[bool, bool]:
    """Return (up_cross, down_cross) vs the previous tick's EMAs. No prev ⇒ (False, False)."""
    if prev_ema is None:
        return (False, False)
    prev_diff = Decimal(prev_ema["ema20"]) - Decimal(prev_ema["ema50"])
    cur_diff = ema20 - ema50
    up_cross = prev_diff <= _ZERO and cur_diff > _ZERO
    down_cross = prev_diff >= _ZERO and cur_diff < _ZERO
    return (up_cross, down_cross)


def _sltp_level(pos: _Position, price: Decimal) -> Decimal | None:
    """SL/TP fill level if the close breached it, else None (TP checked before SL; disjoint)."""
    if pos.direction == "LONG":
        if price >= pos.tp:
            return pos.tp
        if price <= pos.sl:
            return pos.sl
    else:  # SHORT: tp below entry, sl above
        if price <= pos.tp:
            return pos.tp
        if price >= pos.sl:
            return pos.sl
    return None


def compute_baseline(
    name: str, prev_raw: dict[str, Any] | None, market: TickMarket
) -> BaselineResult:
    """Dispatch to the strategy for ``name`` (one of BASELINE_NAMES)."""
    if name == CASH:
        return compute_cash()
    if name == BUY_AND_HOLD:
        return compute_buy_and_hold(prev_raw, market)
    if name == MOMENTUM:
        return compute_momentum(prev_raw, market)
    raise ValueError(f"unknown baseline {name!r}")
