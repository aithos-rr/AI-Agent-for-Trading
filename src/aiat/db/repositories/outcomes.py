"""Repository for outcomes (§7.6) — scientific analysis of closed positions."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.outcome import Outcome


class OutcomesRepository:
    """Read/write for the `outcomes` table (§7.6).

    persist_outcome is the canonical write path for standalone outcome creation
    (e.g. HOLD/FLAT synthetic outcomes, tax-sim updates).  For real traded
    positions the atomic path is PositionsRepository.close_position.

    No internal commit — caller owns the Unit of Work (AsyncSession).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def persist_outcome(
        self,
        *,
        position_id: str,
        opening_action_id: str,
        opening_run_id: str,
        closing_run_id: str,
        experiment_id: str,
        model_id: str,
        symbol: str,
        realized_pnl_gross_usd: Decimal,
        sum_fees_usd: Decimal,
        sum_funding_usd: Decimal,
        pnl_net_fee_usd: Decimal,
        pnl_net_fee_funding_usd: Decimal,
        pnl_net_fee_funding_tax_sim_usd: Decimal,
        was_profitable_net: bool,
        holding_duration_min: int,
        decision_action_confidence: Decimal,
        decision_action_time_horizon_min: int,
        horizon_met: bool,
    ) -> str:
        """Insert an Outcome row in the caller's transaction.

        Args:
            position_id: UUID string of the closed position.
            opening_action_id: UUID string of the DecisionAction that opened the position.
            opening_run_id: UUID string of the run that made the opening decision.
            closing_run_id: UUID string of the run that made the closing decision.
            experiment_id: UUID string of the current experiment.
            model_id: Model identifier string.
            symbol: Trading symbol (BTC/ETH/SOL).
            realized_pnl_gross_usd: Gross PnL before fees/funding (Decimal).
            sum_fees_usd: Sum of all fee_events for this position (≥ 0).
            sum_funding_usd: Sum of all funding_events for this position.
            pnl_net_fee_usd: realized_pnl_gross_usd - sum_fees_usd.
            pnl_net_fee_funding_usd: pnl_net_fee_usd - sum_funding_usd.
            pnl_net_fee_funding_tax_sim_usd: Tax-sim adjusted PnL (0 until computed).
            was_profitable_net: True iff pnl_net_fee_funding_usd > 0.
            holding_duration_min: Minutes position was held (≥ 0).
            decision_action_confidence: Confidence from the opening DecisionAction ∈ [0,1].
            decision_action_time_horizon_min: Time horizon from the opening action (> 0).
            horizon_met: True iff holding_duration_min ≤ decision_action_time_horizon_min.

        Returns:
            outcome_id (str UUID) of the newly persisted Outcome.

        Raises:
            IntegrityError: on FK or CHECK violation (position_id UNIQUE, confidence range,
                sum_fees_usd ≥ 0, holding_duration_min ≥ 0, time_horizon_min > 0).
        """
        outcome = Outcome(
            id=uuid.uuid4(),
            position_id=uuid.UUID(position_id),
            opening_action_id=uuid.UUID(opening_action_id),
            opening_run_id=uuid.UUID(opening_run_id),
            closing_run_id=uuid.UUID(closing_run_id),
            experiment_id=uuid.UUID(experiment_id),
            model_id=model_id,
            symbol=symbol,
            realized_pnl_gross_usd=realized_pnl_gross_usd,
            sum_fees_usd=sum_fees_usd,
            sum_funding_usd=sum_funding_usd,
            pnl_net_fee_usd=pnl_net_fee_usd,
            pnl_net_fee_funding_usd=pnl_net_fee_funding_usd,
            pnl_net_fee_funding_tax_sim_usd=pnl_net_fee_funding_tax_sim_usd,
            was_profitable_net=was_profitable_net,
            holding_duration_min=holding_duration_min,
            decision_action_confidence=decision_action_confidence,
            decision_action_time_horizon_min=decision_action_time_horizon_min,
            horizon_met=horizon_met,
        )
        self._session.add(outcome)
        await self._session.flush()
        return str(outcome.id)

    async def list_for_model_in_window(
        self,
        model_id: str,
        start: str,
        end: str,
    ) -> list[Outcome]:
        """Return Outcomes for a model whose created_at falls in [start, end].

        Args:
            model_id: Model identifier to filter by.
            start: ISO 8601 timestamp; outcomes created at or after this time are included.
            end: ISO 8601 timestamp; outcomes created before or at this time are included.

        Returns:
            List of Outcome ordered by created_at ascending.
        """
        start_dt = datetime.fromisoformat(start)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=UTC)

        end_dt = datetime.fromisoformat(end)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=UTC)

        result = await self._session.execute(
            select(Outcome)
            .where(
                Outcome.model_id == model_id,
                Outcome.created_at >= start_dt,
                Outcome.created_at <= end_dt,
            )
            .order_by(Outcome.created_at.asc())
        )
        return list(result.scalars().all())
