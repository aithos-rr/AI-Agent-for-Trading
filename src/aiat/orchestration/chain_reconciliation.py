"""DB↔chain position reconciliation — detection only (ADR-0025, finding from smoke M6).

At the start of each agent tick the loop compares the positions the DB believes are OPEN
against what the chain (``clearinghouseState`` via ``fetch_portfolio_state``) actually holds.
A flip (close→open) is two non-atomic market orders, and an SL/TP/liquidation can close a
position on-chain before the loop's ``_check_pending_closures`` records it — so DB and chain
can diverge. This module is the pure detector; the loop turns any divergence into a
``ChainDivergence`` errors row + a warning log and then **proceeds** (M6.2 = detect + alert,
NO auto-repair — see ADR-0025 for why auto-repair is deferred).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

# Size comparison tolerance: DB stores the executed (quantized) size and the chain reports
# ``szi``, which should match exactly; a small abs+rel band absorbs float→Decimal string
# artifacts and per-asset szDecimals rounding without raising false divergences.
_SIZE_ABS_TOL = Decimal("0.000001")
_SIZE_REL_TOL = Decimal("0.005")  # 0.5%

DivergenceKind = Literal["missing_on_chain", "missing_in_db", "side_mismatch", "size_mismatch"]


class _PositionLike(Protocol):
    """Structural view shared by the DB Position ORM row and the chain OpenPositionSummary."""

    @property
    def symbol(self) -> str: ...
    @property
    def side(self) -> str: ...
    @property
    def size_units(self) -> Decimal: ...


@dataclass(frozen=True)
class ChainDivergence:
    """One DB↔chain mismatch for a symbol (JSON-serialisable via :meth:`to_dict`)."""

    symbol: str
    kind: DivergenceKind
    db_side: str | None
    chain_side: str | None
    db_size: Decimal | None
    chain_size: Decimal | None

    def to_dict(self) -> dict[str, str | None]:
        """Serialise for the errors.context JSONB column (Decimals → str, inv #12)."""
        return {
            "symbol": self.symbol,
            "kind": self.kind,
            "db_side": self.db_side,
            "chain_side": self.chain_side,
            "db_size": None if self.db_size is None else str(self.db_size),
            "chain_size": None if self.chain_size is None else str(self.chain_size),
        }


def _size_diverges(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) > _SIZE_ABS_TOL + _SIZE_REL_TOL * max(abs(a), abs(b))


def detect_chain_divergences(
    db_positions: Sequence[_PositionLike],
    chain_positions: Sequence[_PositionLike],
) -> list[ChainDivergence]:
    """Compare DB-open vs chain-open positions by symbol and return every divergence.

    Kinds: ``missing_on_chain`` (DB open, chain flat — a close the loop hasn't recorded),
    ``missing_in_db`` (chain holds an untracked position), ``side_mismatch``, ``size_mismatch``
    (beyond tolerance). Deterministic order (by symbol). No side effects.
    """
    db_by = {p.symbol: p for p in db_positions}
    chain_by = {p.symbol: p for p in chain_positions}
    divergences: list[ChainDivergence] = []
    for symbol in sorted(set(db_by) | set(chain_by)):
        db = db_by.get(symbol)
        chain = chain_by.get(symbol)
        if db is not None and chain is None:
            divergences.append(
                ChainDivergence(symbol, "missing_on_chain", db.side, None, db.size_units, None)
            )
        elif db is None and chain is not None:
            divergences.append(
                ChainDivergence(symbol, "missing_in_db", None, chain.side, None, chain.size_units)
            )
        elif db is not None and chain is not None:
            if db.side != chain.side:
                divergences.append(
                    ChainDivergence(
                        symbol,
                        "side_mismatch",
                        db.side,
                        chain.side,
                        db.size_units,
                        chain.size_units,
                    )
                )
            elif _size_diverges(db.size_units, chain.size_units):
                divergences.append(
                    ChainDivergence(
                        symbol,
                        "size_mismatch",
                        db.side,
                        chain.side,
                        db.size_units,
                        chain.size_units,
                    )
                )
    return divergences
