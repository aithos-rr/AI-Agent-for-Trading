"""M4-T08 — real Hyperliquid **testnet** smoke e2e (PRD §12 M4, HUMAN-GATED).

This test does NOT run in the devcontainer (firewalled from Hyperliquid). It runs
in WSL against a funded HL **testnet** wallet, and SKIPS cleanly when no wallet is
configured so the CI/container gate stays green.

Deliberate scope: a thin slice that exercises the REAL SDK integration of
``RealHyperliquidClient`` plus the outcome PnL math, mirroring the decision loop's
model-close wiring (``RealHyperliquidClient.execute_action`` →
``PositionsRepository.open_position`` → ``execute_action`` (FLAT) →
``PositionsRepository.close_position``). It does NOT go through the LLM/decision
path — that is M5-T14 (local multi-tick smoke).

SDK assumptions this smoke VALIDATES against a live wallet:
  1. open/close round-trip: market entry + reduce-only SL/TP triggers, then a
     market close, parse to OrderResult with Decimal price/size (inv #12).
  2. leveraged sizing (ADR-0015): size_units = equity·size_pct·leverage / mark,
     submitted via update_leverage (integer leverage) + market_open.
  3. symbol-as-identity (ADR-0016): the position is opened/closed and the outcome
     is keyed purely by coin symbol.
  4. outcome PnL math end-to-end: the Outcome row created by close_position
     satisfies pnl_net_fee_funding = gross − fees − funding (market-independent).
  5. holding-duration / Decimal discipline survive a real round-trip.
  6. **fee_usd reconciliation from user_fills** (finding A, ADR-0027): the client now
     reconciles the taker fee by oid from ``user_fills`` for entry + model-close orders,
     so ``sum_fees_usd`` reflects a real fee. We assert it is a non-negative Decimal (a
     taker fee is positive, but we avoid a magnitude assertion to stay venue-agnostic).

Assumptions this smoke does NOT validate (honest record for the thesis):
  - **close_reason SL-vs-TP-vs-liquidation attribution**: distinguishing these
    requires forcing the price to hit a trigger; deferred (would need an ADR once
    real fill shapes are observed).
  - **autonomous-close fee persistence**: SL/TP-trigger/liquidation fees are captured on
    PositionClosureInfo but their fee_event persistence stays deferred (ADR-0025).
"""

from __future__ import annotations

import contextlib
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.config.settings import AgentSettings
from aiat.db.models.action import DecisionAction
from aiat.db.models.outcome import Outcome
from aiat.db.models.position import Position
from aiat.db.repositories.positions import PositionsRepository
from aiat.domain.enums import CloseReason, EntryType, OrderKind, Side
from aiat.domain.schemas import ActionDecision, OpenPositionSummary
from aiat.execution.hyperliquid_client import (
    OrderResult,
    PositionClosureInfo,
    RealHyperliquidClient,
)
from tests.integration.test_db_repositories_positions import _seed, _seed_flat_closing_action

# Every test here requires a real testnet wallet + network; mark + skip-guard.
pytestmark = pytest.mark.testnet

_SYMBOL = "BTC"
_TARGET_NOTIONAL_USD = Decimal("75")  # tiny, well below the $700 cap
_LEVERAGE = Decimal("2")
_SL_PCT = Decimal("0.10")  # wide so SL/TP do NOT trigger before the immediate close
_TP_PCT = Decimal("0.10")


def _settings_from_env() -> AgentSettings:
    """Build agent settings from the testnet wallet env (LLM fields are placeholders)."""
    return AgentSettings(  # type: ignore[call-arg]
        experiment_id="testnet-smoke",
        git_commit_sha="testnet-smoke",
        database_url="postgresql+asyncpg://x:x@localhost/x",
        network=os.environ.get("AIAT_NETWORK", ""),
        service_role="agent",
        model_id="testnet-smoke-model",
        prompt_template_hash="0" * 64,
        schema_version="v1",
        llm_provider="openai",
        model_name_api="gpt-4o",
        openai_api_key="sk-unused-for-hl-smoke",
        hl_wallet_private_key=os.environ["AIAT_HL_WALLET_PRIVATE_KEY"],
        hl_wallet_address=os.environ.get("AIAT_HL_WALLET_ADDRESS", ""),
        llm_gateway="direct",
        hl_client_impl="real",
        hard_timeout_seconds=60,
    )


def _flat_action(symbol: str) -> ActionDecision:
    return ActionDecision(
        symbol=symbol,  # type: ignore[arg-type]
        side=Side.FLAT,
        leverage=Decimal("0"),
        size_pct=Decimal("0"),
        stop_loss_pct=None,
        take_profit_pct=None,
        entry_type=EntryType.NONE,
        limit_price=None,
        confidence=Decimal("0.60"),
        time_horizon_min=120,
        action_reasoning="Testnet smoke: close the position to flatten exposure.",
        action_key_signals=[],
    )


async def _close_residual_positions(client: RealHyperliquidClient) -> None:
    """Best-effort teardown: flatten any open position so no exposure lingers."""
    with contextlib.suppress(Exception):
        state = await client.fetch_portfolio_state()
        for pos in state.open_positions:
            summary = OpenPositionSummary(
                symbol=pos.symbol,
                side=pos.side,
                entry_price=pos.entry_price,
                current_price=pos.current_price,
                size_units=pos.size_units,
                leverage=pos.leverage,
                unrealized_pnl_usd=pos.unrealized_pnl_usd,
                age_minutes=pos.age_minutes,
            )
            await client.execute_action(_flat_action(pos.symbol), "teardown", summary)


@pytest.mark.skipif(
    not os.environ.get("AIAT_HL_WALLET_PRIVATE_KEY"),
    reason="requires a funded Hyperliquid testnet wallet (M4-T08, runs in WSL)",
)
@pytest.mark.asyncio
async def test_testnet_open_close_outcome(db_session: AsyncSession) -> None:
    """Open a tiny LONG BTC on real testnet, close it, assert the outcome PnL math.

    Mirrors the decision loop's model-close path. The strong assertion is the
    market-INDEPENDENT identity pnl_net_fee_funding = gross − fees − funding, so the
    test does not depend on which way the price moved in the few seconds it was open.
    """
    # HARD safety (invariant #9): if a wallet is configured we MUST be on testnet.
    assert os.environ.get("AIAT_NETWORK") == "testnet", (
        "AIAT_NETWORK must be 'testnet' — refusing to trade on anything else (inv #9)"
    )
    assert os.environ.get("AIAT_HL_WALLET_ADDRESS"), "set AIAT_HL_WALLET_ADDRESS too"

    settings = _settings_from_env()
    client = RealHyperliquidClient.from_settings(settings)

    try:
        # Size a tiny position from the live equity (Decimal throughout, inv #12).
        portfolio = await client.fetch_portfolio_state()
        equity = portfolio.equity_usd
        assert equity > 0, "testnet wallet has no equity — fund it via the faucet first"
        size_pct = (_TARGET_NOTIONAL_USD / (equity * _LEVERAGE)).quantize(Decimal("0.0001"))
        if size_pct <= 0:
            size_pct = Decimal("0.0001")

        # Minimal FK graph for Position/Outcome (reuse the positions-repo factory).
        seed = await _seed(db_session)
        db_action = await db_session.get(DecisionAction, seed.action_id)
        assert db_action is not None
        # Align the seeded opening action with what we actually execute, so the
        # persisted Position (leverage/SL/TP/size) is consistent with the live order.
        db_action.leverage_executed = _LEVERAGE
        db_action.size_pct_executed = size_pct
        db_action.stop_loss_pct = _SL_PCT
        db_action.take_profit_pct = _TP_PCT
        await db_session.flush()

        long_action = ActionDecision(
            symbol=_SYMBOL,
            side=Side.LONG,
            leverage=_LEVERAGE,
            size_pct=size_pct,
            stop_loss_pct=_SL_PCT,
            take_profit_pct=_TP_PCT,
            entry_type=EntryType.MARKET,
            limit_price=None,
            confidence=Decimal("0.60"),
            time_horizon_min=120,
            action_reasoning="Testnet smoke: small LONG validating the real SDK round-trip.",
            action_key_signals=[],
        )

        # --- OPEN (real testnet order) -------------------------------------
        open_results = await client.execute_action(long_action, str(seed.opening_run_id), None)
        entry = next(o for o in open_results if o.order_kind == OrderKind.ENTRY)
        assert entry.filled_price is not None
        assert entry.filled_size_units is not None
        assert isinstance(entry.filled_price, Decimal)
        assert isinstance(entry.filled_size_units, Decimal)

        repo = PositionsRepository(db_session)
        entry_orders: list[OrderResult] = [
            o for o in open_results if o.order_kind != OrderKind.CLOSE
        ]
        position_id = await repo.open_position(
            str(seed.action_id), entry_orders, str(seed.opening_run_id)
        )
        await db_session.flush()

        open_pos = await db_session.get(Position, uuid.UUID(position_id))
        assert open_pos is not None
        assert open_pos.symbol == _SYMBOL  # identity = symbol (ADR-0016)

        # --- CLOSE (real testnet order, model-close path) ------------------
        current_summary = OpenPositionSummary(
            symbol=_SYMBOL,  # type: ignore[arg-type]
            side="LONG",
            entry_price=open_pos.entry_price,
            current_price=open_pos.entry_price,
            size_units=open_pos.size_units,
            leverage=open_pos.leverage,
            unrealized_pnl_usd=Decimal("0"),
            age_minutes=0,
        )
        close_results = await client.execute_action(
            _flat_action(_SYMBOL), str(seed.closing_run_id), current_summary
        )
        close_order = next(o for o in close_results if o.order_kind == OrderKind.CLOSE)
        assert close_order.filled_price is not None

        # Replicate the loop's realized-PnL computation for a model-close.
        side_mult = Decimal("1") if open_pos.side == "LONG" else Decimal("-1")
        realized_pnl = (
            (close_order.filled_price - open_pos.entry_price) * open_pos.size_units * side_mult
        )
        closure = PositionClosureInfo(
            closed_at=datetime.now(UTC).isoformat(),
            exit_price=close_order.filled_price,
            close_reason=CloseReason.MODEL_CLOSE,
            realized_pnl_usd=realized_pnl,
        )
        # Model-close bookkeeping (ADR-0027 + ADR-0030): a FLAT close is caused by a model
        # decision, so it carries a persisted FLAT closing action (distinct from the opening
        # one) + the CLOSE OrderResult we submitted above. Pass both — the conditional
        # chk_position_closed_consistency REQUIRES closing_action_id for model_close.
        closing_action_id = await _seed_flat_closing_action(db_session, seed)
        await repo.close_position(
            str(open_pos.id),
            closure,
            str(seed.closing_run_id),
            closing_action_id=str(closing_action_id),
            close_order=close_order,
        )
        await db_session.flush()

        # --- ASSERT outcome math (market-independent) ----------------------
        outcome = await db_session.scalar(select(Outcome).where(Outcome.position_id == open_pos.id))
        assert outcome is not None
        assert isinstance(outcome.pnl_net_fee_funding_usd, Decimal)
        assert outcome.pnl_net_fee_funding_usd.is_finite()
        # Strong, market-INDEPENDENT identity (this is the real teeth):
        assert outcome.pnl_net_fee_funding_usd == (
            outcome.realized_pnl_gross_usd - outcome.sum_fees_usd - outcome.sum_funding_usd
        )
        # fee is now reconciled from user_fills (finding A) ⇒ validate type/sign; a real
        # taker fee is positive, but stay venue-agnostic and assert non-negative only.
        assert isinstance(outcome.sum_fees_usd, Decimal)
        assert outcome.sum_fees_usd >= 0
        assert isinstance(outcome.sum_funding_usd, Decimal)
        assert outcome.holding_duration_min >= 0
        assert outcome.symbol == _SYMBOL  # identity = symbol (ADR-0016)

        await db_session.refresh(open_pos)
        assert open_pos.close_reason == CloseReason.MODEL_CLOSE.value

        # PnL plausibly tiny given the minuscule notional (a <100% move is certain
        # for an immediately-closed position).
        assert abs(outcome.realized_pnl_gross_usd) < open_pos.notional_value_usd
    finally:
        await _close_residual_positions(client)
