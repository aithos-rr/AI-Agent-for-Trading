"""Repository for positions + orders + fee_events (§7.6)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.action import DecisionAction
from aiat.db.models.fee_event import FeeEvent
from aiat.db.models.funding_event import FundingEvent
from aiat.db.models.order import Order
from aiat.db.models.outcome import Outcome
from aiat.db.models.position import Position
from aiat.domain.enums import OrderKind
from aiat.execution.hyperliquid_client import OrderResult, PositionClosureInfo


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
    ) -> None:
        """Update position with closing fields and create outcomes row.

        Computes sum_fees_usd and sum_funding_usd from the DB, then persists
        an Outcome in the caller's transaction.  pnl_net_fee_funding_tax_sim_usd
        is set equal to pnl_net_fee_funding_usd (tax-sim populated in M5).
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
        await self._session.flush()

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
            pnl_net_fee_funding_tax_sim_usd=pnl_net_fee_funding_usd,
            was_profitable_net=pnl_net_fee_funding_usd > Decimal("0"),
            holding_duration_min=holding_duration_min,
            decision_action_confidence=opening_action.confidence,
            decision_action_time_horizon_min=opening_action.time_horizon_min,
            horizon_met=horizon_met,
        )
        self._session.add(outcome)
        await self._session.flush()

    async def list_open_for_model(self, model_id: str) -> list[Position]:
        """Return all open positions for a model (closed_at IS NULL)."""
        result = await self._session.execute(
            select(Position).where(
                Position.model_id == model_id,
                Position.closed_at.is_(None),
            )
        )
        return list(result.scalars().all())


def _fee_type(order_kind: OrderKind) -> str:
    """Map OrderKind to fee_type CHECK value."""
    if order_kind == OrderKind.ENTRY:
        return "taker_open"
    return "taker_close"
