"""HyperliquidClient ABC + MockHyperliquidClient (§7.5)."""

import uuid
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from aiat.domain.enums import CloseReason, OrderKind, Side
from aiat.domain.schemas import ActionDecision, OpenPositionSummary, PortfolioState
from aiat.execution.sizing import compute_position_sizing


class OrderResult(BaseModel):
    """Result of a single order submitted to Hyperliquid."""

    model_config = ConfigDict(extra="forbid")

    hl_order_id: str
    client_order_id: str
    order_kind: OrderKind
    status: Literal["pending", "filled", "partial", "cancelled", "rejected", "triggered"]
    requested_price: Decimal | None
    filled_price: Decimal | None
    requested_size_units: Decimal
    filled_size_units: Decimal | None
    slippage_bps: Decimal | None
    fee_usd: Decimal | None
    raw_response: dict[str, Any]


class PositionClosureInfo(BaseModel):
    """Information about a closed position, returned by check_position_closure."""

    model_config = ConfigDict(extra="forbid")

    closed_at: str  # ISO 8601 timestamp
    exit_price: Decimal
    close_reason: CloseReason
    realized_pnl_usd: Decimal


class HyperliquidClient(ABC):
    """Wrapper testnet del Hyperliquid SDK."""

    @abstractmethod
    async def fetch_portfolio_state(self) -> PortfolioState:
        """Snapshot dello stato wallet. Letto a inizio di ogni decision_loop."""
        ...

    @abstractmethod
    async def execute_action(
        self,
        action: ActionDecision,
        run_id: str,
        current_position: OpenPositionSummary | None,
    ) -> list[OrderResult]:
        """
        Esegue una action conoscendo lo stato corrente della posizione.

        Args:
            action: la action post-guardrail da eseguire.
            run_id: per audit, FK in `orders.run_id`.
            current_position: posizione aperta per `action.symbol`, o None.

        Semantica per `action.side`:
            LONG/SHORT: se current_position è None, apre nuova; se esiste della
                stessa side, ignora (no add-to-position in v2); se esiste della
                opposite side, prima close, poi open (2 fasi).
            FLAT: se current_position è None, no-op; altrimenti close-only.
            HOLD: no-op.

        Raises:
            ExecutionRejectedError: ordine rifiutato da HL (margin, size limits).
            ExecutionTimeoutError: timeout 60s superato.
        """
        ...

    @abstractmethod
    async def check_position_closure(
        self,
        hl_position_id: str,
    ) -> PositionClosureInfo | None:
        """Ritorna None se la posizione è ancora aperta."""
        ...


class MockHyperliquidClient(HyperliquidClient):
    """In-memory mock for unit tests. No real network calls."""

    def __init__(
        self,
        portfolio_state: PortfolioState | None = None,
        closed_positions: dict[str, PositionClosureInfo] | None = None,
    ) -> None:
        self._portfolio_state = portfolio_state or PortfolioState(
            equity_usd=Decimal("10000.00"),
            available_usd=Decimal("9500.00"),
            margin_used_usd=Decimal("500.00"),
            n_open_positions=0,
            unrealized_pnl_usd=Decimal("0.00"),
            open_positions=[],
        )
        self._closed_positions: dict[str, PositionClosureInfo] = (
            closed_positions if closed_positions is not None else {}
        )
        self.executed_actions: list[tuple[ActionDecision, str, OpenPositionSummary | None]] = []

    async def fetch_portfolio_state(self) -> PortfolioState:
        return self._portfolio_state

    async def execute_action(
        self,
        action: ActionDecision,
        run_id: str,
        current_position: OpenPositionSummary | None,
    ) -> list[OrderResult]:
        self.executed_actions.append((action, run_id, current_position))

        if action.side == Side.HOLD:
            return []

        if action.side == Side.FLAT:
            if current_position is None:
                return []
            return [self._close_order(current_position.size_units)]

        # LONG or SHORT
        if current_position is not None:
            if current_position.side == action.side.value:
                return []  # same side — no add-to-position in v2
            # opposite side: close first, then open
            return [self._close_order(current_position.size_units)] + self._open_orders(action)

        return self._open_orders(action)

    async def check_position_closure(
        self,
        hl_position_id: str,
    ) -> PositionClosureInfo | None:
        return self._closed_positions.get(hl_position_id)

    def _close_order(self, size_units: Decimal) -> OrderResult:
        return OrderResult(
            hl_order_id=str(uuid.uuid4()),
            client_order_id=str(uuid.uuid4()),
            order_kind=OrderKind.CLOSE,
            status="filled",
            requested_price=None,
            filled_price=Decimal("100.00"),
            requested_size_units=size_units,
            filled_size_units=size_units,
            slippage_bps=Decimal("5"),
            fee_usd=Decimal("0.50"),
            raw_response={},
        )

    def _open_orders(self, action: ActionDecision) -> list[OrderResult]:
        # ADR-0015: persist the LEVERAGED executed quantity, not the raw size_pct.
        # `_open_orders` is only reached for LONG/SHORT actions, where the
        # ActionDecision validator guarantees SL/TP are present.
        assert action.stop_loss_pct is not None
        assert action.take_profit_pct is not None
        entry_price = Decimal("100.00")
        sizing = compute_position_sizing(
            equity_usd=self._portfolio_state.equity_usd,
            size_pct=action.size_pct,
            entry_price=entry_price,
            leverage=action.leverage,
            side=action.side,
            stop_loss_pct=action.stop_loss_pct,
            take_profit_pct=action.take_profit_pct,
        )
        size_units = sizing.size_units
        entry = OrderResult(
            hl_order_id=str(uuid.uuid4()),
            client_order_id=str(uuid.uuid4()),
            order_kind=OrderKind.ENTRY,
            status="filled",
            requested_price=action.limit_price,
            filled_price=entry_price,
            requested_size_units=size_units,
            filled_size_units=size_units,
            slippage_bps=Decimal("5"),
            fee_usd=Decimal("1.00"),
            raw_response={},
        )
        sl = OrderResult(
            hl_order_id=str(uuid.uuid4()),
            client_order_id=str(uuid.uuid4()),
            order_kind=OrderKind.STOP_LOSS,
            status="triggered",
            requested_price=None,
            filled_price=None,
            requested_size_units=size_units,
            filled_size_units=None,
            slippage_bps=None,
            fee_usd=None,
            raw_response={},
        )
        tp = OrderResult(
            hl_order_id=str(uuid.uuid4()),
            client_order_id=str(uuid.uuid4()),
            order_kind=OrderKind.TAKE_PROFIT,
            status="triggered",
            requested_price=None,
            filled_price=None,
            requested_size_units=size_units,
            filled_size_units=None,
            slippage_bps=None,
            fee_usd=None,
            raw_response={},
        )
        return [entry, sl, tp]
