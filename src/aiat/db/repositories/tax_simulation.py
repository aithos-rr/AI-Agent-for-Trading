"""Repository for tax_sim_periods (§7.6, §4.3) — quarterly tax simulation."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.outcome import Outcome
from aiat.db.models.tax_sim import TaxSimPeriod


class TaxSimulationRepository:
    """Bounded context: tax_sim_periods aggregated per (model_id × quarter) (§7.6).

    Italian tax rule (§4.3): algebraic sum of net PnL across all closed outcomes
    → taxable_base = MAX(0, sum); tax_due = taxable_base × 0.26.

    No internal commit — caller owns the Unit of Work (AsyncSession).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def compute_and_persist_period(
        self,
        experiment_id: str,
        model_id: str,
        quarter_label: str,
        period_start: str,
        period_end: str,
        outcomes_in_period: list[Outcome],
        tax_rate_pct: Decimal = Decimal("0.26"),
    ) -> str:
        """Aggregate outcomes and persist one tax_sim_periods row.

        Applies Italian algebraic compensation (§4.3): taxable_base = max(0, net)
        where net = total_pnl_gross - total_fees - total_funding.

        Args:
            experiment_id: UUID string of the experiment.
            model_id: Model identifier (FK to models.id).
            quarter_label: Human-readable quarter label (e.g. 'Q1-2026').
            period_start: ISO 8601 start of the quarter.
            period_end: ISO 8601 end of the quarter (must be > period_start).
            outcomes_in_period: Closed Outcome objects for the quarter (may be empty).
            tax_rate_pct: Tax rate as a fraction (default 0.26 = 26%).

        Returns:
            tax_sim_period_id (str UUID) of the newly persisted row.

        Raises:
            IntegrityError: on UNIQUE (experiment_id, model_id, quarter_label) violation.
        """
        zero = Decimal("0")
        total_pnl_gross = sum((o.realized_pnl_gross_usd for o in outcomes_in_period), zero)
        total_fees = sum((o.sum_fees_usd for o in outcomes_in_period), zero)
        total_funding = sum((o.sum_funding_usd for o in outcomes_in_period), zero)

        # §4.3: algebraic compensation — losses offset profits; floor at 0
        net = total_pnl_gross - total_fees - total_funding
        taxable_base = max(zero, net)
        tax_due = taxable_base * tax_rate_pct

        start_dt = datetime.fromisoformat(period_start)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=UTC)

        end_dt = datetime.fromisoformat(period_end)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=UTC)

        period = TaxSimPeriod(
            id=uuid.uuid4(),
            experiment_id=uuid.UUID(experiment_id),
            model_id=model_id,
            quarter_label=quarter_label,
            period_start=start_dt,
            period_end=end_dt,
            total_pnl_gross_usd=total_pnl_gross,
            total_fees_usd=total_fees,
            total_funding_usd=total_funding,
            taxable_base_usd=taxable_base,
            tax_rate_pct=tax_rate_pct,
            tax_due_usd=tax_due,
            n_positions_closed=len(outcomes_in_period),
            computed_at=datetime.now(UTC),
        )
        self._session.add(period)
        await self._session.flush()
        return str(period.id)

    async def list_for_model(
        self,
        model_id: str,
    ) -> list[TaxSimPeriod]:
        """Return tax_sim_periods for a model ordered by period_start ascending.

        Args:
            model_id: Model identifier to filter by.

        Returns:
            List of TaxSimPeriod ordered by period_start ascending.
        """
        result = await self._session.execute(
            select(TaxSimPeriod)
            .where(TaxSimPeriod.model_id == model_id)
            .order_by(TaxSimPeriod.period_start.asc())
        )
        return list(result.scalars().all())
