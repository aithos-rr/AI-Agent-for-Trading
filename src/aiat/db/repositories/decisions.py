"""Repository for decisions + decision_actions + cost_events + llm_invocations (§7.6)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.action import DecisionAction
from aiat.db.models.cost_event import CostEvent
from aiat.db.models.decision import Decision
from aiat.db.models.llm_invocation import LLMInvocation
from aiat.domain.enums import ExecutionStatus
from aiat.domain.schemas import ActionDecision as ActionDecisionSchema
from aiat.domain.schemas import GuardrailReport, LLMInvocationResult

# decision_actions.execution_error is unbounded TEXT in Postgres, but a raw exchange-rejection
# message can embed a full order response; cap defensively before storing (ADR-0024).
_EXECUTION_ERROR_MAXLEN = 1000


class DecisionsRepository:
    """Atomic persist for the decision bounded context (§7.6, invariant #4).

    decisions + decision_actions + cost_events + llm_invocations in ONE transaction.
    No internal commit — caller owns the Unit of Work (AsyncSession).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def persist_decision(
        self,
        *,
        run_id: str,
        experiment_id: str,
        model_id: str,
        invocation: LLMInvocationResult,
        post_guardrail_actions: list[ActionDecisionSchema],
        guardrail_reports: list[GuardrailReport],
    ) -> str:
        """Persist decisions + actions + cost_events + llm_invocations atomically.

        Args:
            run_id: UUID string of the run that triggered this decision.
            experiment_id: UUID string of the current experiment.
            model_id: Model identifier string.
            invocation: Raw LLMInvocationResult from LLMClient.invoke().
            post_guardrail_actions: Post-guardrail ActionDecision per symbol (3 items).
            guardrail_reports: GuardrailReport per symbol (3 items).

        Returns:
            decision_id (str UUID) of the newly persisted Decision.

        Raises:
            IntegrityError: on FK or CHECK violation → full rollback.
        """
        now = datetime.now(UTC)
        run_uuid = uuid.UUID(run_id)
        experiment_uuid = uuid.UUID(experiment_id)
        decision_uuid = uuid.uuid4()

        # Step 1: INSERT decisions
        decision = Decision(
            id=decision_uuid,
            run_id=run_uuid,
            experiment_id=experiment_uuid,
            model_id=model_id,
            decided_at=now,
            raw_response_id=invocation.raw_response_id,
            portfolio_reasoning=invocation.decision.portfolio_reasoning,
            risk_assessment=invocation.decision.risk_assessment,
            portfolio_confidence=invocation.decision.portfolio_confidence,
            latency_ms=invocation.latency_ms,
            fallback_used=invocation.fallback_used,
            raw_payload=invocation.raw_payload,
        )
        self._session.add(decision)
        await self._session.flush()  # materialize decision.id before FK use

        # Build symbol-keyed lookups for O(1) access
        original_by_symbol = {a.symbol: a for a in invocation.decision.actions}
        report_by_symbol = {r.symbol: r for r in guardrail_reports}

        # Step 2: INSERT decision_actions (3 rows: BTC/ETH/SOL)
        for final_action in post_guardrail_actions:
            symbol = final_action.symbol
            original = original_by_symbol[symbol]
            report = report_by_symbol[symbol]

            action = DecisionAction(
                id=uuid.uuid4(),
                decision_id=decision_uuid,
                experiment_id=experiment_uuid,
                model_id=model_id,
                run_id=run_uuid,
                symbol=symbol,
                # action-level outputs (preserved even when guardrail forces HOLD)
                confidence=final_action.confidence,
                time_horizon_min=final_action.time_horizon_min,
                action_reasoning=final_action.action_reasoning,
                action_key_signals=list(final_action.action_key_signals),
                # requested values (pre-guardrail, from original LLM output)
                side_requested=original.side.value,
                leverage_requested=original.leverage,
                size_pct_requested=original.size_pct,
                stop_loss_pct=original.stop_loss_pct,
                take_profit_pct=original.take_profit_pct,
                entry_type=original.entry_type.value,
                limit_price=original.limit_price,
                # executed values (post-guardrail)
                side_executed=final_action.side.value,
                leverage_executed=final_action.leverage,
                size_pct_executed=final_action.size_pct,
                # guardrail flags
                leverage_clamped=report.leverage_clamped,
                size_pct_clamped=report.size_pct_clamped,
                forced_hold=report.forced_hold,
                # original_side only populated when guardrail forced a side change
                original_side=report.original_side.value if report.forced_hold else None,
            )
            self._session.add(action)

        await self._session.flush()  # flush all 3 actions

        # Step 3: INSERT cost_events AFTER decisions row (invariant #4)
        cost = invocation.cost
        # Convert Decimal values to str for JSON-serializable pricing_snapshot
        pricing_json: dict[str, str] = {k: str(v) for k, v in cost.pricing_snapshot.items()}

        cost_event = CostEvent(
            id=uuid.uuid4(),
            decision_id=decision_uuid,
            experiment_id=experiment_uuid,
            model_id=model_id,
            run_id=run_uuid,
            input_tokens=cost.input_tokens,
            output_tokens=cost.output_tokens,
            reasoning_tokens=cost.reasoning_tokens,
            n_attempts=cost.n_attempts,
            cost_usd=cost.cost_usd,
            pricing_snapshot=pricing_json,
        )
        self._session.add(cost_event)

        # Step 4: INSERT llm_invocations (nuisance snapshot per run)
        llm_inv = LLMInvocation(
            id=uuid.uuid4(),
            run_id=run_uuid,
            model_id=model_id,
            provider_snapshot=invocation.provider_snapshot,
            model_name_api_snapshot=invocation.model_name_api_snapshot,
            temperature=invocation.temperature,
            top_p=invocation.top_p,
            max_tokens=invocation.max_tokens,
            seed=invocation.seed,
            llm_config_snapshot={
                "provider": invocation.provider_snapshot,
                "model_name_api": invocation.model_name_api_snapshot,
                "temperature": (
                    str(invocation.temperature) if invocation.temperature is not None else None
                ),
                "top_p": (str(invocation.top_p) if invocation.top_p is not None else None),
                "max_tokens": invocation.max_tokens,
                "seed": invocation.seed,
                "fallback_used": invocation.fallback_used,
            },
        )
        self._session.add(llm_inv)
        await self._session.flush()

        return str(decision_uuid)

    async def mark_action_execution(
        self,
        action_id: str,
        *,
        status: ExecutionStatus,
        executed: bool,
        error: str | None = None,
    ) -> None:
        """Record a decision_action's execution outcome (ADR-0024).

        Sets execution_status/executed (and execution_error on failure) after the decision
        loop attempts the action on the exchange. The decision bounded context (§7.6) owns
        decision_actions. No internal commit — caller owns the Unit of Work.

        Args:
            action_id: UUID string of the DecisionAction to update.
            status: Terminal ExecutionStatus for the action.
            executed: True iff an order actually moved size on the exchange (filled/partial).
            error: Optional rejection/timeout message (truncated before storage).

        Raises:
            ValueError: if action_id does not exist.
        """
        action = await self._session.get(DecisionAction, uuid.UUID(action_id))
        if action is None:
            raise ValueError(f"DecisionAction {action_id!r} not found")
        action.execution_status = status.value
        action.executed = executed
        if error is not None:
            action.execution_error = error[:_EXECUTION_ERROR_MAXLEN]
        await self._session.flush()

    async def get_by_run(self, run_id: str) -> Decision | None:
        """Return the Decision for a given run_id, or None if not found."""
        result = await self._session.execute(
            select(Decision).where(Decision.run_id == uuid.UUID(run_id))
        )
        return result.scalar_one_or_none()

    async def get_action_history(
        self,
        model_id: str,
        symbol: str,
        since: str,
    ) -> list[DecisionAction]:
        """Return DecisionActions for model/symbol created at or after since (ISO timestamp).

        Args:
            model_id: Model identifier to filter by.
            symbol: Trading symbol to filter by (BTC/ETH/SOL).
            since: ISO 8601 timestamp; actions at or after this time are returned.

        Returns:
            List of DecisionAction ordered by created_at descending (most recent first).
        """
        since_dt = datetime.fromisoformat(since)
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=UTC)

        result = await self._session.execute(
            select(DecisionAction)
            .where(
                DecisionAction.model_id == model_id,
                DecisionAction.symbol == symbol,
                DecisionAction.created_at >= since_dt,
            )
            .order_by(DecisionAction.created_at.desc())
        )
        return list(result.scalars().all())
