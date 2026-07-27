"""One-shot repair of 5 corrupted zombie ``positions`` rows (experiment 5555…, M6.1).

Detection T4 (ADR-0034 vocabulary / ADR-0025 chain↔DB reconciliation) confirmed five
corrupted ``positions`` rows in the M6.1 smoke experiment
``55555555-5555-5555-5555-555555555555``. Every value below was verified on-chain via
``userFills`` on the Hyperliquid **testnet** API. Two root causes:

  * **T4b** (ADR-0025): an SL/TP trigger fired between two ticks and a reopen in the same
    tick short-circuited ``check_position_closure`` on ``szi != 0`` — the DB kept a zombie
    open (or mis-attributed a later exit/fee to it).
  * **usa-premium agent death**: its LLM credit was exhausted from 2026-07-19 ~01:30 UTC,
    so on-chain TP fills after that were never booked (the DB row stayed open).

This script repairs ONLY the historical data. The T4b root-cause fix is a **separate**
goal — this utility does not touch any runtime code (orchestrator/agent). See ADR-0035 for
the five conventions and the case table with on-chain order ids.

Two repair shapes:
  * **CORRECTION** (cases 1-2): the row is closed with the WRONG close (a later
    ``model_close`` exit/fee mis-applied to a zombie). Rewrite it to the real autonomous SL
    close, fix the mis-applied close fee, reassign funding accrued after the real close to
    the next position, and recompute the existing ``outcomes`` row.
  * **CLOSURE** (cases 3-5): the row is still OPEN in the DB but was closed on-chain by a TP
    trigger. Close it (``take_profit``, ``closing_action_id`` NULL — ADR-0030), insert the
    ``taker_close`` ``fee_event`` on the fired TP order (ADR-0032), and insert the
    ``outcomes`` row.

SAFE BY DEFAULT — dry-run: prints a per-row diff (old→new + planned inserts) and writes
NOTHING. Pass ``--apply`` to commit. All repairs run in ONE transaction; any dependency
resolution failure aborts the whole thing (rollback, nothing written). Idempotent via
per-row PRE-STATE ASSERTIONS: a row not in its expected corrupt state (already repaired or
divergent) is SKIPPED with a message; the other rows still proceed. Honors inv #9 (testnet),
inv #12 (``Decimal`` via ``str``), and reuses ``OutcomeResolver`` for the derived outcome
fields (no reimplementation).

Usage (SCRIPT=scripts/repair_zombie_positions.py):
    AIAT_DATABASE_URL=postgresql+asyncpg://...  uv run python $SCRIPT            # dry-run
    AIAT_DATABASE_URL=postgresql+asyncpg://...  uv run python $SCRIPT --apply    # commit
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.db.models.action import DecisionAction
from aiat.db.models.fee_event import FeeEvent
from aiat.db.models.funding_event import FundingEvent
from aiat.db.models.order import Order
from aiat.db.models.outcome import Outcome
from aiat.db.models.position import Position
from aiat.db.models.run import Run
from aiat.execution.outcome_resolver import (
    OutcomeResolver,
    OutcomeResult,
    PositionOutcomeInput,
    holding_duration_min,
)

logger = structlog.get_logger(__name__)

# The M6.1 smoke experiment. This script is one-shot for THIS experiment only and must never
# be run against the M7 official dataset (ADR-0035).
EXPERIMENT_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
_ZERO = Decimal("0")

# Plan statuses.
_REPAIR = "REPAIR"  # row is in the expected corrupt state and every dependency resolved
_SKIP = "SKIP"  # pre-state mismatch (already repaired or divergent) — no-op, others proceed
_ERROR = "ERROR"  # row is corrupt but a required dependency is missing — aborts the whole tx


def _dt(y: int, mo: int, d: int, h: int, mi: int, s: int, us: int = 0) -> datetime:
    """A tz-aware UTC datetime (all on-chain fill times are UTC)."""
    return datetime(y, mo, d, h, mi, s, us, tzinfo=UTC)


@dataclass(frozen=True)
class ZombieCase:
    """One corrupted position with its verified on-chain target state (ADR-0035)."""

    label: str
    kind: str  # "correction" | "closure"
    position_id: uuid.UUID
    model_id: str
    symbol: str
    close_oid: str  # on-chain order id of the real close (documentation / audit only)
    # ---- target positions state (on-chain truth) ----
    close_reason: str
    exit_price: Decimal
    realized_pnl_usd: Decimal  # gross = on-chain closedPnl (fee excluded), summed over fills
    closed_at: datetime
    close_fee_usd: Decimal  # correction: new value for the existing taker_close fee row
    #                          closure: value of the taker_close fee row to insert
    # ---- correction pre-state (idempotency / divergence guard) ----
    pre_close_reason: str | None = None
    pre_exit_price: Decimal | None = None
    pre_realized_pnl_usd: Decimal | None = None
    pre_closed_at: datetime | None = None
    pre_closing_action_id: uuid.UUID | None = None  # None ⇒ only assert NOT NULL (model_close)
    pre_close_fee_usd: Decimal | None = None
    outcome_id: uuid.UUID | None = None
    pre_outcome_gross: Decimal | None = None
    # ---- closure pre-state ----
    pre_size_units: Decimal | None = None
    pre_entry_price: Decimal | None = None


CASES: tuple[ZombieCase, ...] = (
    # CASE 1 — usa-premium BTC (CORRECTION). Real close: SL oid 56309051125 on 2026-07-13.
    # A 2026-07-14 FLAT closed a SHORT on-chain but the bookkeeping applied that exit (64608)
    # and that Close-Short fee (0.259336) to the zombie long, fabricating +12.307104.
    ZombieCase(
        label="CASE 1",
        kind="correction",
        position_id=uuid.UUID("3e6acfe5-24ed-43f2-87f1-bc78b2c20597"),
        model_id="usa-premium",
        symbol="BTC",
        close_oid="56309051125",
        close_reason="stop_loss",
        exit_price=Decimal("62280"),
        realized_pnl_usd=Decimal("-8.784576"),
        closed_at=_dt(2026, 7, 13, 13, 43, 40, 756000),
        close_fee_usd=Decimal("0.253915"),
        pre_close_reason="model_close",
        pre_exit_price=Decimal("64608"),
        pre_realized_pnl_usd=Decimal("12.307104"),
        pre_closed_at=_dt(2026, 7, 14, 17, 0, 50, 463984),
        pre_closing_action_id=uuid.UUID("918dd9e3-7dc6-45e2-950f-dfeb8e4dce1b"),
        pre_close_fee_usd=Decimal("0.259336"),
        outcome_id=uuid.UUID("fbc94297-e68a-40ab-a996-5d712f5aeff7"),
        pre_outcome_gross=Decimal("12.307104"),
    ),
    # CASE 2 — cn-premium BTC (CORRECTION). Real close: SL oid 56298713468 on 2026-07-11,
    # Close Long 0.00634 @ 62500 (closedPnl -5.72502, fee 0.178312). The zombie carried a
    # synthetic model_close exit 63743.3 (outcome gross 2.157502).
    ZombieCase(
        label="CASE 2",
        kind="correction",
        position_id=uuid.UUID("5b3c555e-14e8-42ba-9237-cb90f774f6ad"),
        model_id="cn-premium",
        symbol="BTC",
        close_oid="56298713468",
        close_reason="stop_loss",
        exit_price=Decimal("62500"),
        realized_pnl_usd=Decimal("-5.72502"),
        closed_at=_dt(2026, 7, 11, 1, 6, 37, 3000),
        close_fee_usd=Decimal("0.178312"),
        pre_close_reason="model_close",
        pre_exit_price=Decimal("63743.30000000"),
        pre_realized_pnl_usd=Decimal("2.157502"),
        pre_closed_at=None,  # not asserted for case 2
        pre_closing_action_id=None,  # unknown — assert only NOT NULL (model_close invariant)
        pre_close_fee_usd=Decimal("0.242666"),
        outcome_id=uuid.UUID("255a0cb8-600a-4d68-9724-6d22de69f0ad"),
        pre_outcome_gross=Decimal("2.157502"),
    ),
    # CASE 3 — usa-premium BTC (CLOSURE). TP oid 56597441225, VWAP of 8 fills.
    ZombieCase(
        label="CASE 3",
        kind="closure",
        position_id=uuid.UUID("da4823d5-5a8e-4b47-9935-7fee6b44633b"),
        model_id="usa-premium",
        symbol="BTC",
        close_oid="56597441225",
        close_reason="take_profit",
        exit_price=Decimal("64056.3"),
        realized_pnl_usd=Decimal("7.90145"),
        closed_at=_dt(2026, 7, 17, 17, 48, 5, 168000),
        close_fee_usd=Decimal("0.196297"),
        pre_size_units=Decimal("0.00681"),
        pre_entry_price=Decimal("62896"),
    ),
    # CASE 4 — usa-premium BTC (CLOSURE). TP oid 56623016995, VWAP of 6 fills.
    ZombieCase(
        label="CASE 4",
        kind="closure",
        position_id=uuid.UUID("c1624ba0-9f64-49cd-b8d2-c42008c32340"),
        model_id="usa-premium",
        symbol="BTC",
        close_oid="56623016995",
        close_reason="take_profit",
        exit_price=Decimal("65567.1"),
        realized_pnl_usd=Decimal("10.56115"),
        closed_at=_dt(2026, 7, 20, 18, 1, 55, 471000),
        close_fee_usd=Decimal("0.200632"),
        pre_size_units=Decimal("0.0068"),
        pre_entry_price=Decimal("64014"),
    ),
    # CASE 5 — usa-premium SOL (CLOSURE). TP oid 56650748691, single fill.
    ZombieCase(
        label="CASE 5",
        kind="closure",
        position_id=uuid.UUID("710fe90d-8a34-4432-b56b-af5c25e78686"),
        model_id="usa-premium",
        symbol="SOL",
        close_oid="56650748691",
        close_reason="take_profit",
        exit_price=Decimal("75.962"),
        realized_pnl_usd=Decimal("6.34226"),
        closed_at=_dt(2026, 7, 19, 2, 7, 15, 715000),
        close_fee_usd=Decimal("0.159292"),
        pre_size_units=Decimal("4.66"),
        pre_entry_price=Decimal("74.601"),
    ),
)


@dataclass
class FundingMove:
    """One funding_events row reassigned to the next position (convention 5)."""

    funding_id: uuid.UUID
    created_at: datetime
    amount_usd: Decimal


@dataclass
class CasePlan:
    """The resolved, read-only plan for one case (built without any writes)."""

    case: ZombieCase
    status: str
    reason: str = ""
    # human-readable diff: table -> {field: [old, new]}
    changes: dict[str, dict[str, list[str | None]]] = field(default_factory=dict)
    inserts: dict[str, dict[str, str]] = field(default_factory=dict)
    funding_moves: list[FundingMove] = field(default_factory=list)
    funding_target_id: uuid.UUID | None = None
    # resolved entities/values for the apply phase (populated only when status == REPAIR)
    position: Position | None = None
    close_fee_event: FeeEvent | None = None
    outcome: Outcome | None = None
    funding_rows: list[FundingEvent] = field(default_factory=list)
    funding_target: Position | None = None
    trigger_order: Order | None = None
    closing_run_id: uuid.UUID | None = None
    outcome_result: OutcomeResult | None = None


def _s(value: object) -> str | None:
    """Render a value for the diff (None stays None so JSON shows null)."""
    return None if value is None else str(value)


# --------------------------------------------------------------------------- #
# Dependency resolution (read-only helpers)                                   #
# --------------------------------------------------------------------------- #


async def _find_closing_run(
    session: AsyncSession, model_id: str, closed_at: datetime
) -> uuid.UUID | None:
    """Convention 2: the first run of the same model (any status) started after the real
    close. A zombie closed after the agent died is booked against the first FAILED run that
    followed — hence "any status"."""
    run_id: uuid.UUID | None = await session.scalar(
        select(Run.id)
        .where(
            Run.experiment_id == EXPERIMENT_ID,
            Run.model_id == model_id,
            Run.run_started_at > closed_at,
        )
        .order_by(Run.run_started_at.asc())
        .limit(1)
    )
    return run_id


async def _find_trigger_order(
    session: AsyncSession, opening_action_id: uuid.UUID, model_id: str, order_kind: str
) -> Order | None:
    """The fired SL/TP trigger order (created at open_position, order_kind stop_loss/
    take_profit). fee_events.order_id is NOT NULL, so an autonomous close fee links here —
    mirrors PositionsRepository.close_position (ADR-0032)."""
    order: Order | None = await session.scalar(
        select(Order)
        .where(
            Order.decision_action_id == opening_action_id,
            Order.model_id == model_id,
            Order.order_kind == order_kind,
        )
        .order_by(Order.submitted_at.asc())
        .limit(1)
    )
    return order


async def _find_next_position(
    session: AsyncSession, model_id: str, symbol: str, after: datetime
) -> Position | None:
    """Convention 5: the next position of the same model/symbol opened after the real close
    (earliest opened_at). Funding accrued after the real close belongs to it."""
    position: Position | None = await session.scalar(
        select(Position)
        .where(
            Position.experiment_id == EXPERIMENT_ID,
            Position.model_id == model_id,
            Position.symbol == symbol,
            Position.opened_at > after,
        )
        .order_by(Position.opened_at.asc())
        .limit(1)
    )
    return position


async def _sum_fees(
    session: AsyncSession, position_id: uuid.UUID, exclude_id: uuid.UUID | None
) -> Decimal:
    stmt = select(func.coalesce(func.sum(FeeEvent.fee_usd), _ZERO)).where(
        FeeEvent.position_id == position_id
    )
    if exclude_id is not None:
        stmt = stmt.where(FeeEvent.id != exclude_id)
    return (await session.scalar(stmt)) or _ZERO


async def _sum_funding(
    session: AsyncSession, position_id: uuid.UUID, created_at_le: datetime | None
) -> Decimal:
    stmt = select(func.coalesce(func.sum(FundingEvent.funding_amount_usd), _ZERO)).where(
        FundingEvent.position_id == position_id
    )
    if created_at_le is not None:
        stmt = stmt.where(FundingEvent.created_at <= created_at_le)
    return (await session.scalar(stmt)) or _ZERO


def _resolve_outcome(
    pos: Position,
    *,
    gross: Decimal,
    sum_fees: Decimal,
    sum_funding: Decimal,
    closed_at: datetime,
    closing_run_id: uuid.UUID,
    confidence: Decimal,
    time_horizon_min: int,
) -> OutcomeResult:
    """Reuse OutcomeResolver.resolve_position for every derived field (net PnL, profitability,
    horizon_met) — identical to the runtime close path, no reimplementation (ADR-0035)."""
    return OutcomeResolver().resolve_position(
        PositionOutcomeInput(
            opening_action_id=pos.opening_action_id,
            opening_run_id=pos.opening_run_id,
            closing_run_id=closing_run_id,
            experiment_id=pos.experiment_id,
            model_id=pos.model_id,
            symbol=pos.symbol,
            decision_action_confidence=confidence,
            decision_action_time_horizon_min=time_horizon_min,
            realized_pnl_gross_usd=gross,
            sum_fees_usd=sum_fees,
            sum_funding_usd=sum_funding,
            holding_duration_min=holding_duration_min(pos.opened_at, closed_at),
        )
    )


def _outcome_changes(
    old: Outcome | None, r: OutcomeResult, closing_run_id: uuid.UUID
) -> dict[str, list[str | None]]:
    """Diff for the outcomes row (old None ⇒ an insert; confidence/time_horizon untouched)."""
    return {
        "realized_pnl_gross_usd": [
            _s(old.realized_pnl_gross_usd if old else None),
            _s(r.realized_pnl_gross_usd),
        ],
        "sum_fees_usd": [_s(old.sum_fees_usd if old else None), _s(r.sum_fees_usd)],
        "sum_funding_usd": [_s(old.sum_funding_usd if old else None), _s(r.sum_funding_usd)],
        "pnl_net_fee_usd": [_s(old.pnl_net_fee_usd if old else None), _s(r.pnl_net_fee_usd)],
        "pnl_net_fee_funding_usd": [
            _s(old.pnl_net_fee_funding_usd if old else None),
            _s(r.pnl_net_fee_funding_usd),
        ],
        "was_profitable_net": [
            _s(old.was_profitable_net if old else None),
            _s(r.was_profitable_net),
        ],
        "holding_duration_min": [
            _s(old.holding_duration_min if old else None),
            _s(r.holding_duration_min),
        ],
        "horizon_met": [_s(old.horizon_met if old else None), _s(r.horizon_met)],
        "closing_run_id": [_s(old.closing_run_id if old else None), _s(closing_run_id)],
    }


# --------------------------------------------------------------------------- #
# Planning (read-only)                                                        #
# --------------------------------------------------------------------------- #


async def _plan_correction(session: AsyncSession, case: ZombieCase) -> CasePlan:
    pos = await session.get(Position, case.position_id)
    if pos is None:
        return CasePlan(case, _ERROR, "position not found")

    close_fees = list(
        (
            await session.scalars(
                select(FeeEvent).where(
                    FeeEvent.position_id == pos.id, FeeEvent.fee_type == "taker_close"
                )
            )
        ).all()
    )
    outcome = await session.scalar(select(Outcome).where(Outcome.position_id == pos.id))

    # Pre-state assertions (SKIP on any mismatch: already repaired or divergent).
    mismatches: list[str] = []
    if pos.close_reason != case.pre_close_reason:
        mismatches.append(f"close_reason={pos.close_reason!r} (expected {case.pre_close_reason!r})")
    if pos.exit_price != case.pre_exit_price:
        mismatches.append(f"exit_price={pos.exit_price} (expected {case.pre_exit_price})")
    if pos.realized_pnl_usd != case.pre_realized_pnl_usd:
        mismatches.append(
            f"realized_pnl_usd={pos.realized_pnl_usd} (expected {case.pre_realized_pnl_usd})"
        )
    if case.pre_closed_at is not None and pos.closed_at != case.pre_closed_at:
        mismatches.append(f"closed_at={pos.closed_at} (expected {case.pre_closed_at})")
    if case.pre_closing_action_id is not None:
        if pos.closing_action_id != case.pre_closing_action_id:
            mismatches.append(
                f"closing_action_id={pos.closing_action_id} (expected {case.pre_closing_action_id})"
            )
    elif pos.closing_action_id is None:
        mismatches.append("closing_action_id is NULL (expected NOT NULL for model_close)")
    if len(close_fees) == 1 and close_fees[0].fee_usd != case.pre_close_fee_usd:
        mismatches.append(
            f"taker_close.fee_usd={close_fees[0].fee_usd} (expected {case.pre_close_fee_usd})"
        )
    if outcome is not None:
        if case.outcome_id is not None and outcome.id != case.outcome_id:
            mismatches.append(f"outcome.id={outcome.id} (expected {case.outcome_id})")
        if outcome.realized_pnl_gross_usd != case.pre_outcome_gross:
            mismatches.append(
                f"outcome.gross={outcome.realized_pnl_gross_usd} "
                f"(expected {case.pre_outcome_gross})"
            )
    if mismatches:
        return CasePlan(case, _SKIP, "; ".join(mismatches))

    # Row IS in the expected corrupt state — required structure must be present (else abort).
    if len(close_fees) != 1:
        return CasePlan(
            case, _ERROR, f"expected exactly 1 taker_close fee_event, found {len(close_fees)}"
        )
    if outcome is None:
        return CasePlan(case, _ERROR, "no outcomes row for position")
    close_fee_event = close_fees[0]

    closing_run_id = await _find_closing_run(session, case.model_id, case.closed_at)
    if closing_run_id is None:
        return CasePlan(
            case,
            _ERROR,
            f"no run for {case.model_id} started after {case.closed_at} (convention 2)",
        )

    # Funding reassignment (convention 5): rows written after the real close belong to the
    # next position of the same model/symbol.
    funding_rows = list(
        (
            await session.scalars(
                select(FundingEvent)
                .where(
                    FundingEvent.position_id == pos.id,
                    FundingEvent.created_at > case.closed_at,
                )
                .order_by(FundingEvent.created_at.asc())
            )
        ).all()
    )
    funding_target: Position | None = None
    if funding_rows:
        funding_target = await _find_next_position(
            session, case.model_id, case.symbol, case.closed_at
        )
        if funding_target is None:
            return CasePlan(
                case, _ERROR, "funding rows to reassign but no next position found (convention 5)"
            )

    # Recompute outcome from the POST-repair sums (fee row updated, late funding reassigned).
    sum_fees_new = (
        await _sum_fees(session, pos.id, exclude_id=close_fee_event.id)
    ) + case.close_fee_usd
    sum_funding_new = await _sum_funding(session, pos.id, created_at_le=case.closed_at)
    result = _resolve_outcome(
        pos,
        gross=case.realized_pnl_usd,
        sum_fees=sum_fees_new,
        sum_funding=sum_funding_new,
        closed_at=case.closed_at,
        closing_run_id=closing_run_id,
        confidence=outcome.decision_action_confidence,
        time_horizon_min=outcome.decision_action_time_horizon_min,
    )

    plan = CasePlan(case, _REPAIR)
    plan.position = pos
    plan.close_fee_event = close_fee_event
    plan.outcome = outcome
    plan.funding_rows = funding_rows
    plan.funding_target = funding_target
    plan.funding_target_id = funding_target.id if funding_target else None
    plan.closing_run_id = closing_run_id
    plan.outcome_result = result
    plan.funding_moves = [
        FundingMove(fr.id, fr.created_at, fr.funding_amount_usd) for fr in funding_rows
    ]
    plan.changes = {
        "positions": {
            "close_reason": [_s(pos.close_reason), _s(case.close_reason)],
            "closing_action_id": [_s(pos.closing_action_id), None],
            "exit_price": [_s(pos.exit_price), _s(case.exit_price)],
            "realized_pnl_usd": [_s(pos.realized_pnl_usd), _s(case.realized_pnl_usd)],
            "closed_at": [_s(pos.closed_at), _s(case.closed_at)],
        },
        f"fee_events[{close_fee_event.id}]": {
            "fee_usd": [_s(close_fee_event.fee_usd), _s(case.close_fee_usd)],
        },
        f"outcomes[{outcome.id}]": _outcome_changes(outcome, result, closing_run_id),
    }
    return plan


async def _plan_closure(session: AsyncSession, case: ZombieCase) -> CasePlan:
    pos = await session.get(Position, case.position_id)
    if pos is None:
        return CasePlan(case, _ERROR, "position not found")

    mismatches: list[str] = []
    if pos.closed_at is not None:
        mismatches.append(f"closed_at={pos.closed_at} (expected NULL — already closed/repaired)")
    if case.pre_size_units is not None and pos.size_units != case.pre_size_units:
        mismatches.append(f"size_units={pos.size_units} (expected {case.pre_size_units})")
    if case.pre_entry_price is not None and pos.entry_price != case.pre_entry_price:
        mismatches.append(f"entry_price={pos.entry_price} (expected {case.pre_entry_price})")
    if mismatches:
        return CasePlan(case, _SKIP, "; ".join(mismatches))

    if await session.scalar(select(Outcome.id).where(Outcome.position_id == pos.id)) is not None:
        return CasePlan(case, _ERROR, "outcomes row already exists for an open position")
    # Defense-in-depth (symmetric with the outcome guard above): a still-open position must have
    # no taker_close fee yet — one would mean a partial/foreign close. fee_events has no uniqueness
    # backstop, so refuse rather than risk a duplicate taker_close insert.
    if (
        await session.scalar(
            select(FeeEvent.id).where(
                FeeEvent.position_id == pos.id, FeeEvent.fee_type == "taker_close"
            )
        )
        is not None
    ):
        return CasePlan(case, _ERROR, "taker_close fee_event already exists for an open position")

    trigger_order = await _find_trigger_order(
        session, pos.opening_action_id, case.model_id, case.close_reason
    )
    if trigger_order is None:
        return CasePlan(
            case, _ERROR, f"no {case.close_reason} trigger order for fee_events.order_id"
        )
    closing_run_id = await _find_closing_run(session, case.model_id, case.closed_at)
    if closing_run_id is None:
        return CasePlan(
            case,
            _ERROR,
            f"no run for {case.model_id} started after {case.closed_at} (convention 2)",
        )
    opening_action = await session.get(DecisionAction, pos.opening_action_id)
    if opening_action is None:
        return CasePlan(case, _ERROR, "opening decision_action not found")

    sum_fees_new = (await _sum_fees(session, pos.id, exclude_id=None)) + case.close_fee_usd
    sum_funding_new = await _sum_funding(session, pos.id, created_at_le=None)
    result = _resolve_outcome(
        pos,
        gross=case.realized_pnl_usd,
        sum_fees=sum_fees_new,
        sum_funding=sum_funding_new,
        closed_at=case.closed_at,
        closing_run_id=closing_run_id,
        confidence=opening_action.confidence,
        time_horizon_min=opening_action.time_horizon_min,
    )

    plan = CasePlan(case, _REPAIR)
    plan.position = pos
    plan.trigger_order = trigger_order
    plan.closing_run_id = closing_run_id
    plan.outcome_result = result
    plan.changes = {
        "positions": {
            "closed_at": [None, _s(case.closed_at)],
            "exit_price": [None, _s(case.exit_price)],
            "close_reason": [None, _s(case.close_reason)],
            "closing_action_id": [None, None],
            "realized_pnl_usd": [None, _s(case.realized_pnl_usd)],
        },
    }
    plan.inserts = {
        "fee_events": {
            "order_id": str(trigger_order.id),
            "run_id": str(closing_run_id),
            "fee_type": "taker_close",
            "fee_usd": str(case.close_fee_usd),
            "occurred_at": str(case.closed_at),
        },
        "outcomes": {
            "closing_run_id": str(closing_run_id),
            "realized_pnl_gross_usd": str(result.realized_pnl_gross_usd),
            "sum_fees_usd": str(result.sum_fees_usd),
            "sum_funding_usd": str(result.sum_funding_usd),
            "pnl_net_fee_usd": str(result.pnl_net_fee_usd),
            "pnl_net_fee_funding_usd": str(result.pnl_net_fee_funding_usd),
            "was_profitable_net": str(result.was_profitable_net),
            "holding_duration_min": str(result.holding_duration_min),
            "decision_action_confidence": str(result.decision_action_confidence),
            "decision_action_time_horizon_min": str(result.decision_action_time_horizon_min),
            "horizon_met": str(result.horizon_met),
        },
    }
    return plan


async def _plan_case(session: AsyncSession, case: ZombieCase) -> CasePlan:
    if case.kind == "correction":
        return await _plan_correction(session, case)
    return await _plan_closure(session, case)


# --------------------------------------------------------------------------- #
# Apply (writes; only reached for status == REPAIR after all plans validated) #
# --------------------------------------------------------------------------- #


async def _apply_correction(session: AsyncSession, plan: CasePlan) -> None:
    case = plan.case
    pos = plan.position
    assert pos is not None and plan.close_fee_event is not None
    assert plan.outcome is not None and plan.outcome_result is not None
    assert plan.closing_run_id is not None

    pos.close_reason = case.close_reason
    pos.closing_action_id = None
    pos.exit_price = case.exit_price
    pos.realized_pnl_usd = case.realized_pnl_usd
    pos.closed_at = case.closed_at
    plan.close_fee_event.fee_usd = case.close_fee_usd
    for fr in plan.funding_rows:
        assert plan.funding_target is not None
        fr.position_id = plan.funding_target.id
    await session.flush()

    r = plan.outcome_result
    o = plan.outcome
    o.realized_pnl_gross_usd = r.realized_pnl_gross_usd
    o.sum_fees_usd = r.sum_fees_usd
    o.sum_funding_usd = r.sum_funding_usd
    o.pnl_net_fee_usd = r.pnl_net_fee_usd
    o.pnl_net_fee_funding_usd = r.pnl_net_fee_funding_usd
    o.was_profitable_net = r.was_profitable_net
    o.holding_duration_min = r.holding_duration_min
    o.horizon_met = r.horizon_met
    o.closing_run_id = plan.closing_run_id
    # NOT touched (correct from the opening action / separate writer):
    # decision_action_confidence, decision_action_time_horizon_min,
    # pnl_net_fee_funding_tax_sim_usd, opening_action_id, opening_run_id.
    await session.flush()


async def _apply_closure(session: AsyncSession, plan: CasePlan) -> None:
    case = plan.case
    pos = plan.position
    assert pos is not None and plan.trigger_order is not None
    assert plan.outcome_result is not None and plan.closing_run_id is not None

    pos.closed_at = case.closed_at
    pos.exit_price = case.exit_price
    pos.close_reason = case.close_reason
    pos.closing_action_id = None  # autonomous TP (ADR-0030)
    pos.realized_pnl_usd = case.realized_pnl_usd
    await session.flush()

    session.add(
        FeeEvent(
            id=uuid.uuid4(),
            order_id=plan.trigger_order.id,
            position_id=pos.id,
            experiment_id=pos.experiment_id,
            model_id=pos.model_id,
            run_id=plan.closing_run_id,
            fee_type="taker_close",
            fee_usd=case.close_fee_usd,
            occurred_at=case.closed_at,
        )
    )
    await session.flush()

    r = plan.outcome_result
    session.add(
        Outcome(
            id=uuid.uuid4(),
            position_id=pos.id,
            opening_action_id=pos.opening_action_id,
            opening_run_id=pos.opening_run_id,
            closing_run_id=plan.closing_run_id,
            experiment_id=pos.experiment_id,
            model_id=pos.model_id,
            symbol=pos.symbol,
            realized_pnl_gross_usd=r.realized_pnl_gross_usd,
            sum_fees_usd=r.sum_fees_usd,
            sum_funding_usd=r.sum_funding_usd,
            pnl_net_fee_usd=r.pnl_net_fee_usd,
            pnl_net_fee_funding_usd=r.pnl_net_fee_funding_usd,
            pnl_net_fee_funding_tax_sim_usd=r.pnl_net_fee_funding_tax_sim_usd,
            was_profitable_net=r.was_profitable_net,
            holding_duration_min=r.holding_duration_min,
            decision_action_confidence=r.decision_action_confidence,
            decision_action_time_horizon_min=r.decision_action_time_horizon_min,
            horizon_met=r.horizon_met,
        )
    )
    await session.flush()


async def _apply_plan(session: AsyncSession, plan: CasePlan) -> None:
    if plan.case.kind == "correction":
        await _apply_correction(session, plan)
    else:
        await _apply_closure(session, plan)


# --------------------------------------------------------------------------- #
# Reporting + driver                                                          #
# --------------------------------------------------------------------------- #


def _log_plan(plan: CasePlan) -> None:
    case = plan.case
    funding = None
    if plan.funding_moves:
        total = sum((m.amount_usd for m in plan.funding_moves), _ZERO)
        funding = {
            "count": len(plan.funding_moves),
            "sum_usd": str(total),
            "target_position_id": str(plan.funding_target_id),
            "rows": [
                {
                    "id": str(m.funding_id),
                    "created_at": str(m.created_at),
                    "amount_usd": str(m.amount_usd),
                }
                for m in plan.funding_moves
            ],
        }
    logger.info(
        "repair_plan",
        case=case.label,
        action="CORRECTION" if case.kind == "correction" else "CLOSURE",
        position_id=str(case.position_id),
        model_id=case.model_id,
        symbol=case.symbol,
        close_oid=case.close_oid,
        status=plan.status,
        reason=plan.reason or None,
        changes=plan.changes or None,
        inserts=plan.inserts or None,
        funding_reassign=funding,
    )


def _summary(plans: list[CasePlan]) -> dict[str, int]:
    return {
        "corrected": sum(1 for p in plans if p.status == _REPAIR and p.case.kind == "correction"),
        "closed": sum(1 for p in plans if p.status == _REPAIR and p.case.kind == "closure"),
        "skipped": sum(1 for p in plans if p.status == _SKIP),
        "errored": sum(1 for p in plans if p.status == _ERROR),
    }


class RepairAbort(RuntimeError):
    """Raised when --apply hits an ERROR plan; the transaction is rolled back first."""


async def repair(database_url: str, network: str, apply: bool) -> dict[str, int]:
    """Plan (and, with apply=True, commit) the 5 zombie repairs in one transaction.

    Returns the summary counts. Raises RepairAbort (after an explicit rollback) if apply=True
    and any case resolved to ERROR during planning — nothing is written in that case. An
    unexpected DB error during the apply phase (e.g. a CHECK/IntegrityError at flush) propagates
    as its native exception, not RepairAbort; the transaction is still fully rolled back by the
    session context manager (commit is never reached), so nothing is written either way.
    """
    # Invariant #9: this utility writes trading bookkeeping and reads testnet on-chain truth;
    # refuse anything but testnet so it can never touch mainnet data.
    if network != "testnet":
        raise RuntimeError(
            f"repair_zombie_positions requires network='testnet' (inv #9), got {network!r}"
        )

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            plans = [await _plan_case(session, case) for case in CASES]
            for plan in plans:
                _log_plan(plan)
            summary = _summary(plans)

            if not apply:
                await session.rollback()
                logger.info("repair_dry_run", note="no writes — pass --apply to commit", **summary)
                return summary

            errored = [p for p in plans if p.status == _ERROR]
            if errored:
                await session.rollback()
                logger.error(
                    "repair_aborted",
                    errors={p.case.label: p.reason for p in errored},
                    note="a corrupt row was missing a required dependency — NOTHING written",
                    **summary,
                )
                raise RepairAbort(f"{len(errored)} case(s) errored; transaction rolled back")

            for plan in plans:
                if plan.status == _REPAIR:
                    await _apply_plan(session, plan)
            await session.commit()
            logger.info("repair_committed", **summary)
            return summary
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-shot repair of 5 zombie positions (exp 5555…, M6.1). See ADR-0035."
    )
    parser.add_argument(
        "--apply", action="store_true", help="commit the repair (default: dry-run, writes nothing)"
    )
    parser.add_argument("--database-url", default=os.environ.get("AIAT_DATABASE_URL"))
    parser.add_argument("--network", default=os.environ.get("AIAT_NETWORK", "testnet"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("AIAT_DATABASE_URL (or --database-url) is required")
    if not args.apply:
        logger.info("repair_dry_run_start", note="dry-run — no writes; pass --apply to commit")
    asyncio.run(repair(args.database_url, args.network, args.apply))


if __name__ == "__main__":
    main()
