"""E2E tests for market context parity — invariant #13 (PRD §9.5, M5-T10).

Verifies: 4 agents sharing the same tick_id all reference the same
context_snapshot_id and the same context_hash (byte-identical market context).
portfolio_state_hash in account_snapshots diverges correctly because each
agent has a distinct wallet state (market parity vs portfolio independence).
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.config.settings import AgentSettings
from aiat.db.models.account_snapshot import AccountSnapshot
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
    GuardrailReport,
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

_TICK_ID = "2026-06-14T16:00:00+00:00"
_TICK_AT = datetime(2026, 6, 14, 16, 0, tzinfo=UTC)
_GIT_SHA = "parity-test-sha"
_SCHEMA_VERSION = "v1"
_PT_TEXT = (
    "You are a trading agent (parity-test template). "
    "Make decisions based on the market context provided."
)
_PT_HASH = hashlib.sha256(_PT_TEXT.encode()).hexdigest()
_PT_CONFIDENCE_DEF = "Probability that the action yields positive net PnL."

# 4 model IDs mirroring the experiment configuration
_MODEL_IDS = [
    "openai-gpt4o-parity",
    "anthropic-claude3-parity",
    "deepseek-v3-parity",
    "qwen-72b-parity",
]

# Distinct equity values per model so portfolio_state_hash diverges
_EQUITIES = [
    Decimal("10000"),
    Decimal("10500"),
    Decimal("11000"),
    Decimal("11500"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hold_invocation() -> LLMInvocationResult:
    decision = TradeDecision(
        portfolio_reasoning=(
            "No clear signal this tick. Holding flat across all symbols."
            " Risk is low; market is ranging."
        ),
        risk_assessment="Low risk. No open positions. Range-bound conditions.",
        portfolio_confidence=Decimal("0.55"),
        actions=[
            ActionDecision(
                symbol=sym,
                side=Side.HOLD,
                leverage=Decimal("0"),
                size_pct=Decimal("0"),
                stop_loss_pct=None,
                take_profit_pct=None,
                entry_type=EntryType.NONE,
                confidence=Decimal("0.5"),
                time_horizon_min=60,
                action_reasoning=f"{sym}: hold — no signal detected.",
                action_key_signals=[],
            )
            for sym in ("BTC", "ETH", "SOL")
        ],
    )
    return LLMInvocationResult(
        decision=decision,
        cost=CostEventData(
            input_tokens=800,
            output_tokens=150,
            reasoning_tokens=0,
            cost_usd=Decimal("0.00625000"),
            pricing_snapshot={"input": Decimal("5.00"), "output": Decimal("15.00")},
            n_attempts=1,
        ),
        latency_ms=1100,
        raw_response_id="resp-parity-hold",
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
        price_usd=Decimal("65000"),
        rsi_14=Decimal("52"),
        macd_signal_diff=Decimal("40"),
        ema_20=Decimal("64000"),
        ema_50=Decimal("63000"),
        bollinger_upper=Decimal("68000"),
        bollinger_lower=Decimal("62000"),
        atr_14=Decimal("420"),
        volume_24h_usd=Decimal("950000000"),
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
            fear_greed_index=52,
            fear_greed_label="neutral",
            fetched_at=_TICK_ID,
        ),
        news=[
            NewsItem(
                title="Crypto markets stable",
                summary="Markets consolidate without clear direction",
                source="coindesk",
                published_at=_TICK_ID,
            )
        ],
        onchain=[
            OnChainSnapshot(
                symbol="BTC",
                funding_rate_8h=Decimal("0.0001"),
                open_interest_usd=Decimal("9500000"),
                premium=Decimal("0.001"),
                liquidations_24h_usd=Decimal("600000"),
            )
        ],
        source_timestamps={"technical_btc": _TICK_ID},
    )


@pytest_asyncio.fixture(scope="function")
async def parity_session_factory(db_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def parity_seed(
    parity_session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    """Seed experiment, 4 models, shared prompt_template, 1 context_snapshot.

    Returns experiment_id, snapshot_id, prompt_template_hash, and model IDs.
    """
    exp_id = uuid.uuid4()
    snap_id = uuid.uuid4()
    context_bundle = _make_context_bundle()
    context_hash = hashlib.sha256(context_bundle.model_dump_json().encode()).hexdigest()

    # Unique model IDs per test run to avoid collisions across parallel test sessions
    suffix = uuid.uuid4().hex[:6]
    model_ids = [f"{m}-{suffix}" for m in _MODEL_IDS]

    async with parity_session_factory() as session:
        session.add(
            Experiment(
                id=exp_id,
                name=f"parity-test-{exp_id.hex[:8]}",
                started_at=datetime.now(UTC),
                git_commit_sha=_GIT_SHA,
                config_snapshot={},
            )
        )
        await session.flush()

        for i, mid in enumerate(model_ids):
            session.add(
                Model(
                    id=mid,
                    provider=["openai", "anthropic", "deepseek", "qwen"][i],
                    model_name_api=["gpt-4o", "claude-3-5-sonnet", "deepseek-v3", "qwen-72b"][i],
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
                label="parity-pt-shared",
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
                context_hash=context_hash,
                context_json=context_bundle.model_dump(mode="json"),
                source_timestamps={},
                build_duration_ms=25,
            )
        )
        await session.commit()

    return {
        "experiment_id": str(exp_id),
        "snapshot_id": str(snap_id),
        "prompt_template_hash": _PT_HASH,
        "context_hash": context_hash,
        "model_ids": model_ids,
    }


def _make_settings(seed: dict[str, str], model_id: str) -> AgentSettings:
    return AgentSettings(  # type: ignore[call-arg]
        experiment_id=seed["experiment_id"],
        git_commit_sha=_GIT_SHA,
        database_url="postgresql+asyncpg://x:x@localhost/x",
        network="testnet",
        service_role="agent",
        model_id=model_id,
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
        min_open_confidence=Decimal("0.4"),
        hard_timeout_seconds=180,
    )


def _stub_llm() -> AsyncMock:
    client = AsyncMock()
    client.invoke = AsyncMock(side_effect=lambda *_a, **_kw: _make_hold_invocation())
    return client


def _stub_hl(equity: Decimal) -> AsyncMock:
    """Return a HL stub with distinct equity so portfolio_state_hash diverges."""
    client = AsyncMock()
    client.fetch_portfolio_state = AsyncMock(
        return_value=PortfolioState(
            equity_usd=equity,
            available_usd=equity,
            margin_used_usd=Decimal("0"),
            n_open_positions=0,
            unrealized_pnl_usd=Decimal("0"),
            open_positions=[],
        )
    )
    client.execute_action = AsyncMock(return_value=[])
    client.check_position_closure = AsyncMock(return_value=None)
    return client


async def _run_agent(
    settings: AgentSettings,
    equity: Decimal,
    session_factory: async_sessionmaker[AsyncSession],
) -> str | None:
    invocation = _make_hold_invocation()
    reports = [
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
    guardrails.apply = MagicMock(return_value=(invocation.decision, reports))

    loop = DecisionLoop(
        settings=settings,
        llm_client=_stub_llm(),
        hl_client=_stub_hl(equity),
        session_factory=session_factory,
        guardrails=guardrails,
    )
    return await loop.run_once(_TICK_ID, _TICK_AT)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestContextParity:
    @pytest.mark.asyncio
    @pytest.mark.invariant("13")
    async def test_four_agents_share_context_snapshot_id(
        self,
        parity_session_factory: async_sessionmaker[AsyncSession],
        parity_seed: dict[str, str],
    ) -> None:
        """All 4 agents for the same tick must share context_snapshot_id (inv #13)."""
        model_ids: list[str] = parity_seed["model_ids"]
        expected_snap_id = parity_seed["snapshot_id"]

        run_ids = await asyncio.gather(
            *[
                _run_agent(
                    _make_settings(parity_seed, mid),
                    equity,
                    parity_session_factory,
                )
                for mid, equity in zip(model_ids, _EQUITIES, strict=True)
            ]
        )

        assert all(rid is not None for rid in run_ids), (
            f"Some agents returned None run_id: {run_ids}"
        )

        async with parity_session_factory() as session:
            for run_id in run_ids:
                run = await session.get(Run, uuid.UUID(run_id))  # type: ignore[arg-type]
                assert run is not None
                assert str(run.context_snapshot_id) == expected_snap_id, (
                    f"Run {run_id} has context_snapshot_id={run.context_snapshot_id!r}, "
                    f"expected {expected_snap_id!r}"
                )

    @pytest.mark.asyncio
    @pytest.mark.invariant("13")
    async def test_context_hash_byte_identical_across_models(
        self,
        parity_session_factory: async_sessionmaker[AsyncSession],
        parity_seed: dict[str, str],
    ) -> None:
        """The context_hash of the shared snapshot is byte-identical for all 4 agents (inv #13)."""
        model_ids: list[str] = parity_seed["model_ids"]
        expected_context_hash = parity_seed["context_hash"]
        expected_snap_id = parity_seed["snapshot_id"]

        run_ids = await asyncio.gather(
            *[
                _run_agent(
                    _make_settings(parity_seed, mid),
                    equity,
                    parity_session_factory,
                )
                for mid, equity in zip(model_ids, _EQUITIES, strict=True)
            ]
        )

        assert all(rid is not None for rid in run_ids)

        async with parity_session_factory() as session:
            snapshot = await session.get(ContextSnapshot, uuid.UUID(expected_snap_id))
            assert snapshot is not None
            assert snapshot.context_hash == expected_context_hash

            # Every run references that one snapshot — hash is inherently identical
            run_snap_ids = set()
            for run_id in run_ids:
                run = await session.get(Run, uuid.UUID(run_id))  # type: ignore[arg-type]
                assert run is not None
                run_snap_ids.add(str(run.context_snapshot_id))

            assert len(run_snap_ids) == 1, f"Runs reference different snapshots: {run_snap_ids}"
            assert run_snap_ids == {expected_snap_id}

    @pytest.mark.asyncio
    @pytest.mark.invariant("13")
    async def test_portfolio_state_hash_diverges_across_models(
        self,
        parity_session_factory: async_sessionmaker[AsyncSession],
        parity_seed: dict[str, str],
    ) -> None:
        """portfolio_state_hash in account_snapshots must diverge across the 4 models (inv #13).

        Each agent fetches its own wallet state (different equity values), so
        the SHA-256 of the portfolio JSON differs.  This is expected behaviour:
        market context is byte-identical; portfolio state is model-independent.
        """
        model_ids: list[str] = parity_seed["model_ids"]

        run_ids = await asyncio.gather(
            *[
                _run_agent(
                    _make_settings(parity_seed, mid),
                    equity,
                    parity_session_factory,
                )
                for mid, equity in zip(model_ids, _EQUITIES, strict=True)
            ]
        )

        assert all(rid is not None for rid in run_ids)

        async with parity_session_factory() as session:
            portfolio_hashes: list[str] = []
            for run_id in run_ids:
                acc_snap = (
                    await session.scalars(
                        select(AccountSnapshot).where(
                            AccountSnapshot.run_id == uuid.UUID(run_id)  # type: ignore[arg-type]
                        )
                    )
                ).first()
                assert acc_snap is not None, f"No account_snapshot for run {run_id}"
                portfolio_hashes.append(acc_snap.portfolio_state_hash)

            # All 4 hashes must be distinct (each agent has different equity_usd)
            assert len(set(portfolio_hashes)) == len(model_ids), (
                f"Expected {len(model_ids)} distinct portfolio_state_hash values, "
                f"got {len(set(portfolio_hashes))}: {portfolio_hashes}"
            )

    @pytest.mark.asyncio
    @pytest.mark.invariant("13")
    async def test_context_snapshot_written_by_orchestrator_only(
        self,
        parity_session_factory: async_sessionmaker[AsyncSession],
        parity_seed: dict[str, str],
    ) -> None:
        """Agents must not create context_snapshots — only the orchestrator may (inv #13)."""
        model_ids: list[str] = parity_seed["model_ids"]
        expected_snap_id = parity_seed["snapshot_id"]

        # Count snapshots before running agents
        async with parity_session_factory() as session:
            all_snaps_before = (
                await session.scalars(
                    select(ContextSnapshot).where(
                        ContextSnapshot.experiment_id == uuid.UUID(parity_seed["experiment_id"])
                    )
                )
            ).all()
        snap_count_before = len(all_snaps_before)

        run_ids = await asyncio.gather(
            *[
                _run_agent(
                    _make_settings(parity_seed, mid),
                    equity,
                    parity_session_factory,
                )
                for mid, equity in zip(model_ids, _EQUITIES, strict=True)
            ]
        )

        assert all(rid is not None for rid in run_ids)

        # Count snapshots after — agents must not have created any new ones
        async with parity_session_factory() as session:
            all_snaps_after = (
                await session.scalars(
                    select(ContextSnapshot).where(
                        ContextSnapshot.experiment_id == uuid.UUID(parity_seed["experiment_id"])
                    )
                )
            ).all()

        assert len(all_snaps_after) == snap_count_before, (
            f"Agent(s) wrote new context_snapshots: before={snap_count_before}, "
            f"after={len(all_snaps_after)}"
        )
        # The one pre-existing snapshot is still the only one
        snap_ids = {str(s.id) for s in all_snaps_after}
        assert expected_snap_id in snap_ids
