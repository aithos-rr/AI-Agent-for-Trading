"""Funding ledger reconciliation (finding B, PRD §4.2, ADR-0031).

Materialises the ``funding_events`` ledger the smoke M6 run left empty. On Hyperliquid
perps funding accrues hourly; the context-orchestrator runs this every 8h, reads each
model wallet's realized funding payments from the public HL ``userFunding`` endpoint, and
writes one ``FundingEvent`` per hourly payment against the open position it accrued on.

Design (ADR-0031):
  - **Orchestrator, not agent**: the orchestrator already holds a DB session factory and a
    read-only HL info client; funding is a per-wallet ledger fact (public, no private key).
  - **Read the actual payments, don't compute**: ``userFunding`` returns the exact USDC
    delta + rate HL applied, so ``funding_amount_usd``/``funding_rate`` are venue-accurate
    rather than a re-derivation from notional × rate.
  - **Idempotent by natural key** ``(position_id, funding_period_end)``: there is no UNIQUE
    constraint (no migration added), so we check-then-insert. The job is single-instance
    (APScheduler ``max_instances=1``), so there is no write race.
  - ``outcomes.sum_funding_usd`` already sums this table on close — no other wiring needed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiat.db.models.funding_event import FundingEvent
from aiat.db.models.model import Model
from aiat.db.models.position import Position

logger = structlog.get_logger(__name__)

# HL funding accrues hourly on perps; each userFunding record is one hourly payment.
_FUNDING_PERIOD = timedelta(hours=1)
# Look back 25h by default so an 8h job overlaps its own prior windows (idempotency dedups
# the overlap) and tolerates a missed run without dropping a payment.
_DEFAULT_LOOKBACK_MS = 25 * 60 * 60 * 1000
_SUPPORTED_SYMBOLS: frozenset[str] = frozenset({"BTC", "ETH", "SOL"})


class FundingSource(Protocol):
    """Structural type for the read-only HL funding endpoint (avoids a hard dependency on
    context.collectors — any object with this coroutine works, incl. HLPublicInfoClient)."""

    async def user_funding_history(
        self, user: str, start_time_ms: int, end_time_ms: int | None = None
    ) -> list[dict[str, object]]: ...


@dataclass(frozen=True)
class FundingReconcileResult:
    """Outcome of one reconcile pass (for logging/observability, not persisted)."""

    created: int
    skipped: int
    wallets_queried: int
    wallet_errors: int
    parse_failed: int = 0  # funding-typed records we could NOT parse (possible HL shape drift)


@dataclass(frozen=True)
class _FundingDelta:
    coin: str
    usdc: Decimal
    rate: Decimal
    period_end: datetime


def _parse_funding_record(rec: dict[str, object]) -> _FundingDelta | None:
    """Parse one HL ``userFunding`` record into a normalized delta, or None if unusable.

    Expected shape (ASSUMPTION, validate against live testnet — ADR-0031)::

        {"time": 1683849600076, "hash": "0x..",
         "delta": {"type": "funding", "coin": "BTC", "usdc": "-0.31", "fundingRate": "0.0000125"}}

    Money via ``Decimal(str(...))`` (inv #12). Non-funding deltas, unsupported coins, or
    malformed numerics are skipped (returns None) so one bad record cannot abort the pass —
    ``reconcile`` counts funding-typed records it could NOT parse and warns (silent shape drift
    is the finding-A-class risk).

    SIGN: ``usdc`` is returned VERBATIM here (raw HL, ``+ = received`` / ``- = paid`` — VALIDATED
    2026-07-12 vs the HL funding CSV, ADR-0031). The PRD §3.2.6 canonical DB convention is the
    OPPOSITE (``+ = paid``), so ``reconcile`` NEGATES ``usdc`` when writing ``funding_amount_usd``.
    This parser stays faithful to HL; the convention flip is applied at storage.
    """
    delta = rec.get("delta")
    time_ms = rec.get("time")
    if not isinstance(delta, dict) or not isinstance(time_ms, int):
        return None
    if delta.get("type") != "funding":
        return None
    coin = delta.get("coin")
    if not isinstance(coin, str) or coin not in _SUPPORTED_SYMBOLS:
        return None
    usdc_raw = delta.get("usdc")
    rate_raw = delta.get("fundingRate")
    if usdc_raw is None or rate_raw is None:
        return None
    try:
        usdc = Decimal(str(usdc_raw))
        rate = Decimal(str(rate_raw))
        # fromtimestamp can raise (OSError/OverflowError/ValueError) on an out-of-range ms —
        # keep it inside the guard so a single bad record returns None instead of aborting.
        period_end = datetime.fromtimestamp(time_ms / 1000, tz=UTC)
    except (InvalidOperation, ValueError, OSError, OverflowError):
        return None
    return _FundingDelta(coin=coin, usdc=usdc, rate=rate, period_end=period_end)


def _looks_like_funding(rec: dict[str, object]) -> bool:
    """True if the record is shaped like a funding delta (used to flag unparsable ones)."""
    delta = rec.get("delta")
    return isinstance(delta, dict) and delta.get("type") == "funding"


class FundingReconciler:
    """Writes ``funding_events`` for open positions from HL ``userFunding`` (ADR-0031)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        funding_source: FundingSource,
        experiment_id: str,
        lookback_ms: int = _DEFAULT_LOOKBACK_MS,
    ) -> None:
        self._session_factory = session_factory
        self._source = funding_source
        self._experiment_id = experiment_id
        self._lookback_ms = lookback_ms

    async def reconcile(self, now_ms: int) -> FundingReconcileResult:
        """Fetch funding for every open-position wallet and persist missing FundingEvents.

        Args:
            now_ms: Current time in unix milliseconds (injected so the pass is deterministic
                in tests; the scheduled job passes the wall-clock value).
        """
        start_ms = now_ms - self._lookback_ms
        created = skipped = wallets = wallet_errors = parse_failed = 0

        async with self._session_factory() as session:
            open_positions = await self._open_positions(session)
            if not open_positions:
                return FundingReconcileResult(0, 0, 0, 0)

            wallet_by_model = await self._wallet_by_model(
                session, {p.model_id for p in open_positions}
            )
            # positions grouped per model so we can attribute each funding delta by coin+time.
            positions_by_model: dict[str, list[Position]] = {}
            for pos in open_positions:
                positions_by_model.setdefault(pos.model_id, []).append(pos)

            for model_id, positions in positions_by_model.items():
                wallet = wallet_by_model.get(model_id)
                if wallet is None:
                    logger.warning("funding_no_wallet_for_model", model_id=model_id)
                    continue
                wallets += 1
                try:
                    records = await self._source.user_funding_history(wallet, start_ms)
                except Exception as exc:  # noqa: BLE001 — one wallet must not abort the pass
                    wallet_errors += 1
                    logger.warning("funding_fetch_failed", model_id=model_id, error=str(exc))
                    continue

                for rec in records:
                    delta = _parse_funding_record(rec)
                    if delta is None:
                        # A funding-typed record we could not parse ⇒ possible HL shape drift.
                        # Count it so the silent-skip (finding-A-class) risk is observable.
                        if _looks_like_funding(rec):
                            parse_failed += 1
                        continue
                    matched = self._match_position(positions, delta)
                    if matched is None:
                        continue
                    if await self._already_recorded(session, matched.id, delta.period_end):
                        skipped += 1
                        continue
                    session.add(
                        FundingEvent(
                            id=uuid.uuid4(),
                            position_id=matched.id,
                            experiment_id=matched.experiment_id,
                            model_id=matched.model_id,
                            funding_rate=delta.rate,
                            # SIGN: HL `usdc` is +=received / -=paid (validated empirically vs the
                            # HL funding CSV). The DB/PRD canonical convention (§3.2.6) is the
                            # OPPOSITE — +=paid / -=received — which every consumer assumes
                            # (`pnl_net_fee_funding = pnl_net_fee - Σ funding_amount_usd`, tax-sim
                            # `gross - fees - funding`). So negate at ingest to store the PRD sign;
                            # net PnL then comes out correct (received → raises, paid → lowers).
                            funding_amount_usd=-delta.usdc,
                            funding_period_start=delta.period_end - _FUNDING_PERIOD,
                            funding_period_end=delta.period_end,
                        )
                    )
                    created += 1

            await session.commit()

        if parse_failed:
            # Loud: funding records arrived but did not fit the assumed shape → likely drift.
            logger.warning(
                "funding_records_unparsed",
                parse_failed=parse_failed,
                note="funding-typed HL records did not match the assumed shape (ADR-0031)",
            )
        logger.info(
            "funding_reconcile_done",
            created=created,
            skipped=skipped,
            wallets=wallets,
            wallet_errors=wallet_errors,
            parse_failed=parse_failed,
        )
        return FundingReconcileResult(created, skipped, wallets, wallet_errors, parse_failed)

    async def _open_positions(self, session: AsyncSession) -> list[Position]:
        result = await session.execute(
            select(Position).where(
                Position.experiment_id == uuid.UUID(self._experiment_id),
                Position.closed_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def _wallet_by_model(self, session: AsyncSession, model_ids: set[str]) -> dict[str, str]:
        result = await session.execute(
            select(Model.id, Model.wallet_address).where(Model.id.in_(model_ids))
        )
        return {row[0]: row[1] for row in result.all()}

    @staticmethod
    def _match_position(positions: list[Position], delta: _FundingDelta) -> Position | None:
        """Pick the open position for this coin that was already open at the funding time.

        v2 holds at most one open position per symbol per model, so at most one candidate
        matches; the ``opened_at`` guard drops funding that accrued before this position
        opened (e.g. a prior closed position in the same coin within the lookback window).
        """
        candidates = [
            p
            for p in positions
            if p.symbol == delta.coin and _as_utc(p.opened_at) <= delta.period_end
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda p: _as_utc(p.opened_at))

    @staticmethod
    async def _already_recorded(
        session: AsyncSession, position_id: uuid.UUID, period_end: datetime
    ) -> bool:
        existing = await session.scalar(
            select(FundingEvent.id).where(
                FundingEvent.position_id == position_id,
                FundingEvent.funding_period_end == period_end,
            )
        )
        return existing is not None


def _as_utc(dt: datetime) -> datetime:
    """Treat a naive DB timestamp as UTC so comparisons with the funding time are sound."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
