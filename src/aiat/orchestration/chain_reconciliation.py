"""DB↔chain position reconciliation — detection only (ADR-0025, findings from smoke M6).

At the start of each agent tick the loop compares the positions the DB believes are OPEN
against what the chain (``clearinghouseState`` via ``fetch_portfolio_state``) actually holds.
A flip (close→open) is two non-atomic market orders, and an SL/TP/liquidation can close a
position on-chain before the loop's ``_check_pending_closures`` records it — so DB and chain
can diverge. This module is the pure detector; the loop turns any divergence into a
``ChainDivergence`` errors row + a warning log and then **proceeds** (M6.2 = detect + alert,
NO auto-repair — see ADR-0025 for why auto-repair is deferred).

CRITICAL (empirical, cn-premium, 2026-07-11): **Hyperliquid nets per coin** — at most ONE
on-chain position per symbol — while the DB can hold MULTIPLE open rows per symbol (a row
closed on-chain but never closed in the DB = a *zombie*). So the comparison MUST aggregate the
DB rows per symbol (signed sum) and compare that to the single chain position — NEVER row by
row (a naive by-symbol dict would silently drop the zombie). Divergences carry the DB
``position_id`` + delta so the zombie can be repaired manually.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

# Size comparison tolerance: DB stores the executed (quantized) size and the chain reports
# ``szi``; a small abs+rel band absorbs float→Decimal string artifacts and per-asset szDecimals
# rounding without raising false divergences.
_SIZE_ABS_TOL = Decimal("0.000001")
_SIZE_REL_TOL = Decimal("0.005")  # 0.5%

# zombie_row  : DB holds open size the chain does not (chain flat, or chain size < DB sum on the
#               same side) — one or more DB rows are stale (closed on-chain, never closed in DB).
# missing_row : the chain holds a position for a symbol the DB has NO open row for.
# size_mismatch: both present but the summed sizes disagree beyond tolerance in a way that is not
#               a clean DB-over-count (e.g. chain larger than the DB sum, or a side flip).
DivergenceKind = Literal["zombie_row", "missing_row", "size_mismatch"]


class _DbPositionLike(Protocol):
    """A DB open-position row (carries the id needed for manual repair)."""

    @property
    def id(self) -> object: ...
    @property
    def symbol(self) -> str: ...
    @property
    def side(self) -> str: ...
    @property
    def size_units(self) -> Decimal: ...


class _ChainPositionLike(Protocol):
    """A chain position summary (no stable id on Hyperliquid)."""

    @property
    def symbol(self) -> str: ...
    @property
    def side(self) -> str: ...
    @property
    def size_units(self) -> Decimal: ...


@dataclass(frozen=True)
class DbPositionRef:
    """A DB open row implicated in a divergence — the handle for manual repair."""

    position_id: str
    side: str
    size_units: Decimal

    def to_dict(self) -> dict[str, str]:
        return {
            "position_id": self.position_id,
            "side": self.side,
            "size_units": str(self.size_units),
        }


@dataclass(frozen=True)
class ChainDivergence:
    """One DB↔chain mismatch for a symbol, aggregated per coin (JSON-safe via :meth:`to_dict`)."""

    symbol: str
    kind: DivergenceKind
    chain_side: str | None
    chain_size: Decimal | None
    db_positions: tuple[DbPositionRef, ...]
    delta: Decimal  # signed (Σ DB signed size − chain signed size); the amount to reconcile

    def to_dict(self) -> dict[str, object]:
        """Serialise for the errors.context JSONB column (Decimals → str, inv #12)."""
        return {
            "symbol": self.symbol,
            "kind": self.kind,
            "chain_side": self.chain_side,
            "chain_size": None if self.chain_size is None else str(self.chain_size),
            "delta": str(self.delta),
            "db_positions": [r.to_dict() for r in self.db_positions],
        }


def _signed(side: str, size: Decimal) -> Decimal:
    """LONG → +size, SHORT → −size (nets long/short like the exchange does)."""
    return size if side == "LONG" else -size


def _diverges(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) > _SIZE_ABS_TOL + _SIZE_REL_TOL * max(abs(a), abs(b))


def detect_chain_divergences(
    db_positions: Sequence[_DbPositionLike],
    chain_positions: Sequence[_ChainPositionLike],
) -> list[ChainDivergence]:
    """Compare DB-open vs chain-open positions PER COIN (netted) and return every divergence.

    DB rows are aggregated per symbol (signed sum) because Hyperliquid nets per coin; the chain
    holds at most one position per symbol. Deterministic order (by symbol). No side effects.
    """
    db_by_symbol: dict[str, list[_DbPositionLike]] = {}
    for p in db_positions:
        db_by_symbol.setdefault(p.symbol, []).append(p)
    # HL nets per coin → one chain position per symbol (last wins if the venue ever returned more).
    chain_by_symbol = {c.symbol: c for c in chain_positions}

    divergences: list[ChainDivergence] = []
    for symbol in sorted(set(db_by_symbol) | set(chain_by_symbol)):
        rows = db_by_symbol.get(symbol, [])
        chain = chain_by_symbol.get(symbol)
        db_signed = sum((_signed(r.side, r.size_units) for r in rows), Decimal("0"))
        chain_signed = _signed(chain.side, chain.size_units) if chain is not None else Decimal("0")

        if not _diverges(db_signed, chain_signed):
            continue

        refs = tuple(DbPositionRef(str(r.id), r.side, r.size_units) for r in rows)
        chain_side = chain.side if chain is not None else None
        chain_size = chain.size_units if chain is not None else None
        delta = db_signed - chain_signed

        if not rows:
            kind: DivergenceKind = "missing_row"
        elif chain is None:
            kind = "zombie_row"  # DB open, chain flat → all rows stale
        elif abs(db_signed) > abs(chain_signed) and (db_signed > 0) == (chain_signed > 0):
            kind = "zombie_row"  # DB over-counts on the same side → excess/partial zombie
        else:
            kind = "size_mismatch"  # chain larger than DB sum, or a side flip

        divergences.append(ChainDivergence(symbol, kind, chain_side, chain_size, refs, delta))
    return divergences
