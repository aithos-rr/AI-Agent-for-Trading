"""Backfill missing ``fee_events`` for pre-fix orders (finding A).

Before the finding-A fix (`51a8e05`), ``RealHyperliquidClient`` hard-coded
``OrderResult.fee_usd=None`` for entry/close orders, so ``PositionsRepository`` wrote no
``fee_events`` and the M6 outcomes carry ``sum_fees_usd = 0`` (net PnL overstated). The fix
reconciles fees going FORWARD only. This script repairs the HISTORICAL rows.

Approach:
  1. Per model wallet, pull fills in the model's order window from HL ``userFillsByTime``
     (read-only, no key). Each fill has ``oid`` + ``fee``.
  2. Build ``oid -> Σ fee`` (an order can fill in several partials).
  3. For every ``orders`` row with an ``hl_order_id`` in that map and NO existing ``fee_events``
     row, create a ``FeeEvent`` (``taker_open`` for ENTRY, else ``taker_close``), linked to the
     order's resolved position.
  4. Recompute each affected ``outcomes`` row: ``sum_fees_usd`` / ``pnl_net_fee_usd`` /
     ``pnl_net_fee_funding_usd`` / ``was_profitable_net``.

SAFE BY DEFAULT: dry-run — prints the plan and writes NOTHING. Pass ``--execute`` to commit.
Runs one transaction per model. No schema change (every table exists). Honors inv #12 (Decimal
via ``str``), inv #1 (every query filtered by ``model_id``), inv #9 (testnet).

Usage:
    # preview (no writes):
    AIAT_DATABASE_URL=postgresql+asyncpg://... uv run python scripts/backfill_fees.py
    # write (after reviewing the dry-run):
    AIAT_DATABASE_URL=postgresql+asyncpg://... uv run python scripts/backfill_fees.py --execute
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.context.collectors.onchain import HLPublicInfoClient
from aiat.db.models.fee_event import FeeEvent
from aiat.db.models.model import Model
from aiat.db.models.order import Order
from aiat.db.models.outcome import Outcome
from aiat.db.models.position import Position

logger = structlog.get_logger(__name__)

_HL_FILL_CAP = 2000  # HL userFillsByTime returns at most ~2000 fills per request


def build_oid_fee_map(fills: Sequence[object]) -> dict[str, Decimal]:
    """Sum the ``fee`` of every fill grouped by ``oid`` (str-keyed). Skips malformed records."""
    out: dict[str, Decimal] = {}
    for f in fills:
        if not isinstance(f, dict):
            continue
        oid = f.get("oid")
        fee_raw = f.get("fee")
        if oid is None or fee_raw is None:
            continue
        try:
            fee = Decimal(str(fee_raw))
        except (InvalidOperation, ValueError):
            continue
        key = str(oid)
        out[key] = out.get(key, Decimal("0")) + fee
    return out


def fee_type_for(order_kind: str) -> str:
    """Map an order_kind to the fee_type CHECK value (mirrors positions._fee_type)."""
    return "taker_open" if order_kind == "entry" else "taker_close"


async def _resolve_position(session: AsyncSession, order: Order) -> Position | None:
    """Find the position an order belongs to: entry/SL/TP link via opening_action_id, a model
    close links via closing_action_id. At most one matches (both are per-position)."""
    return await session.scalar(
        select(Position).where(
            (Position.opening_action_id == order.decision_action_id)
            | (Position.closing_action_id == order.decision_action_id)
        )
    )


async def _recompute_outcomes(session: AsyncSession, position_ids: set[uuid.UUID]) -> None:
    """Recompute fee-derived fields on the outcomes of the given positions."""
    for pid in position_ids:
        outcome = await session.scalar(select(Outcome).where(Outcome.position_id == pid))
        if outcome is None:
            continue
        sum_fees = await session.scalar(
            select(func.coalesce(func.sum(FeeEvent.fee_usd), Decimal("0"))).where(
                FeeEvent.position_id == pid
            )
        )
        outcome.sum_fees_usd = sum_fees
        outcome.pnl_net_fee_usd = outcome.realized_pnl_gross_usd - sum_fees
        outcome.pnl_net_fee_funding_usd = outcome.pnl_net_fee_usd - outcome.sum_funding_usd
        outcome.was_profitable_net = outcome.pnl_net_fee_funding_usd > Decimal("0")
    await session.flush()


async def _backfill_model(
    session: AsyncSession,
    hl: HLPublicInfoClient,
    model: Model,
    experiment_id: str | None,
    execute: bool,
) -> int:
    """Plan (and, if execute, write) the missing fee_events for one model. Returns the count."""
    window = select(func.min(Order.submitted_at), func.max(Order.submitted_at)).where(
        Order.model_id == model.id
    )
    if experiment_id is not None:
        window = window.where(Order.experiment_id == uuid.UUID(experiment_id))
    row = (await session.execute(window)).one()
    min_at, max_at = row[0], row[1]
    if min_at is None:
        return 0  # this model placed no orders

    start_ms = int(min_at.timestamp() * 1000) - 1000
    end_ms = int(max_at.timestamp() * 1000) + 1000
    fills = await hl.user_fills_by_time(model.wallet_address, start_ms, end_ms)
    if len(fills) >= _HL_FILL_CAP:
        logger.warning(
            "backfill_fill_cap_hit",
            model_id=model.id,
            count=len(fills),
            note="window may be undercounted — narrow the window / paginate",
        )
    oid_fee = build_oid_fee_map(fills)

    orders_stmt = select(Order).where(Order.model_id == model.id, Order.hl_order_id.is_not(None))
    if experiment_id is not None:
        orders_stmt = orders_stmt.where(Order.experiment_id == uuid.UUID(experiment_id))
    orders = (await session.scalars(orders_stmt)).all()

    planned = 0
    affected: set[uuid.UUID] = set()
    for order in orders:
        if order.hl_order_id is None or order.hl_order_id not in oid_fee:
            continue
        already = await session.scalar(select(FeeEvent.id).where(FeeEvent.order_id == order.id))
        if already is not None:
            continue
        pos = await _resolve_position(session, order)
        if pos is None:
            logger.warning("backfill_no_position_for_order", order_id=str(order.id))
            continue
        fee = oid_fee[order.hl_order_id]
        planned += 1
        logger.info(
            "backfill_plan_fee_event",
            model_id=model.id,
            order_id=str(order.id),
            order_kind=order.order_kind,
            hl_order_id=order.hl_order_id,
            fee_usd=str(fee),
            execute=execute,
        )
        if execute:
            session.add(
                FeeEvent(
                    id=uuid.uuid4(),
                    order_id=order.id,
                    position_id=pos.id,
                    experiment_id=order.experiment_id,
                    model_id=order.model_id,
                    run_id=order.run_id,
                    fee_type=fee_type_for(order.order_kind),
                    fee_usd=fee,
                    occurred_at=order.filled_at or order.submitted_at,
                )
            )
            affected.add(pos.id)

    if execute:
        await session.flush()
        await _recompute_outcomes(session, affected)
        await session.commit()
        logger.info("backfill_model_committed", model_id=model.id, fee_events=planned)
    return planned


async def backfill(
    database_url: str, network: str, experiment_id: str | None, execute: bool
) -> int:
    """Backfill all models. Returns the total number of fee_events planned/written."""
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    hl = HLPublicInfoClient(network=network)
    total = 0
    try:
        async with factory() as session:
            models = (await session.scalars(select(Model))).all()
            for model in models:
                total += await _backfill_model(session, hl, model, experiment_id, execute)
    finally:
        await engine.dispose()
    logger.info("backfill_done", total_fee_events=total, execute=execute)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill missing fee_events (finding A).")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="write to the DB (default: dry-run, writes nothing)",
    )
    parser.add_argument("--experiment-id", default=None, help="restrict to one experiment UUID")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("AIAT_DATABASE_URL"),
        help="asyncpg URL (default: AIAT_DATABASE_URL)",
    )
    parser.add_argument(
        "--network", default=os.environ.get("AIAT_NETWORK", "testnet"), help="HL network"
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("AIAT_DATABASE_URL (or --database-url) is required")
    if not args.execute:
        logger.info("backfill_dry_run", note="no writes — pass --execute to commit")
    asyncio.run(backfill(args.database_url, args.network, args.experiment_id, args.execute))


if __name__ == "__main__":
    main()
