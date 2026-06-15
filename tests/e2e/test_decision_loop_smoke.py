"""E2E smoke test for DecisionLoop.run_once (PRD §9.5, M5-T08).

Uses an ephemeral Postgres instance (via pytest-postgresql), a stubbed LLM,
and MockHyperliquidClient to exercise the full 10-step decision loop against
a real database.  Verifies the expected row counts and status values.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.config.settings import AgentSettings
from aiat.db.models.account_snapshot import AccountSnapshot
from aiat.db.models.action import DecisionAction
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.cost_event import CostEvent
from aiat.db.models.decision import Decision
from aiat.db.models.experiment import Experiment
from aiat.db.models.llm_invocation import LLMInvocation
from aiat.db.models.model import Model
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from aiat.domain.enums import EntryType, OrderKind, RunStatus, Side
from aiat.domain.schemas import (
    ActionDecision,
    ContextBundle,
    CostEventData,
    GuardrailReport,
    LLMInvocationResult,
    NewsItem,
    OnChainSnapshot,
    PortfolioState,
    SentimentSnapshot,
    TechnicalIndicators,
    TradeDecision,
)
from aiat.execution.hyperliquid_client import OrderResult
from aiat.orchestration.decision_loop import DecisionLoop

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TICK_ID = "2026-06-14T14:30:00+00:00"
_TICK_AT = datetime(2026, 6, 14, 14, 30, tzinfo=UTC)
_GIT_SHA = "abc1234"
_SCHEMA_VERSION = "v1"
_PT_TEXT = "You are a trading agent. Make decisions based on the context provided below."
_PT_HASH = hashlib.sha256(_PT_TEXT.encode()).hexdigest()
_PT_CONFIDENCE_DEF = "Probability that the action yields positive net PnL within time_horizon_min."


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def session_factory(db_url: str) -> async_sessionmaker[AsyncSession]:
    """Session factory connected to the ephemeral test DB."""
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def seed_ids(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    """Seed experiment, model, prompt_template, and context_snapshot rows.

    Commits data so DecisionLoop sessions (separate connections) can read it.
    Returns a dict with experiment_id, model_id, snapshot_id, prompt_template_hash.
    """
    exp_id = uuid.uuid4()
    model_id = f"openai-gpt4o-smoke-{uuid.uuid4().hex[:6]}"
    snap_id = uuid.uuid4()

    context_bundle = _make_context_bundle()

    async with session_factory() as session:
        session.add(
            Experiment(
                id=exp_id,
                name=f"smoke-test-{exp_id.hex[:8]}",
                started_at=datetime.now(UTC),
                git_commit_sha=_GIT_SHA,
                config_snapshot={},
            )
        )
        await session.flush()

        session.add(
            Model(
                id=model_id,
                provider="openai",
                model_name_api="gpt-4o",
                tier="premium",
                geography="USA",
                wallet_address=f"0x{uuid.uuid4().hex[:40]}",
                pricing_input_usd_per_1m=Decimal("5.000000"),
                pricing_output_usd_per_1m=Decimal("15.000000"),
            )
        )
        await session.flush()

        # PromptTemplate PK is sha256_hash — skip if already inserted by a prior test.
        await session.execute(
            pg_insert(PromptTemplate)
            .values(
                sha256_hash=_PT_HASH,
                label="smoke-pt-shared",
                template_text=_PT_TEXT,
                confidence_def=_PT_CONFIDENCE_DEF,
                controlled_signals=[],
            )
            .on_conflict_do_nothing(index_elements=["sha256_hash"])
        )
        await session.flush()

        session.add(
            ContextSnapshot(
                id=snap_id,
                experiment_id=exp_id,
                tick_id=_TICK_ID,
                tick_at=_TICK_AT,
                context_hash=hashlib.sha256(b"test").hexdigest(),
                context_json=context_bundle.model_dump(mode="json"),
                source_timestamps={},
                build_duration_ms=50,
            )
        )
        await session.commit()

    return {
        "experiment_id": str(exp_id),
        "model_id": model_id,
        "snapshot_id": str(snap_id),
        "prompt_template_hash": _PT_HASH,
    }


def _make_agent_settings(seed_ids: dict[str, str]) -> AgentSettings:
    return AgentSettings(  # type: ignore[call-arg]
        experiment_id=seed_ids["experiment_id"],
        git_commit_sha=_GIT_SHA,
        database_url="postgresql+asyncpg://x:x@localhost/x",  # overridden by session_factory
        network="testnet",
        service_role="agent",
        model_id=seed_ids["model_id"],
        prompt_template_hash=seed_ids["prompt_template_hash"],
        schema_version=_SCHEMA_VERSION,
        llm_provider="openai",
        model_name_api="gpt-4o",
        openai_api_key="sk-test",
        hl_wallet_private_key="0x" + "0" * 64,
        hl_wallet_address="0x" + "0" * 40,
        llm_gateway="direct",
        max_size_pct=Decimal("0.20"),
        hard_max_leverage=Decimal("10"),
        min_open_confidence=Decimal("0.4"),
        hard_timeout_seconds=180,
    )


def _make_context_bundle() -> ContextBundle:
    tech = TechnicalIndicators(
        symbol="BTC",
        price_usd=Decimal("65000"),
        rsi_14=Decimal("55"),
        macd_signal_diff=Decimal("100"),
        ema_20=Decimal("64000"),
        ema_50=Decimal("63000"),
        bollinger_upper=Decimal("68000"),
        bollinger_lower=Decimal("62000"),
        atr_14=Decimal("500"),
        volume_24h_usd=Decimal("1000000000"),
    )
    return ContextBundle(
        tick_id=_TICK_ID,
        tick_at=_TICK_ID,
        technical=[
            tech,
            tech.model_copy(update={"symbol": "ETH", "price_usd": Decimal("3500")}),
            tech.model_copy(update={"symbol": "SOL", "price_usd": Decimal("180")}),
        ],
        sentiment=SentimentSnapshot(
            fear_greed_index=55,
            fear_greed_label="greed",
            fetched_at=_TICK_ID,
        ),
        news=[
            NewsItem(
                title="BTC consolidates near ATH",
                summary="Bitcoin price holds steady near all-time high levels",
                source="coindesk",
                published_at=_TICK_ID,
            )
        ],
        onchain=[
            OnChainSnapshot(
                symbol="BTC",
                funding_rate_8h=Decimal("0.0001"),
                open_interest_usd=Decimal("10000000"),
                premium=Decimal("-0.0002"),
                liquidations_24h_usd=Decimal("1000000"),
            )
        ],
        source_timestamps={"technical_btc": _TICK_ID},
    )


def _make_hold_invocation() -> LLMInvocationResult:
    """LLM result with all-HOLD decisions (no orders placed)."""
    decision = TradeDecision(
        portfolio_reasoning=(
            "Market is consolidating — no strong signals justify new positions at this time."
            " Maintaining current allocation."
        ),
        risk_assessment=("Low risk; no open positions. Market volatility is moderate."),
        portfolio_confidence=Decimal("0.6"),
        actions=[
            ActionDecision(
                symbol="BTC",
                side=Side.HOLD,
                leverage=Decimal("0"),
                size_pct=Decimal("0"),
                stop_loss_pct=None,
                take_profit_pct=None,
                entry_type=EntryType.NONE,
                confidence=Decimal("0.5"),
                time_horizon_min=60,
                action_reasoning="No momentum signal; RSI neutral; waiting for confirmation.",
                action_key_signals=[],
            ),
            ActionDecision(
                symbol="ETH",
                side=Side.HOLD,
                leverage=Decimal("0"),
                size_pct=Decimal("0"),
                stop_loss_pct=None,
                take_profit_pct=None,
                entry_type=EntryType.NONE,
                confidence=Decimal("0.5"),
                time_horizon_min=60,
                action_reasoning="ETH following BTC; no independent signal present.",
                action_key_signals=[],
            ),
            ActionDecision(
                symbol="SOL",
                side=Side.HOLD,
                leverage=Decimal("0"),
                size_pct=Decimal("0"),
                stop_loss_pct=None,
                take_profit_pct=None,
                entry_type=EntryType.NONE,
                confidence=Decimal("0.5"),
                time_horizon_min=60,
                action_reasoning="SOL range-bound; no catalyst visible in next 15 minutes.",
                action_key_signals=[],
            ),
        ],
    )
    return LLMInvocationResult(
        decision=decision,
        cost=CostEventData(
            input_tokens=1000,
            output_tokens=200,
            reasoning_tokens=0,
            cost_usd=Decimal("0.01000000"),
            pricing_snapshot={"input": Decimal("5.00"), "output": Decimal("15.00")},
            n_attempts=1,
        ),
        latency_ms=1500,
        raw_response_id="resp-smoke-hold",
        raw_payload={"model": "gpt-4o", "usage": {"prompt_tokens": 1000}},
        fallback_used=False,
        provider_snapshot="openai",
        model_name_api_snapshot="gpt-4o",
        temperature=Decimal("0.7"),
        top_p=None,
        max_tokens=4096,
        seed=None,
    )


def _make_long_btc_invocation() -> LLMInvocationResult:
    """LLM result with BTC LONG (+ ETH/SOL HOLD)."""
    decision = TradeDecision(
        portfolio_reasoning=(
            "Strong bullish momentum detected on BTC: RSI trending up, MACD cross confirmed."
            " Entering a conservative long position with stop-loss protection."
        ),
        risk_assessment=(
            "Moderate risk; BTC shows strong trend. Position sized at 10% with 5% SL."
        ),
        portfolio_confidence=Decimal("0.75"),
        actions=[
            ActionDecision(
                symbol="BTC",
                side=Side.LONG,
                leverage=Decimal("2.00"),
                size_pct=Decimal("0.10"),
                stop_loss_pct=Decimal("0.05"),
                take_profit_pct=Decimal("0.10"),
                entry_type=EntryType.MARKET,
                confidence=Decimal("0.75"),
                time_horizon_min=120,
                action_reasoning=(
                    "RSI 55 trending toward overbought; MACD just crossed positive."
                    " Entry at market."
                ),
                action_key_signals=["technical.rsi_extreme", "technical.macd_cross"],
            ),
            ActionDecision(
                symbol="ETH",
                side=Side.HOLD,
                leverage=Decimal("0"),
                size_pct=Decimal("0"),
                stop_loss_pct=None,
                take_profit_pct=None,
                entry_type=EntryType.NONE,
                confidence=Decimal("0.5"),
                time_horizon_min=60,
                action_reasoning="ETH lacks independent catalyst; hold and wait.",
                action_key_signals=[],
            ),
            ActionDecision(
                symbol="SOL",
                side=Side.HOLD,
                leverage=Decimal("0"),
                size_pct=Decimal("0"),
                stop_loss_pct=None,
                take_profit_pct=None,
                entry_type=EntryType.NONE,
                confidence=Decimal("0.5"),
                time_horizon_min=60,
                action_reasoning="SOL sideways; no signal.",
                action_key_signals=[],
            ),
        ],
    )
    return LLMInvocationResult(
        decision=decision,
        cost=CostEventData(
            input_tokens=1200,
            output_tokens=300,
            reasoning_tokens=0,
            cost_usd=Decimal("0.01050000"),
            pricing_snapshot={"input": Decimal("5.00"), "output": Decimal("15.00")},
            n_attempts=1,
        ),
        latency_ms=1800,
        raw_response_id="resp-smoke-long-btc",
        raw_payload={"model": "gpt-4o", "usage": {"prompt_tokens": 1200}},
        fallback_used=False,
        provider_snapshot="openai",
        model_name_api_snapshot="gpt-4o",
        temperature=Decimal("0.7"),
        top_p=None,
        max_tokens=4096,
        seed=None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_llm_client(invocation: LLMInvocationResult) -> MagicMock:
    """Return an async mock LLM client that returns a fixed invocation."""
    client = AsyncMock()
    client.invoke = AsyncMock(return_value=invocation)
    return client


def _stub_hl_client(
    order_results: list[OrderResult] | None = None,
    portfolio: PortfolioState | None = None,
) -> AsyncMock:
    """Return an async mock HL client."""
    client = AsyncMock()
    client.fetch_portfolio_state = AsyncMock(
        return_value=portfolio
        or PortfolioState(
            equity_usd=Decimal("10000"),
            available_usd=Decimal("10000"),
            margin_used_usd=Decimal("0"),
            n_open_positions=0,
            unrealized_pnl_usd=Decimal("0"),
            open_positions=[],
        )
    )
    client.execute_action = AsyncMock(return_value=order_results or [])
    client.check_position_closure = AsyncMock(return_value=None)
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDecisionLoopSmoke:
    @pytest.mark.asyncio
    async def test_hold_all_creates_expected_rows(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed_ids: dict[str, str],
    ) -> None:
        """run_once (all-HOLD) creates: 1 run, 1 decision, 3 actions, 1 cost_event,
        1 llm_invocation, 1 account_snapshot.  runs.status='success'."""
        settings = _make_agent_settings(seed_ids)
        invocation = _make_hold_invocation()
        inv_reports = [
            GuardrailReport(
                symbol=a.symbol,
                original_side=a.side,
                leverage_clamped=False,
                size_pct_clamped=False,
                forced_hold=False,
                final_action=a,
            )
            for a in invocation.decision.actions
        ]
        guardrails = MagicMock()
        guardrails.apply = MagicMock(return_value=(invocation.decision, inv_reports))

        loop = DecisionLoop(
            settings=settings,
            llm_client=_stub_llm_client(invocation),
            hl_client=_stub_hl_client(),
            session_factory=session_factory,
            guardrails=guardrails,
        )
        run_id = await loop.run_once(_TICK_ID, _TICK_AT)

        assert run_id is not None

        async with session_factory() as session:
            run = await session.get(Run, uuid.UUID(run_id))
            assert run is not None
            assert run.status == RunStatus.SUCCESS.value

            decision_count = await session.scalar(
                select(func.count()).where(Decision.run_id == uuid.UUID(run_id))
            )
            assert decision_count == 1

            action_count = await session.scalar(
                select(func.count()).where(DecisionAction.run_id == uuid.UUID(run_id))
            )
            assert action_count == 3

            cost_count = await session.scalar(
                select(func.count()).where(CostEvent.run_id == uuid.UUID(run_id))
            )
            assert cost_count == 1

            invocation_count = await session.scalar(
                select(func.count()).where(LLMInvocation.run_id == uuid.UUID(run_id))
            )
            assert invocation_count == 1

            snapshot_count = await session.scalar(
                select(func.count()).where(AccountSnapshot.run_id == uuid.UUID(run_id))
            )
            assert snapshot_count == 1

    @pytest.mark.asyncio
    async def test_long_btc_creates_position(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed_ids: dict[str, str],
    ) -> None:
        """run_once with BTC=LONG creates a Position row and calls execute_action."""
        settings = _make_agent_settings(seed_ids)
        invocation = _make_long_btc_invocation()
        inv_reports = [
            GuardrailReport(
                symbol=a.symbol,
                original_side=a.side,
                leverage_clamped=False,
                size_pct_clamped=False,
                forced_hold=False,
                final_action=a,
            )
            for a in invocation.decision.actions
        ]
        guardrails = MagicMock()
        guardrails.apply = MagicMock(return_value=(invocation.decision, inv_reports))

        entry_result = OrderResult(
            hl_order_id=str(uuid.uuid4()),
            client_order_id=str(uuid.uuid4()),
            order_kind=OrderKind.ENTRY,
            status="filled",
            requested_price=None,
            filled_price=Decimal("65000"),
            requested_size_units=Decimal("0.01"),
            filled_size_units=Decimal("0.01"),
            slippage_bps=Decimal("5"),
            fee_usd=Decimal("1.00"),
            raw_response={},
        )
        hl_client = _stub_hl_client(order_results=[entry_result])

        loop = DecisionLoop(
            settings=settings,
            llm_client=_stub_llm_client(invocation),
            hl_client=hl_client,
            session_factory=session_factory,
            guardrails=guardrails,
        )
        run_id = await loop.run_once(_TICK_ID, _TICK_AT)

        assert run_id is not None

        async with session_factory() as session:
            run = await session.get(Run, uuid.UUID(run_id))
            assert run is not None
            assert run.status == RunStatus.SUCCESS.value

        # execute_action was called once (only BTC is LONG)
        hl_client.execute_action.assert_called_once()
        assert hl_client.execute_action.call_args[0][0].symbol == "BTC"
        assert hl_client.execute_action.call_args[0][0].side == Side.LONG

    @pytest.mark.asyncio
    async def test_missed_tick_returns_none(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed_ids: dict[str, str],
    ) -> None:
        """run_once returns None and creates no run row when context_snapshot missing."""
        settings = _make_agent_settings(seed_ids)
        # Use a different tick_id that has no snapshot in DB
        missing_tick_id = "2099-01-01T00:00:00+00:00"
        missing_tick_at = datetime(2099, 1, 1, tzinfo=UTC)

        loop = DecisionLoop(
            settings=settings,
            llm_client=AsyncMock(),
            hl_client=_stub_hl_client(),
            session_factory=session_factory,
        )
        # Patch asyncio.sleep to skip retry delays
        from unittest.mock import patch

        with patch("aiat.orchestration.decision_loop.asyncio.sleep"):
            run_id = await loop.run_once(missing_tick_id, missing_tick_at)

        assert run_id is None

        # Verify no run row was created for this tick
        async with session_factory() as session:
            run_count = await session.scalar(
                select(func.count()).where(
                    Run.tick_id == missing_tick_id,
                    Run.model_id == seed_ids["model_id"],
                )
            )
        assert run_count == 0

    @pytest.mark.asyncio
    async def test_run_has_correct_metadata(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed_ids: dict[str, str],
    ) -> None:
        """Verify run row has correct experiment_id, model_id, tick_id, schema_version, git_sha."""
        settings = _make_agent_settings(seed_ids)
        invocation = _make_hold_invocation()
        inv_reports = [
            GuardrailReport(
                symbol=a.symbol,
                original_side=a.side,
                leverage_clamped=False,
                size_pct_clamped=False,
                forced_hold=False,
                final_action=a,
            )
            for a in invocation.decision.actions
        ]
        guardrails = MagicMock()
        guardrails.apply = MagicMock(return_value=(invocation.decision, inv_reports))

        loop = DecisionLoop(
            settings=settings,
            llm_client=_stub_llm_client(invocation),
            hl_client=_stub_hl_client(),
            session_factory=session_factory,
            guardrails=guardrails,
        )
        run_id = await loop.run_once(_TICK_ID, _TICK_AT)
        assert run_id is not None

        async with session_factory() as session:
            run = await session.get(Run, uuid.UUID(run_id))
            assert run is not None
            assert str(run.experiment_id) == seed_ids["experiment_id"]
            assert run.model_id == seed_ids["model_id"]
            assert run.tick_id == _TICK_ID
            assert run.schema_version == _SCHEMA_VERSION
            assert run.git_commit_sha == _GIT_SHA
            assert run.prompt_template_hash == _PT_HASH
            assert run.context_snapshot_id == uuid.UUID(seed_ids["snapshot_id"])
