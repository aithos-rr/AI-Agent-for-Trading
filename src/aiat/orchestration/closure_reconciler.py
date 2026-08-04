"""Autonomous SL/TP closure reconciliation — T4b root-cause fix (ADR-0038).

The bookkeeping of autonomous SL/TP closures used to live INSIDE the agent tick
(``decision_loop._check_pending_closures``, PRD §4.1 step 9). That had two failure modes that
left positions as permanent "zombies" (open in DB, closed on-chain — 5 occurrences in 20 days,
ADR-0035):

  * **M1 (step order + symbol short-circuit):** it ran AFTER ``_execute_actions``, and
    ``check_position_closure`` short-circuited on ``szi != 0`` at the SYMBOL level. An SL/TP that
    fired between ticks followed by a reopen of the same symbol in the same tick left the chain
    non-flat, so the prior close was never detected.
  * **M2 (run dependence):** it only ran if the tick reached step 9. If the run failed earlier
    (e.g. ``LLMError`` when the model's API credit was exhausted), the closure check never ran and
    positions stayed zombie forever across the blackout.

This moves closure to the ORCHESTRATOR (like ``FundingReconciler`` / the baseline step): a per-tick
pass that (a) runs for every model regardless of that model's run succeeding, failing, or not
running at all (fixes M2), (b) fires at second 0 of the tick — before the agents open new positions
at second ~30 (fixes the ordering half of M1), and (c) detects PER POSITION by matching each open
position's own trigger order ``oid`` against the wallet's ``user_fills`` — no ``szi`` short-circuit,
so a same-symbol reopen (a NEW position) never masks the old position's close (fixes M1). Detection
is public (``user_fills`` by address, no private key); bookkeeping reuses ``close_position`` +
``OutcomeResolver`` (ADR-0027/0030/0032) with the unchanged per-side ``close_reason`` heuristic.

Idempotent by construction (only ``closed_at IS NULL`` positions are considered; a closed one is
never re-processed). The chain↔DB detection (T4/ADR-0025/0034) stays as a safety net and should stop
signalling ``ChainDivergence`` in normal operation once this runs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiat.db.models.model import Model
from aiat.db.models.order import Order
from aiat.db.models.position import Position
from aiat.db.models.run import Run
from aiat.db.repositories.positions import PositionsRepository
from aiat.domain.enums import CloseReason
from aiat.execution.hyperliquid_client import PositionClosureInfo, detect_autonomous_closure

logger = structlog.get_logger(__name__)

# Trigger fills can surface with latency; a small look-back buffer before the oldest open
# position's opened_at makes the query robust to clock skew (idempotency dedups any overlap).
_WINDOW_BUFFER_MS = 60_000
_HL_FILL_CAP = 2000  # user_fills_by_time returns at most ~2000 fills; warn if the window is capped
_TRIGGER_KINDS: tuple[str, ...] = ("stop_loss", "take_profit")


class FillsSource(Protocol):
    """Structural type for the read-only HL fills endpoint (HLPublicInfoClient satisfies it)."""

    async def user_fills_by_time(
        self, user: str, start_time_ms: int, end_time_ms: int | None = None
    ) -> list[dict[str, object]]: ...


@dataclass(frozen=True)
class ClosureReconcileResult:
    """Outcome of one closure pass (for logging/observability, not persisted)."""

    closed: int
    still_open: int
    models: int
    model_errors: int
    no_run: int = 0  # positions we could not close because the model had no run to attribute


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _attribute_close_reason(
    position: Position,
    closure: PositionClosureInfo,
    run_id: str,
) -> CloseReason:
    """Attribute an autonomous closure to stop_loss / take_profit / liquidated (ADR-0030).

    ``detect_autonomous_closure`` only flags LIQUIDATED (from the fill) vs its default
    MODEL_CLOSE. This resolves it with a PER-SIDE heuristic (unchanged from the original
    decision_loop implementation — ADR-0030):

    - **liquidation wins**: if the closure is flagged a liquidation, return LIQUIDATED and do NOT
      apply the SL/TP heuristic (a liquidation fills on the loss side of entry and would otherwise
      be misread as a stop-loss).
    - otherwise use the side of ``exit_price`` relative to ``entry_price``. For a LONG the
      stop-loss sits strictly below entry and the take-profit strictly above (SHORT inverted), and
      ``entry_price`` is strictly between the two triggers, so the side of the exit identifies which
      fired. Robust to gaps/slippage: it keys off the side of entry, not proximity to a stored
      trigger price. An exit exactly at entry is structurally impossible for a real trigger; it
      resolves to stop_loss and is logged as an anomaly rather than failing the pass.
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


class ClosureReconciler:
    """Books autonomous SL/TP closures for every open position, per tick (ADR-0038)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        fills_source: FillsSource,
        experiment_id: str,
    ) -> None:
        self._session_factory = session_factory
        self._source = fills_source
        self._experiment_id = experiment_id

    async def reconcile(self, now_ms: int) -> ClosureReconcileResult:
        """Detect + book every autonomous closure across all models' open positions.

        Args:
            now_ms: current time in unix ms (injected so the pass is deterministic in tests).
        """
        async with self._session_factory() as session:
            plan = await self._models_with_open_positions(session)

        closed = still_open = models = model_errors = no_run = 0
        for model_id, (wallet, start_ms) in plan.items():
            models += 1
            try:
                fills = list(await self._source.user_fills_by_time(wallet, start_ms, now_ms))
            except Exception as exc:  # noqa: BLE001 — one wallet must not abort the pass
                model_errors += 1
                logger.warning("closure_fills_fetch_failed", model_id=model_id, error=str(exc))
                continue
            if len(fills) >= _HL_FILL_CAP:
                logger.warning(
                    "closure_fill_cap_hit",
                    model_id=model_id,
                    count=len(fills),
                    note="window may be undercounted — an old trigger fill could be truncated",
                )
            try:
                async with self._session_factory() as session:
                    c, o, nr = await self._close_for_model(session, model_id, fills)
                    await session.commit()
                closed += c
                still_open += o
                no_run += nr
            except Exception as exc:  # noqa: BLE001 — one model must not abort the batch
                model_errors += 1
                logger.exception("closure_model_failed", model_id=model_id, error=str(exc))

        logger.info(
            "closure_reconcile_done",
            closed=closed,
            still_open=still_open,
            models=models,
            model_errors=model_errors,
            no_run=no_run,
        )
        return ClosureReconcileResult(closed, still_open, models, model_errors, no_run)

    async def _close_for_model(
        self, session: AsyncSession, model_id: str, fills: list[dict[str, object]]
    ) -> tuple[int, int, int]:
        """Book closures for one model's open positions. Returns (closed, still_open, no_run)."""
        repo = PositionsRepository(session)
        # Scoped to this reconciler's experiment (ADR-0039) — matching what
        # _models_with_open_positions already selects on. Without it the pass would try to
        # book closures for archived experiments' rows whose triggers are long gone.
        positions = await repo.list_open_for_model(
            experiment_id=self._experiment_id, model_id=model_id
        )
        if not positions:
            return (0, 0, 0)
        closing_run_id = await self._latest_run_id(session, model_id)

        closed = still_open = no_run = 0
        for position in positions:
            trigger_oids = await self._trigger_oids(session, position.opening_action_id)
            closure = detect_autonomous_closure(fills, trigger_oids)
            if closure is None:
                still_open += 1
                continue
            if closing_run_id is None:
                # A position implies an opening run, so this is unreachable in practice; guard
                # rather than violate outcomes.closing_run_id NOT NULL.
                no_run += 1
                logger.warning(
                    "closure_no_run_to_attribute",
                    model_id=model_id,
                    position_id=str(position.id),
                )
                continue
            corrected = closure.model_copy(
                update={"close_reason": _attribute_close_reason(position, closure, closing_run_id)}
            )
            await repo.close_position(
                str(position.id),
                corrected,
                closing_run_id,
                closing_action_id=None,
                close_order=None,
            )
            closed += 1
            logger.info(
                "closure_booked",
                model_id=model_id,
                position_id=str(position.id),
                symbol=position.symbol,
                close_reason=corrected.close_reason.value,
                exit_price=str(corrected.exit_price),
            )
        return (closed, still_open, no_run)

    async def _models_with_open_positions(
        self, session: AsyncSession
    ) -> dict[str, tuple[str, int]]:
        """Return ``{model_id: (wallet_address, fills_window_start_ms)}`` for models with open
        positions (window start = oldest open position's opened_at minus a small buffer)."""
        rows = (
            await session.execute(
                select(Position.model_id, func.min(Position.opened_at))
                .where(
                    Position.experiment_id == uuid.UUID(self._experiment_id),
                    Position.closed_at.is_(None),
                )
                .group_by(Position.model_id)
            )
        ).all()
        if not rows:
            return {}
        model_ids = [r[0] for r in rows]
        wallet_rows = (
            await session.execute(
                select(Model.id, Model.wallet_address).where(Model.id.in_(model_ids))
            )
        ).all()
        wallets: dict[str, str] = {row[0]: row[1] for row in wallet_rows}
        out: dict[str, tuple[str, int]] = {}
        for model_id, min_opened in rows:
            wallet = wallets.get(model_id)
            if wallet is None:
                logger.warning("closure_no_wallet_for_model", model_id=model_id)
                continue
            start_ms = int(_as_utc(min_opened).timestamp() * 1000) - _WINDOW_BUFFER_MS
            out[model_id] = (wallet, start_ms)
        return out

    async def _trigger_oids(self, session: AsyncSession, opening_action_id: uuid.UUID) -> set[str]:
        """The hl_order_id of the position's stop_loss + take_profit trigger orders."""
        rows = (
            await session.execute(
                select(Order.hl_order_id).where(
                    Order.decision_action_id == opening_action_id,
                    Order.order_kind.in_(_TRIGGER_KINDS),
                    Order.hl_order_id.is_not(None),
                )
            )
        ).all()
        return {str(r[0]) for r in rows}

    async def _latest_run_id(self, session: AsyncSession, model_id: str) -> str | None:
        """The model's most recent run (any status) — used as ``closing_run_id`` for the
        autonomous closure booked outside any model run (ADR-0038 convention)."""
        run_id = await session.scalar(
            select(Run.id)
            .where(
                Run.experiment_id == uuid.UUID(self._experiment_id),
                Run.model_id == model_id,
            )
            .order_by(Run.run_started_at.desc())
            .limit(1)
        )
        return str(run_id) if run_id is not None else None
