"""HyperliquidClient ABC + Mock/Real implementations (§7.5).

`RealHyperliquidClient` wraps the synchronous ``hyperliquid-python-sdk`` and is
the production path used once a funded testnet wallet is wired (M4-T08, human
gated). `MockHyperliquidClient` remains the in-memory test/dev double.
"""

import asyncio
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict

from aiat.domain.enums import CloseReason, OrderKind, Side
from aiat.domain.exceptions import ExecutionRejectedError, ExecutionTimeoutError
from aiat.domain.schemas import ActionDecision, OpenPositionSummary, PortfolioState
from aiat.execution.sizing import compute_position_sizing

if TYPE_CHECKING:
    from aiat.config.settings import AgentSettings

logger = structlog.get_logger(__name__)


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


# ---------------------------------------------------------------------------
# RealHyperliquidClient — production path over hyperliquid-python-sdk
# ---------------------------------------------------------------------------

# Testnet endpoint (invariant #9: this client NEVER targets mainnet).
_HL_TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"
_SUPPORTED_SYMBOLS: frozenset[str] = frozenset({"BTC", "ETH", "SOL"})
_DEFAULT_EXEC_TIMEOUT_S = 60.0
_BPS = Decimal("10000")


def _to_decimal(value: Any) -> Decimal:
    """Convert an SDK numeric (str | int | float) to Decimal via ``str``.

    Invariant #12: money never flows through ``float``. The SDK returns prices and
    sizes as float-strings (e.g. ``"30001.0"``); going through ``str`` preserves the
    exact textual value and avoids the lossy ``Decimal(float)`` path.
    """
    return Decimal(str(value))


@dataclass(frozen=True)
class _ParsedOrder:
    """Normalized view of a single Hyperliquid order status."""

    status: Literal["filled", "resting"]
    oid: str
    avg_px: Decimal | None
    total_sz: Decimal | None


def _parse_order_response(resp: object) -> _ParsedOrder:
    """Parse a Hyperliquid order/market response into a :class:`_ParsedOrder`.

    Hyperliquid wraps a single order outcome as::

        {"status": "ok", "response": {"type": "order",
            "data": {"statuses": [{"filled": {"totalSz": "..", "avgPx": "..", "oid": N}}]}}}

    A status item may instead be ``{"resting": {"oid": N}}`` (accepted, not yet
    filled) or ``{"error": "<msg>"}``. A top-level ``{"status": "err", ...}`` or an
    SDK JSON-parse ``{"error": ...}`` signals outright rejection.

    Raises:
        ExecutionRejectedError: any error/rejection or a structurally invalid body.
    """
    if not isinstance(resp, dict):
        raise ExecutionRejectedError(f"unexpected HL response: {resp!r}")
    if resp.get("status") == "err":
        raise ExecutionRejectedError(f"HL rejected order: {resp.get('response')!r}")
    if "error" in resp:  # SDK-level transport/parse error envelope
        raise ExecutionRejectedError(f"HL error: {resp['error']!r}")

    try:
        statuses = resp["response"]["data"]["statuses"]
    except (KeyError, TypeError) as exc:
        raise ExecutionRejectedError(f"malformed HL response: {resp!r}") from exc
    if not isinstance(statuses, list) or not statuses:
        raise ExecutionRejectedError(f"empty/invalid statuses in HL response: {resp!r}")

    item = statuses[0]
    if not isinstance(item, dict):
        raise ExecutionRejectedError(f"invalid status item: {item!r}")
    if "error" in item:
        raise ExecutionRejectedError(f"HL order error: {item['error']!r}")
    if "filled" in item:
        filled = item["filled"]
        oid, avg_px, total_sz = filled.get("oid"), filled.get("avgPx"), filled.get("totalSz")
        # Required fields must be present and non-null; a partial/garbled fill is a
        # rejection, never a KeyError/InvalidOperation leaking past the contract.
        if oid is None or avg_px is None or total_sz is None:
            raise ExecutionRejectedError(f"filled status missing oid/avgPx/totalSz: {item!r}")
        try:
            return _ParsedOrder(
                status="filled",
                oid=str(oid),
                avg_px=_to_decimal(avg_px),
                total_sz=_to_decimal(total_sz),
            )
        except (InvalidOperation, ValueError) as exc:
            raise ExecutionRejectedError(f"unparseable filled numerics: {item!r}") from exc
    if "resting" in item:
        oid = item["resting"].get("oid")
        if oid is None:
            raise ExecutionRejectedError(f"resting status missing oid: {item!r}")
        return _ParsedOrder(status="resting", oid=str(oid), avg_px=None, total_sz=None)
    raise ExecutionRejectedError(f"unrecognized HL order status: {item!r}")


class RealHyperliquidClient(HyperliquidClient):
    """Concrete client backed by ``hyperliquid-python-sdk`` (testnet only).

    Design contract (mirrors :class:`MockHyperliquidClient`'s side/ordering semantics):
        - ``execute_action`` honours the same side semantics — HOLD/FLAT no-op or
          close-only, LONG/SHORT open, same-side ignore, opposite-side close-then-open —
          and returns the same ``order_kind`` sequence the decision loop keys off.
        - Order sizing follows **ADR-0015**: the submitted/persisted ``size_units`` is
          the *leveraged* quantity, ``size_units = (equity · size_pct · leverage) /
          entry_price``, computed via :func:`compute_position_sizing`.
        - SL/TP ``OrderResult.requested_price`` records the actual trigger price (more
          faithful than the Mock's placeholder ``None``); all other fields match.

    Invariants enforced here:
        - **#9 (no mainnet)**: the constructor raises ``RuntimeError`` unless
          ``network == "testnet"``; :meth:`from_settings` hard-codes the testnet
          base URL and never builds a mainnet SDK client.
        - **#12 (Decimal money)**: ``float`` appears only transiently as an SDK call
          argument (the SDK API requires it); every numeric read back from the SDK is
          reconverted to ``Decimal`` immediately (see :func:`_to_decimal`). No
          ``float`` reaches :class:`OrderResult` / :class:`PortfolioState`.

    The synchronous SDK is invoked off the event loop via :func:`asyncio.to_thread`
    and bounded by ``timeout_seconds`` (default 60s); a timeout raises
    :class:`ExecutionTimeoutError`, an exchange rejection raises
    :class:`ExecutionRejectedError`.
    """

    def __init__(
        self,
        *,
        exchange: Any,
        info: Any,
        account_address: str,
        network: str = "testnet",
        timeout_seconds: float = _DEFAULT_EXEC_TIMEOUT_S,
    ) -> None:
        # Invariant #9: refuse to operate on anything but testnet. Defense in depth —
        # lifecycle._check_network_testnet validates at startup, but the client must
        # not be constructible against mainnet even in isolation.
        if network != "testnet":
            raise RuntimeError(
                f"RealHyperliquidClient requires AIAT_NETWORK='testnet' (invariant #9), "
                f"got {network!r}"
            )
        self._exchange = exchange
        self._info = info
        self._account_address = account_address
        self._network = network
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: "AgentSettings") -> "RealHyperliquidClient":
        """Build a testnet client from :class:`AgentSettings`.

        Imports the SDK lazily so the mock path never pays the SDK import cost.

        Raises:
            RuntimeError: if ``settings.network != "testnet"`` (invariant #9).
        """
        if settings.network != "testnet":
            raise RuntimeError(
                f"RealHyperliquidClient requires AIAT_NETWORK='testnet' (invariant #9), "
                f"got {settings.network!r}"
            )
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info

        wallet = Account.from_key(settings.hl_wallet_private_key.get_secret_value())
        # base_url hard-pinned to testnet — never derived from anything mainnet-capable.
        info = Info(base_url=_HL_TESTNET_API_URL, skip_ws=True)
        exchange = Exchange(
            wallet,
            base_url=_HL_TESTNET_API_URL,
            account_address=settings.hl_wallet_address,
        )
        return cls(
            exchange=exchange,
            info=info,
            account_address=settings.hl_wallet_address,
            network=settings.network,
            timeout_seconds=float(settings.hard_timeout_seconds),
        )

    # -- SDK invocation -----------------------------------------------------

    async def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a synchronous SDK call off-loop, bounded by ``timeout_seconds``.

        Raises:
            ExecutionTimeoutError: if the call exceeds ``timeout_seconds``.
        """
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn, *args, **kwargs),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:  # asyncio.TimeoutError is an alias since 3.11
            raise ExecutionTimeoutError(
                f"Hyperliquid call timed out after {self._timeout_seconds}s"
            ) from exc

    # -- fetch_portfolio_state ---------------------------------------------

    async def fetch_portfolio_state(self) -> PortfolioState:
        """Snapshot the wallet state from HL ``clearinghouseState`` + mark prices."""
        state = await self._call(self._info.user_state, self._account_address)
        mids = await self._call(self._info.all_mids)
        return self._build_portfolio_state(state, mids)

    def _build_portfolio_state(self, state: dict[str, Any], mids: dict[str, Any]) -> PortfolioState:
        margin = state.get("marginSummary", {})
        equity_usd = _to_decimal(margin.get("accountValue", "0"))
        margin_used_usd = _to_decimal(margin.get("totalMarginUsed", "0"))
        available_usd = _to_decimal(state.get("withdrawable", "0"))

        open_positions: list[OpenPositionSummary] = []
        unrealized_pnl_usd = Decimal("0")
        for asset_pos in state.get("assetPositions", []):
            pos = asset_pos.get("position", {})
            coin = pos.get("coin")
            szi = _to_decimal(pos.get("szi", "0"))
            if coin not in _SUPPORTED_SYMBOLS or szi == 0:
                continue
            upnl = _to_decimal(pos.get("unrealizedPnl", "0"))
            unrealized_pnl_usd += upnl
            entry_price = _to_decimal(pos.get("entryPx", "0"))
            current_price = _to_decimal(mids[coin]) if coin in mids else entry_price
            leverage = _to_decimal(pos.get("leverage", {}).get("value", "1"))
            open_positions.append(
                OpenPositionSummary(
                    symbol=coin,
                    side="LONG" if szi > 0 else "SHORT",
                    entry_price=entry_price,
                    current_price=current_price,
                    size_units=abs(szi),
                    leverage=leverage,
                    unrealized_pnl_usd=upnl,
                    # ASSUMPTION (validate M4-T08): HL clearinghouseState exposes no
                    # position open-time, so age cannot be derived from this call.
                    age_minutes=0,
                )
            )

        return PortfolioState(
            equity_usd=equity_usd,
            available_usd=available_usd,
            margin_used_usd=margin_used_usd,
            n_open_positions=len(open_positions),
            unrealized_pnl_usd=unrealized_pnl_usd,
            open_positions=open_positions,
        )

    # -- execute_action -----------------------------------------------------

    async def execute_action(
        self,
        action: ActionDecision,
        run_id: str,
        current_position: OpenPositionSummary | None,
    ) -> list[OrderResult]:
        """Execute one action on HL, mirroring :class:`MockHyperliquidClient`."""
        if action.side == Side.HOLD:
            return []

        if action.side == Side.FLAT:
            if current_position is None:
                return []
            return [await self._close_order(action.symbol, current_position)]

        # LONG or SHORT
        if current_position is not None:
            if current_position.side == action.side.value:
                return []  # same side — no add-to-position in v2
            # opposite side: close first, then open (2 phases)
            close = await self._close_order(action.symbol, current_position)
            return [close, *await self._open_orders(action)]

        return await self._open_orders(action)

    async def _close_order(self, symbol: str, current_position: OpenPositionSummary) -> OrderResult:
        """Market-close the whole position for ``symbol``."""
        resp = await self._call(self._exchange.market_close, symbol)
        parsed = _parse_order_response(resp)
        if parsed.status != "filled" or parsed.avg_px is None or parsed.total_sz is None:
            raise ExecutionRejectedError(f"close order did not fill for {symbol}: {resp!r}")
        return OrderResult(
            hl_order_id=parsed.oid,
            client_order_id=str(uuid.uuid4()),
            order_kind=OrderKind.CLOSE,
            status="filled",
            requested_price=None,
            filled_price=parsed.avg_px,
            # close uses the existing position size (matches Mock semantics).
            requested_size_units=current_position.size_units,
            filled_size_units=parsed.total_sz,
            slippage_bps=None,
            # ASSUMPTION (validate M4-T08): per-fill fee is not in the order response;
            # fee reconciliation from user_fills is deferred. None ⇒ no fee_event row.
            fee_usd=None,
            raw_response=resp,
        )

    def _sz_decimals(self, symbol: str) -> int:
        """Read the asset's size precision (szDecimals) live from the venue (ADR-0017)."""
        try:
            return int(self._info.asset_to_sz_decimals[self._info.name_to_asset(symbol)])
        except KeyError as exc:
            raise ExecutionRejectedError(
                f"cannot resolve szDecimals for {symbol}: {exc!r}"
            ) from exc

    def _quantize_size(self, symbol: str, size: Decimal) -> Decimal:
        """Quantize an order size to the asset's szDecimals with ROUND_DOWN (ADR-0017).

        Hyperliquid rejects sizes carrying more decimals than the asset's szDecimals
        ("float_to_wire causes rounding"). Quantizing stays in Decimal (inv #12); the
        float() conversion happens only at the SDK call. ROUND_DOWN guarantees the
        executed notional never exceeds the requested one (preserves the size guardrail,
        inv #8).
        """
        quantum = Decimal(1).scaleb(-self._sz_decimals(symbol))
        return size.quantize(quantum, rounding=ROUND_DOWN)

    async def _open_orders(self, action: ActionDecision) -> list[OrderResult]:
        """Open a LONG/SHORT position: leverage + market entry + SL/TP triggers."""
        # The ActionDecision validator guarantees SL/TP for LONG/SHORT (mirrors Mock).
        assert action.stop_loss_pct is not None
        assert action.take_profit_pct is not None

        # ADR-0015: equity (current account value) + mark price drive leveraged sizing.
        state = await self._call(self._info.user_state, self._account_address)
        equity_usd = _to_decimal(state.get("marginSummary", {}).get("accountValue", "0"))
        mids = await self._call(self._info.all_mids)
        if action.symbol not in mids:
            raise ExecutionRejectedError(f"no mark price for {action.symbol}")
        mark_price = _to_decimal(mids[action.symbol])

        sizing = compute_position_sizing(
            equity_usd=equity_usd,
            size_pct=action.size_pct,
            entry_price=mark_price,
            leverage=action.leverage,
            side=action.side,
            stop_loss_pct=action.stop_loss_pct,
            take_profit_pct=action.take_profit_pct,
        )
        # ADR-0017: quantize to the asset's szDecimals (ROUND_DOWN) at the SDK boundary,
        # else Hyperliquid rejects the order ("float_to_wire causes rounding").
        size_units = self._quantize_size(action.symbol, sizing.size_units)
        if size_units <= 0:
            raise ExecutionRejectedError(
                f"size for {action.symbol} quantizes to 0 at szDecimals="
                f"{self._sz_decimals(action.symbol)} "
                f"(theoretical size_units={sizing.size_units}); notional too small "
                "for one size step"
            )
        is_buy = action.side == Side.LONG

        # Set leverage for this coin (cross margin). ASSUMPTION (validate M4-T08):
        # cross margin + integer leverage (HL only accepts int leverage per coin).
        await self._call(self._exchange.update_leverage, int(action.leverage), action.symbol, True)

        # Market entry — submit the quantized leveraged size; float() only at the boundary.
        entry_resp = await self._call(
            self._exchange.market_open, action.symbol, is_buy, float(size_units)
        )
        entry_parsed = _parse_order_response(entry_resp)
        if (
            entry_parsed.status != "filled"
            or entry_parsed.avg_px is None
            or entry_parsed.total_sz is None
            or entry_parsed.total_sz == 0
        ):
            raise ExecutionRejectedError(
                f"entry order did not fill for {action.symbol}: {entry_resp!r}"
            )
        filled_price = entry_parsed.avg_px
        filled_size = entry_parsed.total_sz
        slippage_bps = (
            abs(filled_price - mark_price) / mark_price * _BPS if mark_price != 0 else None
        )
        entry = OrderResult(
            hl_order_id=entry_parsed.oid,
            client_order_id=str(uuid.uuid4()),
            order_kind=OrderKind.ENTRY,
            status="filled",
            requested_price=action.limit_price,
            filled_price=filled_price,
            requested_size_units=size_units,
            filled_size_units=filled_size,
            slippage_bps=slippage_bps,
            fee_usd=None,  # see _close_order note (fee reconciliation deferred)
            raw_response=entry_resp,
        )

        # SL/TP are reduce-only trigger orders on the opposite side, sized to the fill.
        sl = await self._trigger_order(
            symbol=action.symbol,
            is_buy=not is_buy,
            size_units=filled_size,
            trigger_price=sizing.stop_loss_price,
            kind=OrderKind.STOP_LOSS,
            tpsl="sl",
        )
        tp = await self._trigger_order(
            symbol=action.symbol,
            is_buy=not is_buy,
            size_units=filled_size,
            trigger_price=sizing.take_profit_price,
            kind=OrderKind.TAKE_PROFIT,
            tpsl="tp",
        )
        return [entry, sl, tp]

    async def _trigger_order(
        self,
        *,
        symbol: str,
        is_buy: bool,
        size_units: Decimal,
        trigger_price: Decimal,
        kind: OrderKind,
        tpsl: Literal["sl", "tp"],
    ) -> OrderResult:
        """Place a reduce-only stop-loss/take-profit trigger order."""
        # ADR-0017: quantize at the SDK boundary. The fill size from HL is already
        # szDecimals-valid, so this is idempotent in practice, but keeps every
        # size→SDK path uniform.
        size_units = self._quantize_size(symbol, size_units)
        order_type = {
            "trigger": {
                "triggerPx": float(trigger_price),
                "isMarket": True,
                "tpsl": tpsl,
            }
        }
        resp = await self._call(
            self._exchange.order,
            symbol,
            is_buy,
            float(size_units),
            float(trigger_price),
            order_type,
            True,  # reduce_only
        )
        parsed = _parse_order_response(resp)  # raises on rejection
        return OrderResult(
            hl_order_id=parsed.oid,
            client_order_id=str(uuid.uuid4()),
            order_kind=kind,
            # A just-placed trigger rests until its price is hit — mirror the Mock's
            # "triggered" status for a live-but-unfilled protective order.
            status="triggered",
            requested_price=trigger_price,
            filled_price=None,
            requested_size_units=size_units,
            filled_size_units=None,
            slippage_bps=None,
            fee_usd=None,
            raw_response=resp,
        )

    # -- check_position_closure --------------------------------------------

    async def check_position_closure(
        self,
        hl_position_id: str,
    ) -> PositionClosureInfo | None:
        """Detect whether the position for a coin has closed since the last tick.

        ``hl_position_id`` is interpreted as the **coin symbol** (HL has no stable
        position identifier — a wallet holds at most one position per coin). Returns
        ``None`` while a non-zero position is still open.

        ASSUMPTION (validate / formalize in M4-T08, scientifically sensitive):
            ``close_reason`` attribution from HL fills is best-effort —
            ``LIQUIDATED`` when any closing fill is flagged a liquidation, else
            ``MODEL_CLOSE``. Distinguishing STOP_LOSS vs TAKE_PROFIT requires
            matching the closing fill's ``oid`` to the stored trigger order ids,
            which this stateless client does not have. The exact rule needs a
            decision (likely an ADR) once real testnet fill shapes are observed.
        """
        state = await self._call(self._info.user_state, self._account_address)
        for asset_pos in state.get("assetPositions", []):
            pos = asset_pos.get("position", {})
            if pos.get("coin") == hl_position_id and _to_decimal(pos.get("szi", "0")) != 0:
                return None  # still open

        fills = await self._call(self._info.user_fills, self._account_address)
        close_fills = [
            f
            for f in (fills or [])
            if isinstance(f, dict)
            and f.get("coin") == hl_position_id
            and str(f.get("dir", "")).lower().startswith("close")
        ]
        if not close_fills:
            return None  # no closing fill observed yet — treat as still pending

        last = close_fills[0]  # HL returns most-recent fills first
        try:
            # closedPnl may be missing or explicitly null on a fill — coalesce to 0
            # (`.get(default)` does NOT catch a present-but-null value).
            realized = sum(
                (_to_decimal(f.get("closedPnl") or "0") for f in close_fills), Decimal("0")
            )
            exit_raw = last.get("px")
            exit_price = _to_decimal(exit_raw) if exit_raw is not None else Decimal("0")
        except (InvalidOperation, ValueError) as exc:
            # Malformed fill numerics: do not fabricate a closure — retry next tick.
            logger.warning("hl_closure_unparseable", coin=hl_position_id, error=str(exc))
            return None
        if exit_price <= 0:
            # An exit price of 0 would violate the positions.exit_price > 0 CHECK; treat
            # the closure as not-yet-observable rather than persisting a corrupt row.
            logger.warning("hl_closure_nonpositive_exit_price", coin=hl_position_id)
            return None

        liquidated = any(bool(f.get("liquidation")) for f in close_fills)
        closed_at_ms = last.get("time")
        closed_at = (
            datetime.fromtimestamp(int(closed_at_ms) / 1000, tz=UTC).isoformat()
            if closed_at_ms is not None
            else datetime.now(UTC).isoformat()
        )
        return PositionClosureInfo(
            closed_at=closed_at,
            exit_price=exit_price,
            close_reason=CloseReason.LIQUIDATED if liquidated else CloseReason.MODEL_CLOSE,
            realized_pnl_usd=realized,
        )


def build_hl_client(settings: "AgentSettings") -> HyperliquidClient:
    """Select the Hyperliquid client implementation for an agent service.

    Defaults to the in-memory mock so existing tests and un-provisioned deploys stay
    green; set ``AIAT_HL_CLIENT_IMPL=real`` to use the live testnet SDK client once a
    funded wallet is wired (M4-T08).
    """
    if getattr(settings, "hl_client_impl", "mock") == "real":
        return RealHyperliquidClient.from_settings(settings)
    return MockHyperliquidClient()
