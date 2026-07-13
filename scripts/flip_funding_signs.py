"""One-time repair: flip the sign of existing ``funding_events`` rows (funding-sign fix).

Every existing ``funding_events`` row was written by ``FundingReconciler`` (the only writer)
BEFORE the sign fix, so each stored the raw HL ``usdc`` (``+ = received``) instead of the PRD
§3.2.6 canonical sign (``+ = paid``). This script flips them:

    UPDATE funding_events SET funding_amount_usd = -funding_amount_usd;

after which the whole table is consistent with the fixed reconciler and every consumer
(``pnl_net_fee_funding = pnl_net_fee - Σ funding_amount_usd``, tax-sim ``gross - fees - funding``).

RUN ORDER (critical): deploy the reconciler fix FIRST, then run this flip. If you flip before
redeploying, rows the OLD reconciler writes in between get re-inverted (wrong again).

⚠️ NOT IDEMPOTENT — running it twice re-inverts. Run EXACTLY ONCE. Dry-run is the default
(prints the count + samples, writes nothing); pass ``--execute`` to commit.

NOTE on outcomes: the M6 smoke closed its outcomes while ``funding_events`` was empty, so
``outcomes.sum_funding_usd`` is 0 there and needs no recompute. If you have since recomputed any
outcome from these funding rows, recompute those outcomes again after the flip.

Usage:
    AIAT_DATABASE_URL=postgresql+asyncpg://... uv run python scripts/flip_funding_signs.py
    AIAT_DATABASE_URL=postgresql+asyncpg://... uv run python scripts/flip_funding_signs.py --execute
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.db.models.funding_event import FundingEvent

logger = structlog.get_logger(__name__)

_SAMPLE = 10


async def flip(database_url: str, experiment_id: str | None, execute: bool) -> int:
    """Flip funding_amount_usd sign for all (or one experiment's) funding_events. Returns count."""
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            count_stmt = select(func.count()).select_from(FundingEvent)
            sample_stmt = select(
                FundingEvent.id, FundingEvent.model_id, FundingEvent.funding_amount_usd
            ).limit(_SAMPLE)
            upd = update(FundingEvent).values(funding_amount_usd=-FundingEvent.funding_amount_usd)
            if experiment_id is not None:
                exp = uuid.UUID(experiment_id)
                count_stmt = count_stmt.where(FundingEvent.experiment_id == exp)
                sample_stmt = sample_stmt.where(FundingEvent.experiment_id == exp)
                upd = upd.where(FundingEvent.experiment_id == exp)

            total = await session.scalar(count_stmt) or 0
            for row in (await session.execute(sample_stmt)).all():
                logger.info(
                    "flip_sample",
                    id=str(row[0]),
                    model_id=row[1],
                    before=str(row[2]),
                    after=str(-row[2]),
                    execute=execute,
                )
            if execute:
                await session.execute(upd)
                await session.commit()
                logger.info("flip_committed", rows=total)
            else:
                logger.info(
                    "flip_dry_run", rows=total, note="no writes — pass --execute to commit ONCE"
                )
    finally:
        await engine.dispose()
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Flip funding_events sign (one-time repair).")
    parser.add_argument(
        "--execute", action="store_true", help="commit the flip (default: dry-run). RUN ONCE."
    )
    parser.add_argument("--experiment-id", default=None, help="restrict to one experiment UUID")
    parser.add_argument("--database-url", default=os.environ.get("AIAT_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("AIAT_DATABASE_URL (or --database-url) is required")
    if args.execute:
        logger.warning("flip_execute_once", note="NOT idempotent — a second run re-inverts")
    asyncio.run(flip(args.database_url, args.experiment_id, args.execute))


if __name__ == "__main__":
    main()
