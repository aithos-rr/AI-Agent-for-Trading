"""E2E tests for guardrail clamping (PRD §9.5, M5-T11).

Verifies: when the LLM proposes size_pct=0.99 and leverage=30, the real
Guardrails class clamps values to max_size_pct=0.20 and hard_max_leverage≤10,
and the clamped flags are persisted correctly in decision_actions.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.config.settings import AgentSettings
from aiat.db.models.action import DecisionAction
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.experiment import Experiment
from aiat.db.models.model import Model
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from aiat.domain.enums import EntryType, Side
from aiat.domain.schemas import (
    ActionDecision,
    ContextBundle,
    CostEventData,
    LLMInvocationResult,
    NewsItem,
    OnChainSnapshot,
    PortfolioState,
    SentimentSnapshot,
    TechnicalIndicators,
    TradeDecision,
)
from aiat.orchestration.decision_loop import DecisionLoop

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TICK_ID = "2026-06-14T17:00:00+00:00"
_TICK_AT = datetime(2026, 6, 14, 17, 0, tzinfo=UTC)
_GIT_SHA = "guardrail-test-sha"
_SCHEMA_VERSION = "v1"
_PT_TEXT = (
    "You are a trading agent (guardrail-test template). "
    "Make decisions based on the market context provided."
)
_PT_HASH = hashlib.sha256(_PT_TEXT.encode()).hexdigest()
_PT_CONFIDENCE_DEF = "Probability that the action yields positive net PnL."

# LLM-proposed values that exceed guardrail limits
_RAW_SIZE_PCT = Decimal("0.99")
_RAW_LEVERAGE = Decimal("30")
_RAW_CONFIDENCE = Decimal("0.95")

# Expected post-guardrail values
# size_pct clamped to max_size_pct=0.20
_EXPECTED_SIZE_PCT = Decimal("0.20")
# leverage clamped to min(1 + 0.95×9, 10) = min(9.55, 10) = 9.55
_EXPECTED_LEVERAGE_CAP = Decimal("10")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_long_invocation() -> LLMInvocationResult:
    """LLM proposes LONG BTC with size_pct=0.99 and leverage=30 (exceeds all guardrails)."""
    long_action = ActionDecision(
        symbol="BTC",
        side=Side.LONG,
        leverage=_RAW_LEVERAGE,
        size_pct=_RAW_SIZE_PCT,
        stop_loss_pct=Decimal("0.0500"),
        take_profit_pct=Decimal("0.1000"),
        entry_type=EntryType.MARKET,
        confidence=_RAW_CONFIDENCE,
        time_horizon_min=60,
        action_reasoning=(
            "Strong breakout signal on BTC. RSI overbought but momentum confirms entry. "
            "High conviction LONG with max size."
        ),
        action_key_signals=[],
    )
    decision = TradeDecision(
        portfolio_reasoning=(
            "Clear bullish breakout on BTC with strong volume confirmation. "
            "ETH and SOL show no clear signal — holding flat. "
            "Risk is elevated but asymmetric upside justifies the position."
        ),
        risk_assessment=(
            "BTC position at max size is aggressive. Guardrails will enforce limits. "
            "ETH/SOL positions are flat — portfolio concentration risk is manageable."
        ),
        portfolio_confidence=_RAW_CONFIDENCE,
        actions=[
            long_action,
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
                action_reasoning="ETH: hold — no signal detected this tick.",
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
                action_reasoning="SOL: hold — no signal detected this tick.",
                action_key_signals=[],
            ),
        ],
    )
    return LLMInvocationResult(
        decision=decision,
        cost=CostEventData(
            input_tokens=900,
            output_tokens=200,
            reasoning_tokens=0,
            cost_usd=Decimal("0.00750000"),
            pricing_snapshot={"input": Decimal("5.00"), "output": Decimal("15.00")},
            n_attempts=1,
        ),
        latency_ms=1300,
        raw_response_id="resp-guardrail-long",
        raw_payload={"model": "gpt-4o"},
        fallback_used=False,
        provider_snapshot="openai",
        model_name_api_snapshot="gpt-4o",
        temperature=Decimal("0.7"),
        top_p=None,
        max_tokens=4096,
        seed=None,
    )


def _make_context_bundle() -> ContextBundle:
    tech = TechnicalIndicators(
        symbol="BTC",
        price_usd=Decimal("66000"),
        rsi_14=Decimal("72"),
        macd_signal_diff=Decimal("150"),
        ema_20=Decimal("64500"),
        ema_50=Decimal("63000"),
        bollinger_upper=Decimal("68000"),
        bollinger_lower=Decimal("62000"),
        atr_14=Decimal("450"),
        volume_24h_usd=Decimal("1200000000"),
    )
    return ContextBundle(
        tick_id=_TICK_ID,
        tick_at=_TICK_ID,
        technical=[
            tech,
            tech.model_copy(update={"symbol": "ETH", "price_usd": Decimal("3600")}),
            tech.model_copy(update={"symbol": "SOL", "price_usd": Decimal("185")}),
        ],
        sentiment=SentimentSnapshot(
            fear_greed_index=72,
            fear_greed_label="greed",
            fetched_at=_TICK_ID,
        ),
        news=[
            NewsItem(
                title="BTC breaks resistance",
                summary="Bitcoin breaks key resistance with strong volume",
                source="coindesk",
                published_at=_TICK_ID,
            )
        ],
        onchain=[
            OnChainSnapshot(
                symbol="BTC",
                funding_rate_8h=Decimal("0.0003"),
                open_interest_usd=Decimal("12000000"),
                premium=Decimal("0.002"),
                liquidations_24h_usd=Decimal("800000"),
            )
        ],
        source_timestamps={"technical_btc": _TICK_ID},
    )


@pytest_asyncio.fixture(scope="function")
async def guardrail_session_factory(db_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def guardrail_seed(
    guardrail_session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    """Seed experiment, model, prompt_template, and context_snapshot."""
    exp_id = uuid.uuid4()
    snap_id = uuid.uuid4()
    model_id = f"openai-gpt4o-gr-{uuid.uuid4().hex[:6]}"
    context_bundle = _make_context_bundle()

    async with guardrail_session_factory() as session:
        session.add(
            Experiment(
                id=exp_id,
                name=f"guardrail-test-{exp_id.hex[:8]}",
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

        await session.execute(
            pg_insert(PromptTemplate)
            .values(
                sha256_hash=_PT_HASH,
                label="guardrail-pt-shared",
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
                context_hash=hashlib.sha256(b"guardrail-test").hexdigest(),
                context_json=context_bundle.model_dump(mode="json"),
                source_timestamps={},
                build_duration_ms=20,
            )
        )
        await session.commit()

    return {
        "experiment_id": str(exp_id),
        "snapshot_id": str(snap_id),
        "model_id": model_id,
        "prompt_template_hash": _PT_HASH,
    }


def _make_settings(seed: dict[str, str]) -> AgentSettings:
    return AgentSettings(  # type: ignore[call-arg]
        experiment_id=seed["experiment_id"],
        git_commit_sha=_GIT_SHA,
        database_url="postgresql+asyncpg://x:x@localhost/x",
        network="testnet",
        service_role="agent",
        model_id=seed["model_id"],
        prompt_template_hash=seed["prompt_template_hash"],
        schema_version=_SCHEMA_VERSION,
        llm_provider="openai",
        model_name_api="gpt-4o",
        openai_api_key="sk-test",
        hl_wallet_private_key="0x" + "0" * 64,
        hl_wallet_address="0x" + "0" * 40,
        llm_gateway="direct",
        max_size_pct=Decimal("0.20"),
        hard_max_leverage=Decimal("10"),
        min_open_confidence=Decimal("0.40"),
        hard_timeout_seconds=180,
    )


def _stub_llm(invocation: LLMInvocationResult) -> AsyncMock:
    client = AsyncMock()
    client.invoke = AsyncMock(return_value=invocation)
    return client


def _stub_hl() -> AsyncMock:
    client = AsyncMock()
    client.fetch_portfolio_state = AsyncMock(
        return_value=PortfolioState(
            equity_usd=Decimal("10000"),
            available_usd=Decimal("10000"),
            margin_used_usd=Decimal("0"),
            n_open_positions=0,
            unrealized_pnl_usd=Decimal("0"),
            open_positions=[],
        )
    )
    client.execute_action = AsyncMock(return_value=[])
    client.check_position_closure = AsyncMock(return_value=None)
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGuardrailE2E:
    @pytest.mark.asyncio
    async def test_size_pct_clamped_to_max(
        self,
        guardrail_session_factory: async_sessionmaker[AsyncSession],
        guardrail_seed: dict[str, str],
    ) -> None:
        """size_pct=0.99 proposed by LLM must be clamped to 0.20 in decision_actions."""
        settings = _make_settings(guardrail_seed)
        loop = DecisionLoop(
            settings=settings,
            llm_client=_stub_llm(_make_long_invocation()),
            hl_client=_stub_hl(),
            session_factory=guardrail_session_factory,
            # guardrails=None → real Guardrails() instance used
        )

        run_id = await loop.run_once(_TICK_ID, _TICK_AT)
        assert run_id is not None

        async with guardrail_session_factory() as session:
            run = await session.get(Run, uuid.UUID(run_id))
            assert run is not None

            btc_action = (
                await session.scalars(
                    select(DecisionAction).where(
                        DecisionAction.run_id == uuid.UUID(run_id),
                        DecisionAction.symbol == "BTC",
                    )
                )
            ).first()
            assert btc_action is not None

            assert btc_action.size_pct_executed == _EXPECTED_SIZE_PCT, (
                f"Expected size_pct_executed={_EXPECTED_SIZE_PCT}, "
                f"got {btc_action.size_pct_executed}"
            )
            assert btc_action.size_pct_clamped is True, (
                "size_pct_clamped flag must be True when size_pct was clamped"
            )
            # Original requested value preserved
            assert btc_action.size_pct_requested == _RAW_SIZE_PCT

    @pytest.mark.asyncio
    async def test_leverage_clamped_to_hard_cap(
        self,
        guardrail_session_factory: async_sessionmaker[AsyncSession],
        guardrail_seed: dict[str, str],
    ) -> None:
        """leverage=30 proposed by LLM must be clamped to ≤10 in decision_actions."""
        settings = _make_settings(guardrail_seed)
        loop = DecisionLoop(
            settings=settings,
            llm_client=_stub_llm(_make_long_invocation()),
            hl_client=_stub_hl(),
            session_factory=guardrail_session_factory,
        )

        run_id = await loop.run_once(_TICK_ID, _TICK_AT)
        assert run_id is not None

        async with guardrail_session_factory() as session:
            btc_action = (
                await session.scalars(
                    select(DecisionAction).where(
                        DecisionAction.run_id == uuid.UUID(run_id),
                        DecisionAction.symbol == "BTC",
                    )
                )
            ).first()
            assert btc_action is not None

            assert btc_action.leverage_executed <= _EXPECTED_LEVERAGE_CAP, (
                f"Expected leverage_executed ≤ {_EXPECTED_LEVERAGE_CAP}, "
                f"got {btc_action.leverage_executed}"
            )
            assert btc_action.leverage_clamped is True, (
                "leverage_clamped flag must be True when leverage was clamped"
            )
            # Original requested value preserved
            assert btc_action.leverage_requested == _RAW_LEVERAGE

    @pytest.mark.asyncio
    async def test_both_flags_set_simultaneously(
        self,
        guardrail_session_factory: async_sessionmaker[AsyncSession],
        guardrail_seed: dict[str, str],
    ) -> None:
        """Both size_pct_clamped and leverage_clamped must be True in the same row."""
        settings = _make_settings(guardrail_seed)
        loop = DecisionLoop(
            settings=settings,
            llm_client=_stub_llm(_make_long_invocation()),
            hl_client=_stub_hl(),
            session_factory=guardrail_session_factory,
        )

        run_id = await loop.run_once(_TICK_ID, _TICK_AT)
        assert run_id is not None

        async with guardrail_session_factory() as session:
            btc_action = (
                await session.scalars(
                    select(DecisionAction).where(
                        DecisionAction.run_id == uuid.UUID(run_id),
                        DecisionAction.symbol == "BTC",
                    )
                )
            ).first()
            assert btc_action is not None

            assert btc_action.size_pct_clamped is True
            assert btc_action.leverage_clamped is True
            assert btc_action.size_pct_executed == _EXPECTED_SIZE_PCT
            assert btc_action.leverage_executed <= _EXPECTED_LEVERAGE_CAP

    @pytest.mark.asyncio
    async def test_hold_actions_not_clamped(
        self,
        guardrail_session_factory: async_sessionmaker[AsyncSession],
        guardrail_seed: dict[str, str],
    ) -> None:
        """HOLD actions (ETH, SOL) must not have clamping flags set."""
        settings = _make_settings(guardrail_seed)
        loop = DecisionLoop(
            settings=settings,
            llm_client=_stub_llm(_make_long_invocation()),
            hl_client=_stub_hl(),
            session_factory=guardrail_session_factory,
        )

        run_id = await loop.run_once(_TICK_ID, _TICK_AT)
        assert run_id is not None

        async with guardrail_session_factory() as session:
            for symbol in ("ETH", "SOL"):
                action = (
                    await session.scalars(
                        select(DecisionAction).where(
                            DecisionAction.run_id == uuid.UUID(run_id),
                            DecisionAction.symbol == symbol,
                        )
                    )
                ).first()
                assert action is not None, f"Missing decision_action for {symbol}"
                assert action.size_pct_clamped is False, (
                    f"{symbol}: size_pct_clamped should be False for HOLD"
                )
                assert action.leverage_clamped is False, (
                    f"{symbol}: leverage_clamped should be False for HOLD"
                )
                assert action.forced_hold is False, (
                    f"{symbol}: forced_hold should be False — HOLD was already requested"
                )
