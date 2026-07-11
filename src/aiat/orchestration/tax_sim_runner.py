"""Tax-simulation runner (ADR-0033, PRD §4.3).

Wires the existing ``TaxSimulationRepository.compute_and_persist_period`` (which had no
production caller — like the baselines, it was reachable only from tests) into a scheduled
orchestrator job. Each run computes the Italian tax simulation for the most recently *closed*
period: it aggregates every model's ``outcomes`` in that window and persists one
``tax_sim_periods`` row per model.

Design (ADR-0033):
  - **Rate override, not schema change**: the writer receives ``tax_rate_pct`` explicitly
    (0.33 for the leveraged-crypto Italian regime), so the ``tax_sim_periods.tax_rate_pct``
    server_default (0.26) is never relied upon — no migration.
  - **Period configurable**: ``daily`` (M6.2 smoke, fast feedback) or ``quarter`` (experiment).
  - **Idempotent** on the UNIQUE ``(experiment_id, model_id, quarter_label)``: check-then-skip,
    so running the daily job in quarter mode simply recomputes the last closed quarter and skips.
  - **Bucketing**: outcomes are bucketed by ``Outcome.created_at`` (its indexed time column).

Limitation (M6.2): a single run computes only the single most-recently-closed period. A missed
run does not backfill earlier periods — acceptable because tax sim is a recomputable post-hoc
aggregation (a backfill loop can be added later if needed).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

import structlog
from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiat.db.models.outcome import Outcome
from aiat.db.models.tax_sim import TaxSimPeriod
from aiat.db.repositories.tax_simulation import TaxSimulationRepository

logger = structlog.get_logger(__name__)

TaxPeriodMode = Literal["daily", "quarter"]


@dataclass(frozen=True)
class TaxSimRunResult:
    """Outcome of one tax-sim run (for logging, not persisted)."""

    period_label: str
    created: int
    skipped: int
    failed: int = 0  # models whose per-model transaction raised (isolated, logged)


def compute_closed_period(now: datetime, mode: TaxPeriodMode) -> tuple[str, datetime, datetime]:
    """Return ``(label, start, end)`` for the most recently *closed* period before ``now``.

    ``end`` is exclusive. ``daily`` → the previous full UTC day; ``quarter`` → the previous
    full calendar quarter (Jan→prev-year Q4). Labels: ``"YYYY-MM-DD"`` / ``"Q<n>-YYYY"``.
    """
    now = now.astimezone(UTC)
    if mode == "daily":
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(days=1)
        return start.strftime("%Y-%m-%d"), start, today

    # quarter: q_index 0..3 for the quarter containing ``now``
    q_index = (now.month - 1) // 3
    cur_q_start = now.replace(
        month=q_index * 3 + 1, day=1, hour=0, minute=0, second=0, microsecond=0
    )
    if q_index == 0:
        prev_year = now.year - 1
        start = cur_q_start.replace(year=prev_year, month=10)
        label = f"Q4-{prev_year}"
    else:
        start = cur_q_start.replace(month=(q_index - 1) * 3 + 1)
        label = f"Q{q_index}-{now.year}"
    return label, start, cur_q_start


class TaxSimRunner:
    """Computes and persists ``tax_sim_periods`` for the last closed period (ADR-0033)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        experiment_id: str,
        tax_rate_pct: Decimal,
        period_mode: TaxPeriodMode,
    ) -> None:
        self._session_factory = session_factory
        self._experiment_id = experiment_id
        self._tax_rate_pct = tax_rate_pct
        self._period_mode = period_mode

    async def run(self, now: datetime) -> TaxSimRunResult:
        """Compute the closed period and persist one tax_sim row per participating model.

        Args:
            now: Current time (injected so the closed-period computation is deterministic in
                tests; the scheduled job passes the wall-clock value).
        """
        label, start, end = compute_closed_period(now, self._period_mode)
        created = skipped = failed = 0

        async with self._session_factory() as session:
            model_ids = await self._participating_models(session)

        # Per-model transaction (own session + commit) so one model's failure cannot roll back
        # the others' committed rows (finding: a single mid-loop exception was losing the whole
        # batch). Idempotent via the UNIQUE (exp, model, quarter_label) check-then-skip, so a
        # retry resumes from the models not yet committed rather than redoing everything.
        for model_id in model_ids:
            try:
                async with self._session_factory() as session:
                    repo = TaxSimulationRepository(session)
                    if await self._period_exists(session, model_id, label):
                        skipped += 1
                        continue
                    outcomes = await self._outcomes_in_period(session, model_id, start, end)
                    await repo.compute_and_persist_period(
                        experiment_id=self._experiment_id,
                        model_id=model_id,
                        quarter_label=label,
                        period_start=start.isoformat(),
                        period_end=end.isoformat(),
                        outcomes_in_period=outcomes,
                        tax_rate_pct=self._tax_rate_pct,
                    )
                    await session.commit()
                    created += 1
            except Exception:  # noqa: BLE001 — one model must not abort the batch
                failed += 1
                logger.exception("tax_sim_model_failed", model_id=model_id, period=label)

        logger.info(
            "tax_sim_run_done", period=label, created=created, skipped=skipped, failed=failed
        )
        return TaxSimRunResult(period_label=label, created=created, skipped=skipped, failed=failed)

    async def _participating_models(self, session: AsyncSession) -> list[str]:
        exp_id = uuid.UUID(self._experiment_id)
        result = await session.execute(
            select(distinct(Outcome.model_id)).where(Outcome.experiment_id == exp_id)
        )
        return [row[0] for row in result.all()]

    async def _period_exists(self, session: AsyncSession, model_id: str, label: str) -> bool:
        existing = await session.scalar(
            select(TaxSimPeriod.id).where(
                TaxSimPeriod.experiment_id == uuid.UUID(self._experiment_id),
                TaxSimPeriod.model_id == model_id,
                TaxSimPeriod.quarter_label == label,
            )
        )
        return existing is not None

    async def _outcomes_in_period(
        self, session: AsyncSession, model_id: str, start: datetime, end: datetime
    ) -> list[Outcome]:
        result = await session.execute(
            select(Outcome).where(
                Outcome.experiment_id == uuid.UUID(self._experiment_id),
                Outcome.model_id == model_id,
                Outcome.created_at >= start,
                Outcome.created_at < end,
            )
        )
        return list(result.scalars().all())
