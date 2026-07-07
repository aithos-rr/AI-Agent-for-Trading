"""Decision loop — single-agent tick execution (PRD §4.1)."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiat.config.settings import AgentSettings
from aiat.db.models.action import DecisionAction
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.position import Position
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.repositories.decisions import DecisionsRepository
from aiat.db.repositories.positions import PositionsRepository
from aiat.db.repositories.runs import RunsRepository
from aiat.db.repositories.snapshots import SnapshotsRepository
from aiat.domain.enums import CloseReason, ExecutionStatus, OrderKind, RunStatus, Side
from aiat.domain.exceptions import ExecutionRejectedError, ExecutionTimeoutError
from aiat.domain.schemas import ContextBundle, PortfolioState, TradeDecision
from aiat.execution.guardrails import Guardrails
from aiat.execution.hyperliquid_client import HyperliquidClient, OrderResult, PositionClosureInfo
from aiat.llm.base import BaseLLMClient

logger = structlog.get_logger(__name__)

_CONTEXT_RETRY_COUNT = 3
_CONTEXT_RETRY_DELAY_S = 5.0


def _render_prompt(
    template_text: str,
    context_bundle: ContextBundle,
    portfolio_state: PortfolioState,
    confidence_def: str,
) -> tuple[str, str]:
    """Render prompt from template + market context + portfolio state.

    Returns:
        Tuple of (rendered_text, sha256_hash) where hash is the rendered_prompt_hash
        stored in runs.rendered_prompt_hash (inv #7: deterministic cross-run audit).
    """
    rendered = "\n\n".join(
        [
            template_text,
            "## MARKET CONTEXT\n" + context_bundle.model_dump_json(indent=2),
            "## PORTFOLIO STATE\n" + portfolio_state.model_dump_json(indent=2),
            "## CONFIDENCE DEFINITION\n" + confidence_def,
        ]
    )
    prompt_hash = hashlib.sha256(rendered.encode()).hexdigest()
    return rendered, prompt_hash


# OrderResult.status → DecisionAction.execution_status (ADR-0024). `executed` is True only
# when the primary order actually moved size on the exchange (filled or partial).
_ORDER_STATUS_TO_EXECUTION: dict[str, ExecutionStatus] = {
    "filled": ExecutionStatus.FILLED,
    "partial": ExecutionStatus.PARTIAL,
    "rejected": ExecutionStatus.FAILED,
    "cancelled": ExecutionStatus.CANCELLED,
    "pending": ExecutionStatus.PENDING,
}


def _action_execution_outcome(order_results: list[OrderResult]) -> tuple[ExecutionStatus, bool]:
    """Derive (execution_status, executed) for a DecisionAction from its executed orders.

    The outcome tracks the *primary* order — the ENTRY when opening (an opposite-side flip
    also emits a CLOSE, but the action's intent is the new entry), otherwise the CLOSE for a
    pure FLAT. Protective SL/TP triggers are not primary. Returns NOT_APPLICABLE when no
    primary order is present (ADR-0024).
    """
    primary = next((o for o in order_results if o.order_kind == OrderKind.ENTRY), None) or next(
        (o for o in order_results if o.order_kind == OrderKind.CLOSE), None
    )
    if primary is None:
        return ExecutionStatus.NOT_APPLICABLE, False
    status = _ORDER_STATUS_TO_EXECUTION.get(primary.status)
    if status is None:
        # 'triggered' (or any unmapped value) is never expected for a primary entry/close.
        logger.warning("unexpected_primary_order_status", status=primary.status)
        return ExecutionStatus.NOT_APPLICABLE, False
    return status, status in (ExecutionStatus.FILLED, ExecutionStatus.PARTIAL)


def _attribute_close_reason(
    position: Position,
    closure: PositionClosureInfo,
    run_id: str,
) -> CloseReason:
    """Attribute an autonomous closure to stop_loss / take_profit / liquidated (ADR-0030).

    ``check_position_closure`` can only flag LIQUIDATED (from the fill) vs its default
    MODEL_CLOSE; it has no trigger-oid match, so it cannot say stop_loss vs take_profit.
    This resolves it with a PER-SIDE heuristic:

    - **liquidation wins**: if the closure is flagged a liquidation, return LIQUIDATED and
      do NOT apply the SL/TP heuristic (a liquidation fills on the loss side of entry and
      would otherwise be misread as a stop-loss).
    - otherwise use the side of ``exit_price`` relative to ``entry_price``. For a LONG the
      stop-loss sits strictly below entry and the take-profit strictly above (SHORT
      inverted — see ``compute_position_sizing`` / ``PositionsRepository.open_position``),
      and ``entry_price`` is strictly between the two triggers (``stop_loss_pct`` /
      ``take_profit_pct`` are ``> 0``), so the side of the exit identifies which fired.
      Robust to gaps/slippage: it keys off the side of entry, not proximity to a stored
      trigger price.

    LIMITS (deferred to the audit-complete session + ADR-0025): this is NOT an oid match
    against the actual trigger order. It assumes every autonomous closure is an SL/TP
    trigger or a liquidation — no external/manual close — which holds for the experiment.
    An exit exactly at entry is structurally impossible for a real trigger; it resolves to
    stop_loss and is logged as an anomaly rather than failing the tick.
    """
    if closure.close_reason == CloseReason.LIQUIDATED:
        return CloseReason.LIQUIDATED

    entry_price = position.entry_price
    exit_price = closure.exit_price
    if exit_price == entry_price:
        logger.warning(
            "autonomous_close_exit_equals_entry",
            run_id=run_id,
            symbol=position.symbol,
            position_id=str(position.id),
            side=position.side,
            exit_price=str(exit_price),
            entry_price=str(entry_price),
        )
        return CloseReason.STOP_LOSS

    if position.side == "LONG":
        return CloseReason.STOP_LOSS if exit_price < entry_price else CloseReason.TAKE_PROFIT
    return CloseReason.STOP_LOSS if exit_price > entry_price else CloseReason.TAKE_PROFIT


class DecisionLoop:
    """Single-agent decision loop (PRD §4.1).

    One instance per agent process. Invoked once per 15-minute tick by APScheduler.
    Composes LLM client, Hyperliquid client, guardrails, and all repositories.
    """

    def __init__(
        self,
        settings: AgentSettings,
        llm_client: BaseLLMClient,
        hl_client: HyperliquidClient,
        session_factory: async_sessionmaker[AsyncSession],
        guardrails: Guardrails | None = None,
    ) -> None:
        self._settings = settings
        self._llm_client = llm_client
        self._hl_client = hl_client
        self._session_factory = session_factory
        self._guardrails = guardrails if guardrails is not None else Guardrails()

    async def run_once(
        self,
        tick_id: str,
        scheduled_for: datetime,
    ) -> str | None:
        """Execute one 15-minute tick.

        Returns:
            run_id (str UUID) on success/partial, None if context_snapshot was not
            available after retries (status='missed' — no run row created).

        Raises:
            Any unexpected exception after logging + status update to 'failed'.
        """
        # Mutable holder so _execute_tick can write run_id before invoking LLM
        # and the timeout/error handler can read it even if _execute_tick never returns.
        run_id_holder: list[str | None] = [None]

        async def _inner() -> str | None:
            return await self._execute_tick(tick_id, scheduled_for, run_id_holder)

        try:
            return await asyncio.wait_for(
                _inner(),
                timeout=float(self._settings.hard_timeout_seconds),
            )
        except TimeoutError:
            run_id = run_id_holder[0]
            logger.error(
                "decision_loop_timeout",
                tick_id=tick_id,
                run_id=run_id,
                timeout_seconds=self._settings.hard_timeout_seconds,
            )
            if run_id is not None:
                await self._finalize_run(run_id, RunStatus.TIMEOUT, "timeout")
            return run_id
        except Exception:
            run_id = run_id_holder[0]
            logger.exception("decision_loop_error", tick_id=tick_id, run_id=run_id)
            if run_id is not None:
                await self._finalize_run(run_id, RunStatus.FAILED, "error")
            raise

    async def _execute_tick(
        self,
        tick_id: str,
        scheduled_for: datetime,
        _run_id_holder: list[str | None],
    ) -> str | None:
        """Inner tick execution, wrapped in the 180s timeout by run_once.

        Args:
            _run_id_holder: Single-element list shared with run_once. Written
                immediately after create_run so the timeout handler can read
                the run_id even if this coroutine is cancelled mid-flight.
        """
        async with self._session_factory() as session:
            # PRD §4.1 step [3]: read context_snapshot with retry (max 3×, 5s gap)
            snapshot = await self._wait_for_context_snapshot(session, tick_id)
            if snapshot is None:
                runs_repo = RunsRepository(session)
                await runs_repo.log_error(
                    error_kind="MissedTick",
                    error_message=(
                        f"context_snapshot not available after {_CONTEXT_RETRY_COUNT} "
                        f"retries for tick_id={tick_id!r}"
                    ),
                    experiment_id=self._settings.experiment_id,
                    model_id=self._settings.model_id,
                )
                await session.commit()
                logger.warning("decision_loop_missed", tick_id=tick_id)
                return None

            context_bundle = ContextBundle.model_validate(snapshot.context_json)

            # PRD §4.1 step [2]: fetch account state
            portfolio_state = await self._hl_client.fetch_portfolio_state()

            # PRD §4.1 step [4]: load prompt template from DB + render
            template = await self._load_prompt_template(session)
            rendered_text, rendered_hash = _render_prompt(
                template.template_text,
                context_bundle,
                portfolio_state,
                template.confidence_def,
            )

            # PRD §4.1 step [1]: create run row (requires context_snapshot_id)
            runs_repo = RunsRepository(session)
            run_id = await runs_repo.create_run(
                experiment_id=self._settings.experiment_id,
                model_id=self._settings.model_id,
                tick_id=tick_id,
                scheduled_for=scheduled_for,
                prompt_template_hash=self._settings.prompt_template_hash,
                rendered_prompt_hash=rendered_hash,
                rendered_prompt_text=rendered_text,
                context_snapshot_id=str(snapshot.id),
                schema_version=self._settings.schema_version,
                git_commit_sha=self._settings.git_commit_sha,
            )
            # Expose run_id to the timeout/error handler before any I/O that can block.
            _run_id_holder[0] = run_id
            await session.commit()

            # Persist account snapshot before LLM invocation (inv #3 FK chain)
            snapshots_repo = SnapshotsRepository(session)
            await snapshots_repo.persist_account_snapshot(run_id, portfolio_state)
            await session.commit()

            # PRD §4.1 step [5]: invoke LLM. Use the configured hard timeout (aligned with
            # the outer run_once wait_for): thinking models (Opus 4.8, effort=high) can exceed
            # a fixed 90s for one structured decision (M5-T14, ADR-0023).
            invocation = await self._llm_client.invoke(
                rendered_text, timeout_seconds=self._settings.hard_timeout_seconds
            )

            # PRD §4.1 step [6]: apply guardrails
            post_decision, reports = self._guardrails.apply(
                invocation.decision,
                max_size_pct=self._settings.max_size_pct,
                hard_max_leverage=self._settings.hard_max_leverage,
                min_open_confidence=self._settings.min_open_confidence,
            )

            # PRD §4.1 step [7]: atomic persist (inv #4)
            decisions_repo = DecisionsRepository(session)
            decision_id = await decisions_repo.persist_decision(
                run_id=run_id,
                experiment_id=self._settings.experiment_id,
                model_id=self._settings.model_id,
                invocation=invocation,
                post_guardrail_actions=post_decision.actions,
                guardrail_reports=reports,
            )
            await session.commit()

            # PRD §4.1 step [8]: execute actions on Hyperliquid + persist positions.
            # Per-action isolation records each action's execution_status; the count of
            # failed actions downgrades the run to PARTIAL at step [10] (ADR-0024).
            failed_action_count = await self._execute_actions(
                session, run_id, post_decision, portfolio_state
            )
            await session.commit()

            # PRD §4.1 step [9]: check pending closures (SL/TP may have triggered)
            await self._check_pending_closures(session, run_id)
            await session.commit()

            # PRD §4.1 step [10]: finalize run. SUCCESS only if every order executed; a
            # rejected/timed-out action makes the tick PARTIAL — the loop still completed,
            # so FAILED stays reserved for pipeline-aborting exceptions (ADR-0024).
            final_status = RunStatus.PARTIAL if failed_action_count > 0 else RunStatus.SUCCESS
            await runs_repo.update_status(run_id, final_status)
            await session.commit()

            # Event name kept for log continuity; `status`/`failed_actions` now carry the
            # truth — 'success' no longer implies every order executed (ADR-0024).
            logger.info(
                "decision_loop_success",
                tick_id=tick_id,
                run_id=run_id,
                decision_id=decision_id,
                status=final_status.value,
                failed_actions=failed_action_count,
            )
            return run_id

    async def _wait_for_context_snapshot(
        self,
        session: AsyncSession,
        tick_id: str,
    ) -> ContextSnapshot | None:
        """Read context_snapshot, retrying up to 3× at 5s intervals (PRD §4.1 step 3).

        Returns:
            ContextSnapshot ORM object, or None if unavailable (missed tick).
        """
        snapshots_repo = SnapshotsRepository(session)
        for attempt in range(_CONTEXT_RETRY_COUNT + 1):
            snapshot = await snapshots_repo.get_context_snapshot(
                self._settings.experiment_id, tick_id
            )
            if snapshot is not None:
                return snapshot
            if attempt < _CONTEXT_RETRY_COUNT:
                await asyncio.sleep(_CONTEXT_RETRY_DELAY_S)
        return None

    async def _load_prompt_template(self, session: AsyncSession) -> PromptTemplate:
        """Fetch PromptTemplate from DB by hash in settings (checked at startup A5)."""
        tmpl: PromptTemplate | None = await session.get(
            PromptTemplate, self._settings.prompt_template_hash
        )
        if tmpl is None:
            raise RuntimeError(
                f"PromptTemplate {self._settings.prompt_template_hash!r} not found in DB; "
                "startup check A5 should have caught this"
            )
        return tmpl

    async def _execute_actions(
        self,
        session: AsyncSession,
        run_id: str,
        post_decision: TradeDecision,
        portfolio_state: PortfolioState,
    ) -> int:
        """Execute each non-HOLD action on Hyperliquid, persist positions, and record the
        per-action execution outcome on ``decision_actions`` (ADR-0024).

        Each action is isolated: an ``ExecutionRejectedError``/``ExecutionTimeoutError`` from
        one action marks THAT action FAILED and lets the others proceed — one rejected order
        no longer aborts the whole tick. HOLD and no-op actions are marked NOT_APPLICABLE
        instead of being left at the ``pending`` server default.

        Returns:
            The number of actions whose execution failed (>0 ⇒ the tick is PARTIAL).
        """
        # Build per-symbol current position summary from portfolio state
        open_summary_by_symbol = {p.symbol: p for p in portfolio_state.open_positions}
        positions_repo = PositionsRepository(session)
        decisions_repo = DecisionsRepository(session)

        # Fetch persisted DecisionAction IDs for this run (created in persist_decision)
        result = await session.execute(
            select(DecisionAction).where(DecisionAction.run_id == uuid.UUID(run_id))
        )
        db_actions_by_symbol = {a.symbol: a for a in result.scalars().all()}

        failed_count = 0
        for action in post_decision.actions:
            db_action = db_actions_by_symbol.get(action.symbol)
            if db_action is None:
                logger.warning(
                    "db_action_not_found_for_execution",
                    run_id=run_id,
                    symbol=action.symbol,
                )
                continue

            # HOLD never reaches the exchange (PRD §4.1): record not_applicable explicitly
            # rather than leaving the row at its 'pending' server default (ADR-0024).
            if action.side == Side.HOLD:
                await decisions_repo.mark_action_execution(
                    str(db_action.id), status=ExecutionStatus.NOT_APPLICABLE, executed=False
                )
                continue

            current_pos_summary = open_summary_by_symbol.get(action.symbol)
            try:
                order_results = await self._hl_client.execute_action(
                    action, run_id, current_pos_summary
                )
            except (ExecutionRejectedError, ExecutionTimeoutError) as exc:
                # Per-action isolation (ADR-0024): one rejection no longer aborts the tick.
                failed_count += 1
                logger.warning(
                    "action_execution_failed",
                    run_id=run_id,
                    symbol=action.symbol,
                    side=action.side.value,
                    error=str(exc),
                )
                await decisions_repo.mark_action_execution(
                    str(db_action.id),
                    status=ExecutionStatus.FAILED,
                    executed=False,
                    error=str(exc),
                )
                continue

            if not order_results:
                # FLAT with no open position, or a LONG/SHORT same-side action (no
                # add-to-position in v2): nothing reached the exchange → not_applicable.
                await decisions_repo.mark_action_execution(
                    str(db_action.id), status=ExecutionStatus.NOT_APPLICABLE, executed=False
                )
                continue

            has_close = any(o.order_kind == OrderKind.CLOSE for o in order_results)
            has_entry = any(o.order_kind == OrderKind.ENTRY for o in order_results)

            if has_close and current_pos_summary is not None:
                open_positions = await positions_repo.list_open_for_model(self._settings.model_id)
                open_pos = next((p for p in open_positions if p.symbol == action.symbol), None)
                if open_pos is not None:
                    close_order = next(o for o in order_results if o.order_kind == OrderKind.CLOSE)
                    if close_order.filled_price is not None:
                        side_multiplier = Decimal("1") if open_pos.side == "LONG" else Decimal("-1")
                        realized_pnl = (
                            (close_order.filled_price - open_pos.entry_price)
                            * open_pos.size_units
                            * side_multiplier
                        )
                        closure_info = PositionClosureInfo(
                            closed_at=datetime.now(UTC).isoformat(),
                            exit_price=close_order.filled_price,
                            close_reason=CloseReason.MODEL_CLOSE,
                            realized_pnl_usd=realized_pnl,
                        )
                        await positions_repo.close_position(
                            str(open_pos.id),
                            closure_info,
                            run_id,
                            closing_action_id=str(db_action.id),
                            close_order=close_order,
                        )

            if has_entry:
                entry_orders = [o for o in order_results if o.order_kind != OrderKind.CLOSE]
                await positions_repo.open_position(str(db_action.id), entry_orders, run_id)

            # Record the action's execution outcome only after persistence succeeded.
            status, executed = _action_execution_outcome(order_results)
            await decisions_repo.mark_action_execution(
                str(db_action.id), status=status, executed=executed
            )

        return failed_count

    async def _check_pending_closures(
        self,
        session: AsyncSession,
        run_id: str,
    ) -> None:
        """Check if SL/TP triggered on any open positions since last tick (PRD §4.1 step 9).

        Closures detected here are AUTONOMOUS: the position is still open in the DB and no
        model action closed it this tick, so the exchange closed it via an SL/TP trigger or
        a liquidation. Such closures have no model decision_action and no close OrderResult
        of ours (ADR-0030) — the reason is attributed per-side (see _attribute_close_reason)
        and persisted with closing_action_id / close_order = None.
        """
        positions_repo = PositionsRepository(session)
        open_positions = await positions_repo.list_open_for_model(self._settings.model_id)

        for position in open_positions:
            # Position identity = coin symbol (ADR-0016, confirmed by M4-T08 against the
            # real SDK). Hyperliquid exposes no stable position id, and v2 holds at most one
            # position per symbol per wallet, so the symbol uniquely identifies the position.
            closure_info = await self._hl_client.check_position_closure(position.symbol)
            if closure_info is None:
                continue
            # check_position_closure can only tell liquidated vs (default) model_close;
            # attribute stop_loss/take_profit/liquidated per-side, then persist as an
            # autonomous closure (no closing action, no close order of ours — ADR-0030).
            corrected = closure_info.model_copy(
                update={"close_reason": _attribute_close_reason(position, closure_info, run_id)}
            )
            await positions_repo.close_position(
                str(position.id),
                corrected,
                run_id,
                closing_action_id=None,
                close_order=None,
            )

    async def _finalize_run(
        self,
        run_id: str,
        status: RunStatus,
        failure_stage: str | None = None,
    ) -> None:
        """Update run status in a fresh session (used by timeout/error handlers)."""
        try:
            async with self._session_factory() as session:
                runs_repo = RunsRepository(session)
                await runs_repo.update_status(run_id, status, failure_stage)
                await session.commit()
        except Exception:
            logger.exception(
                "decision_loop_finalize_failed",
                run_id=run_id,
                target_status=status.value,
            )
