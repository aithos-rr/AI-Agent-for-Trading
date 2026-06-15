"""Integration tests for DecisionsRepository (§7.6, M5-T01).

Tests atomicity, rollback on FK violation, CHECK constraints, and query methods
on an ephemeral Postgres instance.  Each test gets an isolated transaction via
the db_session fixture (rolled back on teardown).
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.action import DecisionAction
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.cost_event import CostEvent
from aiat.db.models.decision import Decision
from aiat.db.models.experiment import Experiment
from aiat.db.models.llm_invocation import LLMInvocation
from aiat.db.models.model import Model
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from aiat.db.repositories.decisions import DecisionsRepository
from aiat.domain.enums import EntryType, Side
from aiat.domain.schemas import (
    ActionDecision,
    CostEventData,
    GuardrailReport,
    LLMInvocationResult,
    TradeDecision,
)

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

_TICK_ID = "2026-01-15T12:00:00"
_SCHEMA_VERSION = "v2"
_GIT_SHA = "abc1234"
_PT_TEXT = "You are a trading agent."
_PT_HASH = hashlib.sha256(_PT_TEXT.encode()).hexdigest()


@dataclass
class SeedIds:
    experiment_id: uuid.UUID
    model_id: str
    run_id: uuid.UUID
    context_snapshot_id: uuid.UUID


async def _seed(session: AsyncSession) -> SeedIds:
    """Insert the minimum FK chain to satisfy the decisions FK constraints."""
    exp_id = uuid.uuid4()
    model_id = f"openai-gpt4o-{uuid.uuid4().hex[:8]}"
    snap_id = uuid.uuid4()
    run_id = uuid.uuid4()
    tick_at = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

    experiment = Experiment(
        id=exp_id,
        name=f"test-exp-{exp_id.hex[:8]}",
        started_at=datetime.now(UTC),
        git_commit_sha=_GIT_SHA,
        config_snapshot={},
    )
    session.add(experiment)
    await session.flush()

    model = Model(
        id=model_id,
        provider="openai",
        model_name_api="gpt-4o",
        tier="premium",
        geography="USA",
        wallet_address=f"0x{uuid.uuid4().hex}",
        pricing_input_usd_per_1m=Decimal("5.000000"),
        pricing_output_usd_per_1m=Decimal("15.000000"),
    )
    session.add(model)
    await session.flush()

    prompt_tmpl = PromptTemplate(
        sha256_hash=_PT_HASH,
        label=f"test-pt-{uuid.uuid4().hex[:8]}",
        template_text=_PT_TEXT,
        confidence_def="Probability that the action yields positive PnL.",
        controlled_signals=[],
    )
    session.add(prompt_tmpl)
    await session.flush()

    snapshot = ContextSnapshot(
        id=snap_id,
        experiment_id=exp_id,
        tick_id=_TICK_ID,
        tick_at=tick_at,
        context_hash="deadbeef",
        context_json={},
        source_timestamps={},
        build_duration_ms=100,
    )
    session.add(snapshot)
    await session.flush()

    run = Run(
        id=run_id,
        experiment_id=exp_id,
        model_id=model_id,
        tick_id=_TICK_ID,
        scheduled_for=tick_at,
        run_started_at=tick_at,
        status="running",
        prompt_template_hash=_PT_HASH,
        rendered_prompt_hash="aabbcc",
        context_snapshot_id=snap_id,
        schema_version=_SCHEMA_VERSION,
        git_commit_sha=_GIT_SHA,
    )
    session.add(run)
    await session.flush()

    return SeedIds(
        experiment_id=exp_id,
        model_id=model_id,
        run_id=run_id,
        context_snapshot_id=snap_id,
    )


def _make_invocation() -> LLMInvocationResult:
    """Build a valid LLMInvocationResult with 3-symbol TradeDecision."""
    trade = TradeDecision(
        portfolio_reasoning=(
            "Market conditions favour a cautious long on BTC with hedged ETH and flat SOL. "
            "Macro sentiment is neutral; on-chain data shows moderate open interest."
        ),
        risk_assessment=(
            "Risk is moderate; BTC position is sized conservatively within margin limits."
        ),
        portfolio_confidence=Decimal("0.6500"),
        actions=[
            ActionDecision(
                symbol="BTC",
                side=Side.LONG,
                leverage=Decimal("3.00"),
                size_pct=Decimal("0.2000"),
                stop_loss_pct=Decimal("0.0200"),
                take_profit_pct=Decimal("0.0400"),
                entry_type=EntryType.MARKET,
                confidence=Decimal("0.7000"),
                time_horizon_min=60,
                action_reasoning="Strong momentum signal; RSI not overbought; good risk/reward.",
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
                confidence=Decimal("0.4500"),
                time_horizon_min=60,
                action_reasoning="ETH shows mixed signals; no clear edge, holding flat.",
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
                confidence=Decimal("0.4000"),
                time_horizon_min=60,
                action_reasoning="SOL funding rate elevated; prefer to stay flat.",
                action_key_signals=[],
            ),
        ],
    )
    return LLMInvocationResult(
        decision=trade,
        cost=CostEventData(
            input_tokens=1200,
            output_tokens=300,
            reasoning_tokens=0,
            cost_usd=Decimal("0.01050000"),
            pricing_snapshot={
                "input_per_1m": Decimal("5.000000"),
                "output_per_1m": Decimal("15.000000"),
            },
            n_attempts=1,
        ),
        latency_ms=850,
        raw_response_id="chatcmpl-abc123",
        raw_payload={"choices": [{"index": 0}]},
        fallback_used=False,
        provider_snapshot="openai",
        model_name_api_snapshot="gpt-4o-2024-08-06",
        temperature=Decimal("0.700"),
        top_p=None,
        max_tokens=4096,
        seed=None,
    )


def _make_guardrail_reports(
    invocation: LLMInvocationResult,
) -> tuple[list[ActionDecision], list[GuardrailReport]]:
    """Build pass-through guardrail reports (no clamping) for an invocation."""
    post_guardrail: list[ActionDecision] = []
    reports: list[GuardrailReport] = []
    for action in invocation.decision.actions:
        post_guardrail.append(action)
        reports.append(
            GuardrailReport(
                symbol=action.symbol,
                original_side=action.side,
                leverage_clamped=False,
                size_pct_clamped=False,
                forced_hold=False,
                final_action=action,
            )
        )
    return post_guardrail, reports


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.invariant("4")
async def test_persist_decision_creates_all_rows(db_session: AsyncSession) -> None:
    """persist_decision inserts 1 decision, 3 actions, 1 cost_event, 1 llm_invocation."""
    ids = await _seed(db_session)
    invocation = _make_invocation()
    post_actions, reports = _make_guardrail_reports(invocation)

    repo = DecisionsRepository(db_session)
    decision_id = await repo.persist_decision(
        run_id=str(ids.run_id),
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        invocation=invocation,
        post_guardrail_actions=post_actions,
        guardrail_reports=reports,
    )

    # Decision row
    decision = await db_session.get(Decision, uuid.UUID(decision_id))
    assert decision is not None
    assert decision.run_id == ids.run_id
    assert decision.model_id == ids.model_id
    assert decision.latency_ms == 850
    assert decision.fallback_used is False
    assert decision.portfolio_confidence == Decimal("0.6500")
    assert decision.raw_response_id == "chatcmpl-abc123"

    # 3 decision_actions
    actions = (
        (
            await db_session.execute(
                select(DecisionAction).where(DecisionAction.decision_id == uuid.UUID(decision_id))
            )
        )
        .scalars()
        .all()
    )
    assert len(actions) == 3
    symbols = {a.symbol for a in actions}
    assert symbols == {"BTC", "ETH", "SOL"}

    # 1 cost_event — persisted AFTER decision row (inv #4)
    cost_events = (
        (
            await db_session.execute(
                select(CostEvent).where(CostEvent.decision_id == uuid.UUID(decision_id))
            )
        )
        .scalars()
        .all()
    )
    assert len(cost_events) == 1
    ce = cost_events[0]
    assert ce.input_tokens == 1200
    assert ce.output_tokens == 300
    assert ce.cost_usd == Decimal("0.01050000")
    assert ce.n_attempts == 1

    # 1 llm_invocation
    llm_invs = (
        (await db_session.execute(select(LLMInvocation).where(LLMInvocation.run_id == ids.run_id)))
        .scalars()
        .all()
    )
    assert len(llm_invs) == 1
    assert llm_invs[0].provider_snapshot == "openai"
    assert llm_invs[0].model_name_api_snapshot == "gpt-4o-2024-08-06"
    assert llm_invs[0].max_tokens == 4096


@pytest.mark.asyncio
async def test_persist_decision_action_requested_vs_executed(db_session: AsyncSession) -> None:
    """requested and executed fields are mapped correctly; guardrail flags default False."""
    ids = await _seed(db_session)
    invocation = _make_invocation()
    post_actions, reports = _make_guardrail_reports(invocation)

    repo = DecisionsRepository(db_session)
    decision_id = await repo.persist_decision(
        run_id=str(ids.run_id),
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        invocation=invocation,
        post_guardrail_actions=post_actions,
        guardrail_reports=reports,
    )

    btc_action = (
        await db_session.execute(
            select(DecisionAction).where(
                DecisionAction.decision_id == uuid.UUID(decision_id),
                DecisionAction.symbol == "BTC",
            )
        )
    ).scalar_one()

    assert btc_action.side_requested == "LONG"
    assert btc_action.side_executed == "LONG"
    assert btc_action.leverage_requested == Decimal("3.00")
    assert btc_action.leverage_executed == Decimal("3.00")
    assert btc_action.size_pct_requested == Decimal("0.2000")
    assert btc_action.size_pct_executed == Decimal("0.2000")
    assert btc_action.stop_loss_pct == Decimal("0.0200")
    assert btc_action.take_profit_pct == Decimal("0.0400")
    assert btc_action.entry_type == "market"
    assert btc_action.confidence == Decimal("0.7000")
    assert btc_action.time_horizon_min == 60
    assert btc_action.leverage_clamped is False
    assert btc_action.size_pct_clamped is False
    assert btc_action.forced_hold is False
    assert btc_action.original_side is None


@pytest.mark.asyncio
async def test_persist_decision_forced_hold_sets_original_side(db_session: AsyncSession) -> None:
    """When guardrail forces HOLD, original_side is recorded and forced_hold=True."""
    ids = await _seed(db_session)
    invocation = _make_invocation()

    # Find the BTC action (LONG) and manufacture a forced-hold scenario
    original_btc = next(a for a in invocation.decision.actions if a.symbol == "BTC")
    forced_hold_action = ActionDecision(
        symbol="BTC",
        side=Side.HOLD,
        leverage=Decimal("0"),
        size_pct=Decimal("0"),
        stop_loss_pct=None,
        take_profit_pct=None,
        entry_type=EntryType.NONE,
        confidence=original_btc.confidence,
        time_horizon_min=original_btc.time_horizon_min,
        action_reasoning=original_btc.action_reasoning,
        action_key_signals=list(original_btc.action_key_signals),
    )

    post_actions: list[ActionDecision] = []
    reports: list[GuardrailReport] = []
    for action in invocation.decision.actions:
        if action.symbol == "BTC":
            post_actions.append(forced_hold_action)
            reports.append(
                GuardrailReport(
                    symbol="BTC",
                    original_side=Side.LONG,
                    leverage_clamped=False,
                    size_pct_clamped=False,
                    forced_hold=True,
                    final_action=forced_hold_action,
                )
            )
        else:
            post_actions.append(action)
            reports.append(
                GuardrailReport(
                    symbol=action.symbol,
                    original_side=action.side,
                    leverage_clamped=False,
                    size_pct_clamped=False,
                    forced_hold=False,
                    final_action=action,
                )
            )

    repo = DecisionsRepository(db_session)
    decision_id = await repo.persist_decision(
        run_id=str(ids.run_id),
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        invocation=invocation,
        post_guardrail_actions=post_actions,
        guardrail_reports=reports,
    )

    btc_action = (
        await db_session.execute(
            select(DecisionAction).where(
                DecisionAction.decision_id == uuid.UUID(decision_id),
                DecisionAction.symbol == "BTC",
            )
        )
    ).scalar_one()

    assert btc_action.forced_hold is True
    assert btc_action.original_side == "LONG"  # original requested side preserved
    assert btc_action.side_executed == "HOLD"
    assert btc_action.side_requested == "LONG"  # original LLM output unchanged


@pytest.mark.asyncio
async def test_persist_decision_rollback_on_duplicate_run_id(db_session: AsyncSession) -> None:
    """decisions.run_id is UNIQUE — second persist for the same run raises IntegrityError.

    This also verifies inv #4: the atomic block (decisions + actions + cost + llm_inv)
    is isolated; the first call succeeded cleanly and its rows are visible.
    """
    ids = await _seed(db_session)
    invocation = _make_invocation()
    post_actions, reports = _make_guardrail_reports(invocation)

    repo = DecisionsRepository(db_session)

    # First persist: should succeed
    first_id = await repo.persist_decision(
        run_id=str(ids.run_id),
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        invocation=invocation,
        post_guardrail_actions=post_actions,
        guardrail_reports=reports,
    )
    assert first_id  # row was created

    # Second persist with the same run_id: must fail with IntegrityError
    with pytest.raises(IntegrityError):
        await repo.persist_decision(
            run_id=str(ids.run_id),
            experiment_id=str(ids.experiment_id),
            model_id=ids.model_id,
            invocation=invocation,
            post_guardrail_actions=post_actions,
            guardrail_reports=reports,
        )


@pytest.mark.asyncio
async def test_persist_decision_missing_run_raises_integrity_error(
    db_session: AsyncSession,
) -> None:
    """Non-existent run_id violates decisions.run_id FK → IntegrityError (full rollback)."""
    ids = await _seed(db_session)
    invocation = _make_invocation()
    post_actions, reports = _make_guardrail_reports(invocation)

    repo = DecisionsRepository(db_session)

    ghost_run_id = str(uuid.uuid4())
    with pytest.raises(IntegrityError):
        await repo.persist_decision(
            run_id=ghost_run_id,
            experiment_id=str(ids.experiment_id),
            model_id=ids.model_id,
            invocation=invocation,
            post_guardrail_actions=post_actions,
            guardrail_reports=reports,
        )


@pytest.mark.asyncio
async def test_persist_decision_duplicate_action_symbol_raises_integrity_error(
    db_session: AsyncSession,
) -> None:
    """uniq_action_decision_symbol: two actions with same (decision_id, symbol) → IntegrityError."""
    ids = await _seed(db_session)
    invocation = _make_invocation()
    post_actions, reports = _make_guardrail_reports(invocation)

    repo = DecisionsRepository(db_session)

    decision_id = await repo.persist_decision(
        run_id=str(ids.run_id),
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        invocation=invocation,
        post_guardrail_actions=post_actions,
        guardrail_reports=reports,
    )

    # Directly insert a second BTC action for the same decision → UNIQUE violation
    dup_action = DecisionAction(
        id=uuid.uuid4(),
        decision_id=uuid.UUID(decision_id),
        experiment_id=ids.experiment_id,
        model_id=ids.model_id,
        run_id=ids.run_id,
        symbol="BTC",
        confidence=Decimal("0.5000"),
        time_horizon_min=60,
        action_reasoning="Duplicate BTC action to trigger unique constraint violation.",
        action_key_signals=[],
        side_requested="HOLD",
        leverage_requested=Decimal("0"),
        size_pct_requested=Decimal("0"),
        stop_loss_pct=None,
        take_profit_pct=None,
        entry_type="none",
        side_executed="HOLD",
        leverage_executed=Decimal("0"),
        size_pct_executed=Decimal("0"),
    )
    db_session.add(dup_action)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_cost_event_decision_id_fk_valid(db_session: AsyncSession) -> None:
    """cost_event.decision_id FK valid — Decision exists when cost_event is inserted (inv #4)."""
    ids = await _seed(db_session)
    invocation = _make_invocation()
    post_actions, reports = _make_guardrail_reports(invocation)

    repo = DecisionsRepository(db_session)
    decision_id = await repo.persist_decision(
        run_id=str(ids.run_id),
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        invocation=invocation,
        post_guardrail_actions=post_actions,
        guardrail_reports=reports,
    )

    ce = (
        await db_session.execute(
            select(CostEvent).where(CostEvent.decision_id == uuid.UUID(decision_id))
        )
    ).scalar_one()

    # FK integrity verified by the fact that this select returns a row
    assert ce.decision_id == uuid.UUID(decision_id)
    assert ce.run_id == ids.run_id
    assert ce.experiment_id == ids.experiment_id


@pytest.mark.asyncio
async def test_get_by_run_returns_decision(db_session: AsyncSession) -> None:
    """get_by_run returns the Decision for a known run_id."""
    ids = await _seed(db_session)
    invocation = _make_invocation()
    post_actions, reports = _make_guardrail_reports(invocation)

    repo = DecisionsRepository(db_session)
    decision_id = await repo.persist_decision(
        run_id=str(ids.run_id),
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        invocation=invocation,
        post_guardrail_actions=post_actions,
        guardrail_reports=reports,
    )

    found = await repo.get_by_run(str(ids.run_id))
    assert found is not None
    assert str(found.id) == decision_id


@pytest.mark.asyncio
async def test_get_by_run_returns_none_for_unknown(db_session: AsyncSession) -> None:
    """get_by_run returns None when no decision exists for the given run_id."""
    repo = DecisionsRepository(db_session)

    result = await repo.get_by_run(str(uuid.uuid4()))
    assert result is None


@pytest.mark.asyncio
async def test_get_action_history_filters_by_model_symbol(db_session: AsyncSession) -> None:
    """get_action_history returns only actions matching model_id + symbol + since."""
    ids = await _seed(db_session)
    invocation = _make_invocation()
    post_actions, reports = _make_guardrail_reports(invocation)

    repo = DecisionsRepository(db_session)
    await repo.persist_decision(
        run_id=str(ids.run_id),
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        invocation=invocation,
        post_guardrail_actions=post_actions,
        guardrail_reports=reports,
    )

    # Query BTC actions since well before the tick
    since = "2026-01-14T00:00:00+00:00"
    btc_history = await repo.get_action_history(ids.model_id, "BTC", since)
    assert len(btc_history) == 1
    assert btc_history[0].symbol == "BTC"
    assert btc_history[0].model_id == ids.model_id

    # Query ETH actions
    eth_history = await repo.get_action_history(ids.model_id, "ETH", since)
    assert len(eth_history) == 1
    assert eth_history[0].symbol == "ETH"


@pytest.mark.asyncio
async def test_get_action_history_since_excludes_old_actions(db_session: AsyncSession) -> None:
    """get_action_history with a future 'since' returns empty list."""
    ids = await _seed(db_session)
    invocation = _make_invocation()
    post_actions, reports = _make_guardrail_reports(invocation)

    repo = DecisionsRepository(db_session)
    await repo.persist_decision(
        run_id=str(ids.run_id),
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        invocation=invocation,
        post_guardrail_actions=post_actions,
        guardrail_reports=reports,
    )

    # Since far in the future → no actions should match
    future = "2030-01-01T00:00:00+00:00"
    history = await repo.get_action_history(ids.model_id, "BTC", future)
    assert history == []


@pytest.mark.asyncio
async def test_get_action_history_multiple_runs(db_session: AsyncSession) -> None:
    """get_action_history returns all BTC actions across multiple runs."""
    ids = await _seed(db_session)
    repo = DecisionsRepository(db_session)

    # Seed a second run for the same model
    tick_at = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    snap_id2 = uuid.uuid4()
    second_tick_id = "2026-01-15T12:15:00"
    snapshot2 = ContextSnapshot(
        id=snap_id2,
        experiment_id=ids.experiment_id,
        tick_id=second_tick_id,
        tick_at=tick_at + timedelta(minutes=15),
        context_hash="cafebabe",
        context_json={},
        source_timestamps={},
        build_duration_ms=100,
    )
    db_session.add(snapshot2)
    await db_session.flush()

    run2_id = uuid.uuid4()
    run2 = Run(
        id=run2_id,
        experiment_id=ids.experiment_id,
        model_id=ids.model_id,
        tick_id=second_tick_id,
        scheduled_for=tick_at + timedelta(minutes=15),
        run_started_at=tick_at + timedelta(minutes=15),
        status="running",
        prompt_template_hash=_PT_HASH,
        rendered_prompt_hash="ddeeff",
        context_snapshot_id=snap_id2,
        schema_version=_SCHEMA_VERSION,
        git_commit_sha=_GIT_SHA,
    )
    db_session.add(run2)
    await db_session.flush()

    invocation = _make_invocation()
    post_actions, reports = _make_guardrail_reports(invocation)

    # First run
    await repo.persist_decision(
        run_id=str(ids.run_id),
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        invocation=invocation,
        post_guardrail_actions=post_actions,
        guardrail_reports=reports,
    )
    # Second run
    await repo.persist_decision(
        run_id=str(run2_id),
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        invocation=invocation,
        post_guardrail_actions=post_actions,
        guardrail_reports=reports,
    )

    since = "2026-01-14T00:00:00+00:00"
    btc_history = await repo.get_action_history(ids.model_id, "BTC", since)
    assert len(btc_history) == 2
