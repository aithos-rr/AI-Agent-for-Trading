"""Repository for positions + orders + fee_events (§7.6)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.action import DecisionAction
from aiat.db.models.fee_event import FeeEvent
from aiat.db.models.funding_event import FundingEvent
from aiat.db.models.order import Order
from aiat.db.models.outcome import Outcome
from aiat.db.models.position import Position
from aiat.domain.enums import CloseReason, OrderKind
from aiat.execution.hyperliquid_client import OrderResult, PositionClosureInfo

logger = structlog.get_logger(__name__)


class PositionsRepository:
    """Bounded context: positions + orders + fee_events + funding_events (§7.6).

    No internal commit — caller owns the Unit of Work (AsyncSession).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open_position(
        self,
        action_id: str,
        order_results: list[OrderResult],
        run_id: str,
    ) -> str:
        """Create position + orders + fee_events in the caller's transaction.

        Returns:
            position_id (str UUID) of the new Position row.

        Raises:
            ValueError: if action not found or entry order is missing.
            IntegrityError: if opening_action_id already has a position (UNIQUE).
        """
        action = await self._session.get(DecisionAction, uuid.UUID(action_id))
        if action is None:
            raise ValueError(f"DecisionAction {action_id!r} not found")

        entry_order = next(
            (o for o in order_results if o.order_kind == OrderKind.ENTRY),
            None,
        )
        if entry_order is None:
            raise ValueError("No entry order in order_results")
        if entry_order.filled_price is None:
            raise ValueError("Entry order has no filled_price")

        entry_price = entry_order.filled_price
        # Use the actual filled size when present; fall back to requested only when
        # filled is unknown (None). A real Decimal("0") fill is distinct from None and
        # must NOT be silently rewritten to requested — it would fabricate the size and
        # corrupt notional/margin (inv #12). A genuine zero fill fails loud downstream
        # via the chk_position_size_units_gt0 CHECK.
        size_units = (
            entry_order.filled_size_units
            if entry_order.filled_size_units is not None
            else entry_order.requested_size_units
        )
        leverage = action.leverage_executed
        notional_value_usd = size_units * entry_price
        initial_margin_usd = notional_value_usd / leverage

        sl_pct = action.stop_loss_pct
        tp_pct = action.take_profit_pct
        if sl_pct is None or tp_pct is None:
            raise ValueError("Action must declare stop_loss_pct and take_profit_pct")

        side = action.side_executed
        if side == "LONG":
            stop_loss_price = entry_price * (1 - sl_pct)
            take_profit_price = entry_price * (1 + tp_pct)
        else:  # SHORT
            stop_loss_price = entry_price * (1 + sl_pct)
            take_profit_price = entry_price * (1 - tp_pct)

        now = datetime.now(UTC)

        position = Position(
            id=uuid.uuid4(),
            experiment_id=action.experiment_id,
            model_id=action.model_id,
            opening_run_id=uuid.UUID(run_id),
            symbol=action.symbol,
            side=side,
            opening_action_id=action.id,
            opened_at=now,
            entry_price=entry_price,
            size_units=size_units,
            leverage=leverage,
            notional_value_usd=notional_value_usd,
            initial_margin_usd=initial_margin_usd,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
        )
        self._session.add(position)
        await self._session.flush()

        for order_result in order_results:
            order = Order(
                id=uuid.uuid4(),
                decision_action_id=action.id,
                experiment_id=action.experiment_id,
                model_id=action.model_id,
                run_id=uuid.UUID(run_id),
                symbol=action.symbol,
                order_kind=order_result.order_kind.value,
                hl_order_id=order_result.hl_order_id,
                client_order_id=order_result.client_order_id,
                status=order_result.status,
                requested_price=order_result.requested_price,
                filled_price=order_result.filled_price,
                requested_size_units=order_result.requested_size_units,
                filled_size_units=order_result.filled_size_units,
                slippage_bps=order_result.slippage_bps,
                raw_order_response=order_result.raw_response,
                submitted_at=now,
                filled_at=now if order_result.status == "filled" else None,
            )
            self._session.add(order)
            await self._session.flush()

            if order_result.fee_usd is not None:
                fee_event = FeeEvent(
                    id=uuid.uuid4(),
                    order_id=order.id,
                    position_id=position.id,
                    experiment_id=action.experiment_id,
                    model_id=action.model_id,
                    run_id=uuid.UUID(run_id),
                    fee_type=_fee_type(order_result.order_kind),
                    fee_usd=order_result.fee_usd,
                    occurred_at=now,
                )
                self._session.add(fee_event)

        await self._session.flush()
        return str(position.id)

    async def close_position(
        self,
        position_id: str,
        closure: PositionClosureInfo,
        closing_run_id: str,
        closing_action_id: str | None,
        close_order: OrderResult | None,
    ) -> None:
        """Update position with closing fields and create outcomes row.

        Two closure shapes (ADR-0027 + ADR-0030), keyed by whether a model decision caused
        the close. The two optional arguments travel together — pass both, or neither:

        - **model_close (FLAT)**: the model decided to close, so ``closing_action_id`` (the
          FLAT ``decision_action``) and ``close_order`` (the CLOSE ``OrderResult`` we
          submitted) are both provided. Persists ``positions.closing_action_id``, a
          ``close`` orders row, and — if present — the close ``fee_event``.
        - **autonomous (SL/TP trigger, liquidation)**: the exchange closed the position with
          no model action and no close order of ours, so both are ``None``.
          ``closing_action_id`` stays NULL and no ``close`` order row is written.
          ``chk_position_closed_consistency`` admits NULL ``closing_action_id`` for
          ``stop_loss``/``take_profit``/``liquidated``. For an SL/TP closure the taker fee —
          now carried on ``closure.fee_usd`` (finding A) — IS persisted as a ``taker_close``
          ``fee_event`` linked to the fired trigger order (ADR-0032, closes ADR-0030 (iv)).
          Liquidation fees stay deferred (no trigger order of ours to satisfy the fee_events
          FK; ADR-0025). The SL/TP trigger `orders` rows are still left ``status='triggered'``
          (marking the fired trigger ``filled`` is the remaining ADR-0025 deferral).

        Computes sum_fees_usd and sum_funding_usd from the DB, then persists
        an Outcome in the caller's transaction.  pnl_net_fee_funding_tax_sim_usd
        is set to Decimal("0") — the tax-sim value is populated later by
        scripts/compute_tax_sim.py (M5), never here (ADR-0014, matching
        OutcomeResolver).
        """
        pos = await self._session.get(Position, uuid.UUID(position_id))
        if pos is None:
            raise ValueError(f"Position {position_id!r} not found")

        closed_at_dt = datetime.fromisoformat(closure.closed_at)
        if closed_at_dt.tzinfo is None:
            closed_at_dt = closed_at_dt.replace(tzinfo=UTC)

        pos.closed_at = closed_at_dt
        pos.exit_price = closure.exit_price
        pos.close_reason = closure.close_reason.value
        pos.realized_pnl_usd = closure.realized_pnl_usd
        # ADR-0030: an autonomous closure (SL/TP trigger or liquidation) is executed by the
        # exchange with no model decision_action and no close OrderResult of ours, so
        # closing_action_id and close_order arrive as None — leave closing_action_id NULL
        # (the revised chk_position_closed_consistency admits NULL for
        # stop_loss/take_profit/liquidated) and skip the close order row. The SL/TP trigger
        # `orders` rows already exist from open_position (left status='triggered'); their fee
        # is now persisted below (ADR-0032) while marking the trigger filled stays deferred
        # (ADR-0025). The FLAT (model_close) path supplies both and keeps its own bookkeeping
        # (ADR-0027).
        if closing_action_id is not None:
            pos.closing_action_id = uuid.UUID(closing_action_id)
        await self._session.flush()

        if closing_action_id is not None and close_order is not None:
            # Persist the close order row so closures enter the orders audit dataset at
            # parity with entry/SL/TP (ADR-0027 fix (a)). Mirrors the open_position order
            # loop; mapped from the CLOSE OrderResult produced by the decision_loop.
            close_order_row = Order(
                id=uuid.uuid4(),
                decision_action_id=uuid.UUID(closing_action_id),
                experiment_id=pos.experiment_id,
                model_id=pos.model_id,
                run_id=uuid.UUID(closing_run_id),
                symbol=pos.symbol,
                order_kind=close_order.order_kind.value,
                hl_order_id=close_order.hl_order_id,
                client_order_id=close_order.client_order_id,
                status=close_order.status,
                requested_price=close_order.requested_price,
                filled_price=close_order.filled_price,
                requested_size_units=close_order.requested_size_units,
                filled_size_units=close_order.filled_size_units,
                slippage_bps=close_order.slippage_bps,
                raw_order_response=close_order.raw_response,
                submitted_at=closed_at_dt,
                filled_at=closed_at_dt if close_order.status == "filled" else None,
            )
            self._session.add(close_order_row)
            await self._session.flush()

            # Create the close fee_event BEFORE the sum(fee_usd) select below, so the
            # closing fee is included in the net PnL (ADR-0027 fix (a)).
            if close_order.fee_usd is not None:
                close_fee_event = FeeEvent(
                    id=uuid.uuid4(),
                    order_id=close_order_row.id,
                    position_id=pos.id,
                    experiment_id=pos.experiment_id,
                    model_id=pos.model_id,
                    run_id=uuid.UUID(closing_run_id),
                    fee_type=_fee_type(close_order.order_kind),
                    fee_usd=close_order.fee_usd,
                    occurred_at=closed_at_dt,
                )
                self._session.add(close_fee_event)
                await self._session.flush()

        # ADR-0032 (closes ADR-0030 limit (iv) for SL/TP): an autonomous SL/TP closure has no
        # close order of ours, but its taker fee is now reconciled onto
        # PositionClosureInfo.fee_usd (finding A, 51a8e45). Persist it as a taker_close FeeEvent
        # linked to the fired trigger order row (exists from open_position, order_kind
        # stop_loss/take_profit) so it enters sum_fees_usd below. Liquidations stay deferred
        # (ADR-0025): no trigger order of ours to satisfy the NOT-NULL fee_events.order_id FK,
        # and a liquidation is not an SL/TP fill.
        elif (
            close_order is None
            and closure.fee_usd is not None
            and closure.close_reason in (CloseReason.STOP_LOSS, CloseReason.TAKE_PROFIT)
        ):
            trigger_kind = (
                OrderKind.STOP_LOSS
                if closure.close_reason == CloseReason.STOP_LOSS
                else OrderKind.TAKE_PROFIT
            )
            trigger_order = await self._session.scalar(
                select(Order).where(
                    Order.decision_action_id == pos.opening_action_id,
                    Order.order_kind == trigger_kind.value,
                )
            )
            if trigger_order is not None:
                self._session.add(
                    FeeEvent(
                        id=uuid.uuid4(),
                        order_id=trigger_order.id,
                        position_id=pos.id,
                        experiment_id=pos.experiment_id,
                        model_id=pos.model_id,
                        run_id=uuid.UUID(closing_run_id),
                        fee_type=_fee_type(trigger_kind),  # taker_close
                        fee_usd=closure.fee_usd,
                        occurred_at=closed_at_dt,
                    )
                )
                await self._session.flush()
            else:
                logger.warning(
                    "autonomous_close_fee_no_trigger_order",
                    position_id=str(pos.id),
                    close_reason=closure.close_reason.value,
                )

        fee_row = await self._session.execute(
            select(func.coalesce(func.sum(FeeEvent.fee_usd), Decimal("0"))).where(
                FeeEvent.position_id == pos.id
            )
        )
        sum_fees_usd: Decimal = fee_row.scalar_one()

        funding_row = await self._session.execute(
            select(func.coalesce(func.sum(FundingEvent.funding_amount_usd), Decimal("0"))).where(
                FundingEvent.position_id == pos.id
            )
        )
        sum_funding_usd: Decimal = funding_row.scalar_one()

        pnl_net_fee_usd = closure.realized_pnl_usd - sum_fees_usd
        pnl_net_fee_funding_usd = pnl_net_fee_usd - sum_funding_usd

        opening_action = await self._session.get(DecisionAction, pos.opening_action_id)
        if opening_action is None:
            raise ValueError(f"Opening DecisionAction {pos.opening_action_id!r} not found")

        holding_duration_min = max(0, int((closed_at_dt - pos.opened_at).total_seconds() / 60))
        horizon_met = holding_duration_min <= opening_action.time_horizon_min

        outcome = Outcome(
            id=uuid.uuid4(),
            position_id=pos.id,
            opening_action_id=pos.opening_action_id,
            opening_run_id=pos.opening_run_id,
            closing_run_id=uuid.UUID(closing_run_id),
            experiment_id=pos.experiment_id,
            model_id=pos.model_id,
            symbol=pos.symbol,
            realized_pnl_gross_usd=closure.realized_pnl_usd,
            sum_fees_usd=sum_fees_usd,
            sum_funding_usd=sum_funding_usd,
            pnl_net_fee_usd=pnl_net_fee_usd,
            pnl_net_fee_funding_usd=pnl_net_fee_funding_usd,
            # tax-sim value populated by scripts/compute_tax_sim.py in M5, never here
            # (ADR-0014; consistent with OutcomeResolver which also writes Decimal("0")).
            pnl_net_fee_funding_tax_sim_usd=Decimal("0"),
            was_profitable_net=pnl_net_fee_funding_usd > Decimal("0"),
            holding_duration_min=holding_duration_min,
            decision_action_confidence=opening_action.confidence,
            decision_action_time_horizon_min=opening_action.time_horizon_min,
            horizon_met=horizon_met,
        )
        self._session.add(outcome)
        await self._session.flush()

    async def list_open_for_model(self, *, experiment_id: str, model_id: str) -> list[Position]:
        """Return a model's open positions **within one experiment** (ADR-0039).

        Both filters are mandatory and keyword-only — this is the single read path every
        open-position consumer goes through (FLAT bookkeeping, chain-divergence detection,
        ``ClosureReconciler``), and an unscoped variant of it caused the cross-experiment
        leakage of 2026-07-29: a FLAT in the smoke experiment closed an *archived* M6.1 row
        for the same ``(model_id, symbol)``, then every later closure shifted by one
        (ADR-0039). Making ``experiment_id`` required in the signature is what keeps that
        unrepresentable: a caller cannot forget the filter, only pass the wrong value, and
        mypy rejects the old positional call shape outright.

        Ordering is explicit (``opened_at`` ASC, ``id`` as tie-break) so callers that pick
        one row out of several — the FLAT path takes the first match per symbol — behave
        deterministically instead of inheriting Postgres' heap order.

        Args:
            experiment_id: UUID string of the CURRENT experiment (``settings.experiment_id``).
            model_id: the agent's model id (invariant #1).

        Returns:
            Open (``closed_at IS NULL``) positions of that experiment+model, oldest first.
        """
        result = await self._session.execute(
            select(Position)
            .where(
                Position.experiment_id == uuid.UUID(experiment_id),
                Position.model_id == model_id,
                Position.closed_at.is_(None),
            )
            .order_by(Position.opened_at.asc(), Position.id.asc())
        )
        return list(result.scalars().all())


def _fee_type(order_kind: OrderKind) -> str:
    """Map OrderKind to fee_type CHECK value."""
    if order_kind == OrderKind.ENTRY:
        return "taker_open"
    return "taker_close"
