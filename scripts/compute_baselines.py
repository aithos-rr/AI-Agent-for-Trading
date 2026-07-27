"""Catch-up / backfill of ``baseline_equity_snapshots`` from context snapshots (ADR-0036).

The three non-LLM baselines are computed **live** each tick by the context-orchestrator
(``aiat.baselines.runner.BaselineRunner``). This script replays the SAME logic over an
experiment's already-persisted ``context_snapshots`` to (a) fill the M6.1 gap where no live step
existed yet, and (b) recover after any live-step outage. It never calls Hyperliquid — every price
comes from the stored snapshots (inv #13-style parity).

It is idempotent: a tick that already has a baseline snapshot is skipped (its stored state is
carried forward so the sequence stays exact), so re-running only fills what is missing. Ticks with
no context snapshot (MissedTick) are simply absent — no snapshot is invented; the curve resumes at
the next available tick.

SAFE BY DEFAULT — dry-run: computes the full plan and writes NOTHING. Pass ``--apply`` to commit
(one transaction for the whole backfill). ``AIAT_DATABASE_URL`` from the environment.

Usage (SCRIPT=scripts/compute_baselines.py):
    AIAT_DATABASE_URL=postgresql+asyncpg://...  uv run python $SCRIPT              # dry-run
    AIAT_DATABASE_URL=postgresql+asyncpg://...  uv run python $SCRIPT --apply       # commit
    # restrict to one experiment (else the single existing experiment is auto-selected):
    AIAT_DATABASE_URL=...  uv run python $SCRIPT --experiment-id 5555...
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.baselines.compute import BASELINE_NAMES
from aiat.baselines.runner import SKIP_EXISTS, SKIP_NO_CONFIG, WRITE, BaselineRunner
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.experiment import Experiment
from aiat.domain.schemas import ContextBundle

logger = structlog.get_logger(__name__)


@dataclass
class _BaselineTally:
    write: int = 0
    skip_exists: int = 0
    skip_no_config: int = 0
    first_equity: Decimal | None = None
    last_equity: Decimal | None = None
    last_pnl: Decimal | None = None


@dataclass
class BackfillSummary:
    experiment_id: str
    context_ticks: int = 0
    malformed_snapshots: int = 0
    per_baseline: dict[str, _BaselineTally] = field(default_factory=dict)


async def _resolve_experiment_id(session: AsyncSession, explicit: str | None) -> str:
    """Return the target experiment id: explicit arg/env, else the single existing experiment."""
    if explicit:
        return explicit
    ids = (await session.scalars(select(Experiment.id))).all()
    if len(ids) == 1:
        return str(ids[0])
    raise SystemExit(
        f"specify --experiment-id: found {len(ids)} experiments (need exactly 1 to auto-select)"
    )


async def backfill(database_url: str, experiment_id: str | None, apply: bool) -> BackfillSummary:
    """Replay the baselines over an experiment's context snapshots. Returns the summary."""
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            exp_id = await _resolve_experiment_id(session, experiment_id)
            runner = BaselineRunner(exp_id)
            summary = BackfillSummary(exp_id)
            summary.per_baseline = {name: _BaselineTally() for name in BASELINE_NAMES}

            exp_uuid = uuid.UUID(exp_id)
            total = await session.scalar(
                select(func.count())
                .select_from(ContextSnapshot)
                .where(ContextSnapshot.experiment_id == exp_uuid)
            )
            logger.info(
                "baseline_backfill_start",
                experiment_id=exp_id,
                context_snapshots=total,
                apply=apply,
            )

            stmt = (
                select(ContextSnapshot)
                .where(ContextSnapshot.experiment_id == exp_uuid)
                .order_by(ContextSnapshot.tick_at.asc())
            )
            prev_states: dict[str, dict | None] = dict.fromkeys(BASELINE_NAMES)  # type: ignore[type-arg]

            for snap in (await session.scalars(stmt)).all():
                # A snapshot that won't validate OR is structurally incomplete (a symbol missing,
                # so bundle_to_market raises) is treated as a gap: skipped, counted, no snapshot
                # invented; the sequence resumes at the next valid tick (ADR-0036).
                try:
                    bundle = ContextBundle.model_validate(snap.context_json)
                    plans = await runner.process_tick(session, bundle, prev_states, apply=apply)
                except Exception as exc:
                    summary.malformed_snapshots += 1
                    logger.warning(
                        "baseline_backfill_bad_snapshot", tick_id=snap.tick_id, error=str(exc)
                    )
                    continue
                summary.context_ticks += 1
                for p in plans:
                    prev_states[p.baseline_name] = p.new_state
                    tally = summary.per_baseline[p.baseline_name]
                    if p.action == WRITE:
                        tally.write += 1
                    elif p.action == SKIP_EXISTS:
                        tally.skip_exists += 1
                    elif p.action == SKIP_NO_CONFIG:
                        tally.skip_no_config += 1
                    if p.equity_usd is not None:
                        if tally.first_equity is None:
                            tally.first_equity = p.equity_usd
                        tally.last_equity = p.equity_usd
                        tally.last_pnl = p.pnl_usd_cumulative

            if apply:
                await session.commit()
            else:
                await session.rollback()

            for name, t in summary.per_baseline.items():
                logger.info(
                    "baseline_backfill_result",
                    baseline=name,
                    written=t.write,
                    skipped_existing=t.skip_exists,
                    skipped_no_config=t.skip_no_config,
                    first_equity=str(t.first_equity) if t.first_equity is not None else None,
                    last_equity=str(t.last_equity) if t.last_equity is not None else None,
                    final_pnl=str(t.last_pnl) if t.last_pnl is not None else None,
                )
            logger.info(
                "baseline_backfill_done" if apply else "baseline_backfill_dry_run",
                experiment_id=exp_id,
                context_ticks=summary.context_ticks,
                malformed_snapshots=summary.malformed_snapshots,
                note=None if apply else "no writes — pass --apply to commit",
            )
            return summary
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill baseline_equity_snapshots (ADR-0036).")
    parser.add_argument(
        "--apply", action="store_true", help="commit (default: dry-run, writes nothing)"
    )
    parser.add_argument("--experiment-id", default=os.environ.get("AIAT_EXPERIMENT_ID"))
    parser.add_argument("--database-url", default=os.environ.get("AIAT_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("AIAT_DATABASE_URL (or --database-url) is required")
    if not args.apply:
        logger.info("baseline_backfill_dry_run_start", note="dry-run — pass --apply to commit")
    asyncio.run(backfill(args.database_url, args.experiment_id, args.apply))


if __name__ == "__main__":
    main()
