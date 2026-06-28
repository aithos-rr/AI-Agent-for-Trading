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
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.repositories.decisions import DecisionsRepository
from aiat.db.repositories.positions import PositionsRepository
from aiat.db.repositories.runs import RunsRepository
from aiat.db.repositories.snapshots import SnapshotsRepository
from aiat.domain.enums import CloseReason, OrderKind, RunStatus, Side
from aiat.domain.schemas import ContextBundle, PortfolioState, TradeDecision
from aiat.execution.guardrails import Guardrails
from aiat.execution.hyperliquid_client import HyperliquidClient, PositionClosureInfo
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

            # PRD §4.1 step [5]: invoke LLM
            invocation = await self._llm_client.invoke(rendered_text, timeout_seconds=90)

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

            # PRD §4.1 step [8]: execute actions on Hyperliquid + persist positions
            await self._execute_actions(session, run_id, post_decision, portfolio_state)
            await session.commit()

            # PRD §4.1 step [9]: check pending closures (SL/TP may have triggered)
            await self._check_pending_closures(session, run_id)
            await session.commit()

            # PRD §4.1 step [10]: mark run success
            await runs_repo.update_status(run_id, RunStatus.SUCCESS)
            await session.commit()

            logger.info(
                "decision_loop_success",
                tick_id=tick_id,
                run_id=run_id,
                decision_id=decision_id,
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
    ) -> None:
        """Execute each non-HOLD action on Hyperliquid and persist positions.

        Sends orders to HL for LONG/SHORT/FLAT actions, then persists the resulting
        positions and orders via PositionsRepository.
        """
        # Build per-symbol current position summary from portfolio state
        open_summary_by_symbol = {p.symbol: p for p in portfolio_state.open_positions}
        positions_repo = PositionsRepository(session)

        # Fetch persisted DecisionAction IDs for this run (created in persist_decision)
        result = await session.execute(
            select(DecisionAction).where(DecisionAction.run_id == uuid.UUID(run_id))
        )
        db_actions_by_symbol = {a.symbol: a for a in result.scalars().all()}

        for action in post_decision.actions:
            if action.side == Side.HOLD:
                continue

            current_pos_summary = open_summary_by_symbol.get(action.symbol)
            order_results = await self._hl_client.execute_action(
                action, run_id, current_pos_summary
            )
            if not order_results:
                continue

            db_action = db_actions_by_symbol.get(action.symbol)
            if db_action is None:
                logger.warning(
                    "db_action_not_found_for_execution",
                    run_id=run_id,
                    symbol=action.symbol,
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
                        await positions_repo.close_position(str(open_pos.id), closure_info, run_id)

            if has_entry:
                entry_orders = [o for o in order_results if o.order_kind != OrderKind.CLOSE]
                await positions_repo.open_position(str(db_action.id), entry_orders, run_id)

    async def _check_pending_closures(
        self,
        session: AsyncSession,
        run_id: str,
    ) -> None:
        """Check if SL/TP triggered on any open positions since last tick (PRD §4.1 step 9)."""
        positions_repo = PositionsRepository(session)
        open_positions = await positions_repo.list_open_for_model(self._settings.model_id)

        for position in open_positions:
            # Position identity = coin symbol (ADR-0016). Hyperliquid exposes no stable
            # position id, and v2 holds at most one position per symbol per wallet, so the
            # symbol uniquely identifies the position. The positions.hl_position_id column
            # is vestigial (never populated) — keying closure detection off it skipped this
            # loop entirely (always None). Use the symbol, matching RealHyperliquidClient.
            closure_info = await self._hl_client.check_position_closure(position.symbol)
            if closure_info is not None:
                await positions_repo.close_position(str(position.id), closure_info, run_id)

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
