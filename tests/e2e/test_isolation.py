"""E2E tests for cross-model isolation — invariant #1 (PRD §9.5, M5-T09).

Verifies: an agent for model_1 never reads or writes data belonging to model_2.
Primary strategy: RepositorySpy (flush intercept on the ORM session).
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
from aiat.db.models.action import DecisionAction
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.cost_event import CostEvent
from aiat.db.models.decision import Decision
from aiat.db.models.experiment import Experiment
from aiat.db.models.model import Model
from aiat.db.models.position import Position
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from aiat.db.repositories.positions import PositionsRepository
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
from tests.e2e._repository_spy import LeakDetected, RepositorySpy

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TICK_ID = "2026-06-14T15:00:00+00:00"
_TICK_AT = datetime(2026, 6, 14, 15, 0, tzinfo=UTC)
_GIT_SHA = "iso-test-sha"
_SCHEMA_VERSION = "v1"
_PT_TEXT = (
    "You are a trading agent (isolation-test template). Make decisions based on market context."
)
_PT_HASH = hashlib.sha256(_PT_TEXT.encode()).hexdigest()
_PT_CONFIDENCE_DEF = "Probability that the action yields positive net PnL."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hold_invocation() -> LLMInvocationResult:
    decision = TradeDecision(
        portfolio_reasoning=(
            "No signal detected. Maintaining flat position for all symbols this tick."
            " Risk is minimal."
        ),
        risk_assessment="Low risk. No open positions. Market is range-bound.",
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
                action_reasoning=f"{sym}: hold — no signal detected this tick.",
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
        latency_ms=1200,
        raw_response_id="resp-isolation-hold",
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
        price_usd=Decimal("64000"),
        rsi_14=Decimal("50"),
        macd_signal_diff=Decimal("50"),
        ema_20=Decimal("63000"),
        ema_50=Decimal("62000"),
        bollinger_upper=Decimal("67000"),
        bollinger_lower=Decimal("61000"),
        atr_14=Decimal("400"),
        volume_24h_usd=Decimal("900000000"),
    )
    return ContextBundle(
        tick_id=_TICK_ID,
        tick_at=_TICK_ID,
        technical=[
            tech,
            tech.model_copy(update={"symbol": "ETH", "price_usd": Decimal("3400")}),
            tech.model_copy(update={"symbol": "SOL", "price_usd": Decimal("175")}),
        ],
        sentiment=SentimentSnapshot(
            fear_greed_index=50,
            fear_greed_label="neutral",
            fetched_at=_TICK_ID,
        ),
        news=[
            NewsItem(
                title="Crypto market neutral",
                summary="Markets consolidate without clear direction",
                source="coindesk",
                published_at=_TICK_ID,
            )
        ],
        onchain=[
            OnChainSnapshot(
                symbol="BTC",
                funding_rate_8h=Decimal("0"),
                open_interest_usd=Decimal("9000000"),
                premium=Decimal("0"),
                liquidations_24h_usd=Decimal("500000"),
            )
        ],
        source_timestamps={"technical_btc": _TICK_ID},
    )


@pytest_asyncio.fixture(scope="function")
async def iso_session_factory(db_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def iso_seed(
    iso_session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    """Seed experiment, 2 models, prompt_template, and 1 context_snapshot.

    Returns model_1_id, model_2_id, experiment_id, snapshot_id, prompt_template_hash.
    """
    exp_id = uuid.uuid4()
    model_1_id = f"openai-gpt4o-iso1-{uuid.uuid4().hex[:6]}"
    model_2_id = f"anthropic-claude3-iso2-{uuid.uuid4().hex[:6]}"
    snap_id = uuid.uuid4()
    context_bundle = _make_context_bundle()

    async with iso_session_factory() as session:
        session.add(
            Experiment(
                id=exp_id,
                name=f"isolation-test-{exp_id.hex[:8]}",
                started_at=datetime.now(UTC),
                git_commit_sha=_GIT_SHA,
                config_snapshot={},
            )
        )
        await session.flush()

        for mid in (model_1_id, model_2_id):
            session.add(
                Model(
                    id=mid,
                    provider="openai" if "gpt4o" in mid else "anthropic",
                    model_name_api="gpt-4o" if "gpt4o" in mid else "claude-3-5-sonnet",
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
                label="isolation-pt-shared",
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
                context_hash=hashlib.sha256(b"isolation-test").hexdigest(),
                context_json=context_bundle.model_dump(mode="json"),
                source_timestamps={},
                build_duration_ms=30,
            )
        )
        await session.flush()

        # Seed an OPEN Position (closed_at NULL) per model so isolation reads have
        # real foreign-model rows to exclude. Without model_2 positions, the
        # list_open_for_model exclusion assertion would be tautological: a dropped
        # `WHERE Position.model_id` filter would still pass. The positions live in a
        # SEPARATE experiment (`pos_exp_id`) with their own tick/snapshot so the
        # existing run-count assertions (scoped to `experiment_id`) stay unaffected;
        # list_open_for_model is experiment-scoped (ADR-0039), so the read-path test
        # queries with `position_experiment_id`. The spy's load listener gives the teeth
        # (PRD §9.5 lines 2481-2483).
        pos_exp_id = uuid.uuid4()
        pos_snap_id = uuid.uuid4()
        session.add(
            Experiment(
                id=pos_exp_id,
                name=f"isolation-test-pos-{pos_exp_id.hex[:8]}",
                started_at=datetime.now(UTC),
                git_commit_sha=_GIT_SHA,
                config_snapshot={},
            )
        )
        await session.flush()
        session.add(
            ContextSnapshot(
                id=pos_snap_id,
                experiment_id=pos_exp_id,
                tick_id=_TICK_ID,
                tick_at=_TICK_AT,
                context_hash=hashlib.sha256(b"isolation-test-pos").hexdigest(),
                context_json=context_bundle.model_dump(mode="json"),
                source_timestamps={},
                build_duration_ms=30,
            )
        )
        await session.flush()

        position_ids: dict[str, str] = {}
        for mid in (model_1_id, model_2_id):
            run_id = uuid.uuid4()
            decision_id = uuid.uuid4()
            action_id = uuid.uuid4()
            position_id = uuid.uuid4()
            session.add(
                Run(
                    id=run_id,
                    experiment_id=pos_exp_id,
                    model_id=mid,
                    tick_id=_TICK_ID,
                    scheduled_for=_TICK_AT,
                    run_started_at=_TICK_AT,
                    status="success",
                    prompt_template_hash=_PT_HASH,
                    rendered_prompt_hash="seed-open-pos",
                    context_snapshot_id=pos_snap_id,
                    schema_version=_SCHEMA_VERSION,
                    git_commit_sha=_GIT_SHA,
                )
            )
            await session.flush()
            session.add(
                Decision(
                    id=decision_id,
                    run_id=run_id,
                    experiment_id=pos_exp_id,
                    model_id=mid,
                    decided_at=_TICK_AT,
                    portfolio_reasoning="Seeded open position for isolation read-path test.",
                    risk_assessment="Seeded.",
                    portfolio_confidence=Decimal("0.60"),
                    latency_ms=1000,
                    raw_payload={"seed": True},
                )
            )
            await session.flush()
            session.add(
                DecisionAction(
                    id=action_id,
                    decision_id=decision_id,
                    experiment_id=pos_exp_id,
                    model_id=mid,
                    run_id=run_id,
                    symbol="BTC",
                    confidence=Decimal("0.60"),
                    time_horizon_min=60,
                    action_reasoning="Seeded long.",
                    action_key_signals=[],
                    side_requested="LONG",
                    leverage_requested=Decimal("2"),
                    size_pct_requested=Decimal("0.10"),
                    stop_loss_pct=Decimal("0.02"),
                    take_profit_pct=Decimal("0.04"),
                    entry_type="market",
                    side_executed="LONG",
                    leverage_executed=Decimal("2"),
                    size_pct_executed=Decimal("0.10"),
                )
            )
            await session.flush()
            session.add(
                Position(
                    id=position_id,
                    experiment_id=pos_exp_id,
                    model_id=mid,
                    opening_run_id=run_id,
                    symbol="BTC",
                    side="LONG",
                    opening_action_id=action_id,
                    opened_at=_TICK_AT,
                    entry_price=Decimal("64000"),
                    size_units=Decimal("0.01"),
                    leverage=Decimal("2"),
                    notional_value_usd=Decimal("640.00000000"),
                    initial_margin_usd=Decimal("320.00000000"),
                    stop_loss_price=Decimal("62720.00000000"),
                    take_profit_price=Decimal("66560.00000000"),
                    # closed_at left NULL → OPEN position
                )
            )
            await session.flush()
            position_ids[mid] = str(position_id)

        await session.commit()

    return {
        "experiment_id": str(exp_id),
        "model_1_id": model_1_id,
        "model_2_id": model_2_id,
        "snapshot_id": str(snap_id),
        "prompt_template_hash": _PT_HASH,
        # The open positions live in their OWN experiment (see the seed comment above);
        # list_open_for_model is experiment-scoped (ADR-0039) so the read-path test must
        # query with that experiment, not the run-count one.
        "position_experiment_id": str(pos_exp_id),
        "model_1_open_position_id": position_ids[model_1_id],
        "model_2_open_position_id": position_ids[model_2_id],
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


class TestCrossModelIsolation:
    @pytest.mark.asyncio
    @pytest.mark.invariant("1")
    async def test_model1_run_creates_only_model1_rows(
        self,
        iso_session_factory: async_sessionmaker[AsyncSession],
        iso_seed: dict[str, str],
    ) -> None:
        """Model_1's loop must create rows tagged with model_1_id only (inv #1)."""
        model_1 = iso_seed["model_1_id"]
        model_2 = iso_seed["model_2_id"]
        settings = _make_settings(iso_seed, model_1)
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
            llm_client=_stub_llm(invocation),
            hl_client=_stub_hl(),
            session_factory=iso_session_factory,
            guardrails=guardrails,
        )
        run_id = await loop.run_once(_TICK_ID, _TICK_AT)
        assert run_id is not None

        async with iso_session_factory() as session:
            # All runs for model_1 in this experiment
            m1_runs = (
                await session.scalars(
                    select(Run).where(
                        Run.experiment_id == uuid.UUID(iso_seed["experiment_id"]),
                        Run.model_id == model_1,
                    )
                )
            ).all()
            assert len(m1_runs) == 1
            assert str(m1_runs[0].id) == run_id

            # model_2 must have zero runs
            m2_count = await session.scalar(
                select(func.count()).where(
                    Run.experiment_id == uuid.UUID(iso_seed["experiment_id"]),
                    Run.model_id == model_2,
                )
            )
            assert m2_count == 0

            # All decisions belong to model_1
            decisions = (
                await session.scalars(select(Decision).where(Decision.run_id == uuid.UUID(run_id)))
            ).all()
            for d in decisions:
                assert d.model_id == model_1

            # All decision_actions for this run
            actions = (
                await session.scalars(
                    select(DecisionAction).where(DecisionAction.run_id == uuid.UUID(run_id))
                )
            ).all()
            assert len(actions) == 3

            # cost_events for this run
            costs = (
                await session.scalars(
                    select(CostEvent).where(CostEvent.run_id == uuid.UUID(run_id))
                )
            ).all()
            for c in costs:
                assert c.model_id == model_1

    @pytest.mark.asyncio
    @pytest.mark.invariant("1")
    async def test_two_models_produce_isolated_rows(
        self,
        iso_session_factory: async_sessionmaker[AsyncSession],
        iso_seed: dict[str, str],
    ) -> None:
        """Running both models for the same tick yields separate, non-overlapping rows."""
        model_1 = iso_seed["model_1_id"]
        model_2 = iso_seed["model_2_id"]

        for mid in (model_1, model_2):
            settings = _make_settings(iso_seed, mid)
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
                llm_client=_stub_llm(invocation),
                hl_client=_stub_hl(),
                session_factory=iso_session_factory,
                guardrails=guardrails,
            )
            await loop.run_once(_TICK_ID, _TICK_AT)

        exp_id = uuid.UUID(iso_seed["experiment_id"])
        async with iso_session_factory() as session:
            m1_run_ids = set(
                str(r.id)
                for r in (
                    await session.scalars(
                        select(Run).where(Run.experiment_id == exp_id, Run.model_id == model_1)
                    )
                ).all()
            )
            m2_run_ids = set(
                str(r.id)
                for r in (
                    await session.scalars(
                        select(Run).where(Run.experiment_id == exp_id, Run.model_id == model_2)
                    )
                ).all()
            )
            assert len(m1_run_ids) == 1
            assert len(m2_run_ids) == 1
            # Run IDs are distinct
            assert m1_run_ids.isdisjoint(m2_run_ids)

            # Decisions are tagged with correct model_id
            for run_id_str, expected_model in (
                (list(m1_run_ids)[0], model_1),
                (list(m2_run_ids)[0], model_2),
            ):
                decisions = (
                    await session.scalars(
                        select(Decision).where(Decision.run_id == uuid.UUID(run_id_str))
                    )
                ).all()
                for d in decisions:
                    assert d.model_id == expected_model, (
                        f"Decision for run {run_id_str} has wrong model_id "
                        f"'{d.model_id}', expected '{expected_model}'"
                    )

    @pytest.mark.asyncio
    @pytest.mark.invariant("1")
    async def test_repository_spy_detects_cross_model_flush(
        self,
        iso_session_factory: async_sessionmaker[AsyncSession],
        iso_seed: dict[str, str],
    ) -> None:
        """RepositorySpy raises LeakDetected when a flush includes a wrong model_id row."""
        model_1 = iso_seed["model_1_id"]
        model_2 = iso_seed["model_2_id"]
        exp_id = uuid.UUID(iso_seed["experiment_id"])

        async with iso_session_factory() as session:
            spy = RepositorySpy(session, expected_model_id=model_1)

            # Manufacture a run row tagged with model_2 (a "leak")
            leaked_run = Run(
                id=uuid.uuid4(),
                experiment_id=exp_id,
                model_id=model_2,  # wrong model — should trigger spy
                tick_id=_TICK_ID,
                scheduled_for=_TICK_AT,
                run_started_at=_TICK_AT,
                status="running",
                prompt_template_hash=iso_seed["prompt_template_hash"],
                rendered_prompt_hash="aabbcc",
                context_snapshot_id=uuid.UUID(iso_seed["snapshot_id"]),
                schema_version=_SCHEMA_VERSION,
                git_commit_sha=_GIT_SHA,
            )

            with pytest.raises(LeakDetected):
                with spy:
                    session.add(leaked_run)
                    await session.flush()

    @pytest.mark.asyncio
    @pytest.mark.invariant("1")
    async def test_repository_spy_passes_for_correct_model(
        self,
        iso_session_factory: async_sessionmaker[AsyncSession],
        iso_seed: dict[str, str],
    ) -> None:
        """RepositorySpy does NOT raise when all flushed rows belong to expected model_id."""
        model_1 = iso_seed["model_1_id"]
        exp_id = uuid.UUID(iso_seed["experiment_id"])

        async with iso_session_factory() as session:
            spy = RepositorySpy(session, expected_model_id=model_1)

            correct_run = Run(
                id=uuid.uuid4(),
                experiment_id=exp_id,
                model_id=model_1,  # correct model
                tick_id=_TICK_ID,
                scheduled_for=_TICK_AT,
                run_started_at=_TICK_AT,
                status="running",
                prompt_template_hash=iso_seed["prompt_template_hash"],
                rendered_prompt_hash="aabbcc",
                context_snapshot_id=uuid.UUID(iso_seed["snapshot_id"]),
                schema_version=_SCHEMA_VERSION,
                git_commit_sha=_GIT_SHA,
            )

            # Should NOT raise
            with spy:
                session.add(correct_run)
                await session.flush()
            # Roll back so we don't pollute other tests
            await session.rollback()

    @pytest.mark.asyncio
    @pytest.mark.invariant("1")
    async def test_list_open_positions_excludes_other_model(
        self,
        iso_session_factory: async_sessionmaker[AsyncSession],
        iso_seed: dict[str, str],
    ) -> None:
        """list_open_for_model(model_1) returns ONLY model_1's open position (inv #1).

        Read-path teeth (PRD §9.5 lines 2481-2483): with the RepositorySpy load
        listener active and expected_model_id=model_1, any model_2 row materialized
        from the SELECT would raise LeakDetected. Two OPEN positions (one per model)
        are seeded in the same DB; removing the `WHERE Position.model_id == model_id`
        filter in PositionsRepository.list_open_for_model would load model_2's row and
        turn this test RED (the spy raises on the foreign-model instance), so the
        assertion is NOT tautological.
        """
        model_1 = iso_seed["model_1_id"]
        model_2 = iso_seed["model_2_id"]
        m1_pos_id = iso_seed["model_1_open_position_id"]
        m2_pos_id = iso_seed["model_2_open_position_id"]

        async with iso_session_factory() as session:
            repo = PositionsRepository(session)
            with RepositorySpy(session, expected_model_id=model_1):
                open_positions = await repo.list_open_for_model(
                    experiment_id=iso_seed["position_experiment_id"], model_id=model_1
                )

            returned_ids = {str(p.id) for p in open_positions}
            returned_models = {p.model_id for p in open_positions}

            # Only model_1's open position is returned.
            assert returned_models == {model_1}, (
                f"list_open_for_model({model_1!r}) leaked foreign model_ids: {returned_models}"
            )
            assert m1_pos_id in returned_ids
            assert m2_pos_id not in returned_ids
            # model_2's position is never present.
            for p in open_positions:
                assert p.model_id != model_2
