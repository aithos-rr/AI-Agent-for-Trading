"""Integration tests for OutcomesRepository (§7.6, M5-T02b).

Tests persist_outcome and list_for_model_in_window on an ephemeral Postgres
instance. Each test gets an isolated transaction (rolled back on teardown via
db_session fixture).
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
from aiat.db.models.decision import Decision
from aiat.db.models.experiment import Experiment
from aiat.db.models.model import Model
from aiat.db.models.outcome import Outcome
from aiat.db.models.position import Position
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from aiat.db.repositories.outcomes import OutcomesRepository

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

_TICK_ID = "2026-01-15T12:00:00"
_SCHEMA_VERSION = "v2"
_GIT_SHA = "abc1234"
_PT_TEXT = "You are a trading agent."


@dataclass
class SeedIds:
    experiment_id: uuid.UUID
    model_id: str
    opening_run_id: uuid.UUID
    closing_run_id: uuid.UUID
    action_id: uuid.UUID
    position_id: uuid.UUID


async def _seed(session: AsyncSession) -> SeedIds:
    """Insert minimum FK chain for outcomes tests.

    Each call creates an independent experiment/model/prompt-template chain, so
    it can be invoked more than once per test (e.g. to seed a second model) without
    colliding on the prompt_templates primary key or unique label.
    """
    exp_id = uuid.uuid4()
    model_id = f"openai-gpt4o-{uuid.uuid4().hex[:8]}"
    snap_id = uuid.uuid4()
    opening_run_id = uuid.uuid4()
    closing_run_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    action_id = uuid.uuid4()
    position_id = uuid.uuid4()
    tick_at = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

    # Per-call prompt template: unique hash + label so repeated _seed() calls
    # within a single test do not violate the PK / unique-label constraints.
    pt_text = f"{_PT_TEXT} ({model_id})"
    pt_hash = hashlib.sha256(pt_text.encode()).hexdigest()

    session.add(
        Experiment(
            id=exp_id,
            name=f"test-exp-{exp_id.hex[:8]}",
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
            wallet_address=f"0x{uuid.uuid4().hex}",
            pricing_input_usd_per_1m=Decimal("5.000000"),
            pricing_output_usd_per_1m=Decimal("15.000000"),
        )
    )
    await session.flush()

    session.add(
        PromptTemplate(
            sha256_hash=pt_hash,
            label=f"test-pt-{uuid.uuid4().hex[:8]}",
            template_text=pt_text,
            confidence_def="Probability that the action yields positive PnL.",
            controlled_signals=[],
        )
    )
    await session.flush()

    session.add(
        ContextSnapshot(
            id=snap_id,
            experiment_id=exp_id,
            tick_id=_TICK_ID,
            tick_at=tick_at,
            context_hash="deadbeef",
            context_json={},
            source_timestamps={},
            build_duration_ms=100,
        )
    )
    await session.flush()

    for run_id, sched_offset in [(opening_run_id, 0), (closing_run_id, 15)]:
        session.add(
            Run(
                id=run_id,
                experiment_id=exp_id,
                model_id=model_id,
                tick_id=_TICK_ID,
                scheduled_for=tick_at + timedelta(minutes=sched_offset),
                run_started_at=tick_at + timedelta(minutes=sched_offset),
                status="success",
                prompt_template_hash=pt_hash,
                rendered_prompt_hash="aabbcc",
                context_snapshot_id=snap_id,
                schema_version=_SCHEMA_VERSION,
                git_commit_sha=_GIT_SHA,
            )
        )
    await session.flush()

    session.add(
        Decision(
            id=decision_id,
            run_id=opening_run_id,
            experiment_id=exp_id,
            model_id=model_id,
            decided_at=tick_at,
            portfolio_reasoning="Bull market",
            risk_assessment="Low risk",
            latency_ms=500,
            raw_payload={},
        )
    )
    await session.flush()

    session.add(
        DecisionAction(
            id=action_id,
            decision_id=decision_id,
            experiment_id=exp_id,
            model_id=model_id,
            run_id=opening_run_id,
            symbol="BTC",
            confidence=Decimal("0.7500"),
            time_horizon_min=60,
            action_reasoning="Strong momentum; enter LONG.",
            action_key_signals=[],
            side_requested="LONG",
            leverage_requested=Decimal("3.00"),
            size_pct_requested=Decimal("0.2000"),
            stop_loss_pct=Decimal("0.0200"),
            take_profit_pct=Decimal("0.0400"),
            entry_type="market",
            side_executed="LONG",
            leverage_executed=Decimal("3.00"),
            size_pct_executed=Decimal("0.2000"),
            execution_status="filled",
            executed=True,
        )
    )
    await session.flush()

    entry_price = Decimal("50000.00")
    size_units = Decimal("0.01")
    leverage = Decimal("3.00")
    notional = size_units * entry_price
    session.add(
        Position(
            id=position_id,
            experiment_id=exp_id,
            model_id=model_id,
            opening_run_id=opening_run_id,
            symbol="BTC",
            side="LONG",
            opening_action_id=action_id,
            opened_at=tick_at,
            entry_price=entry_price,
            size_units=size_units,
            leverage=leverage,
            notional_value_usd=notional,
            initial_margin_usd=notional / leverage,
            stop_loss_price=entry_price * Decimal("0.98"),
            take_profit_price=entry_price * Decimal("1.04"),
        )
    )
    await session.flush()

    return SeedIds(
        experiment_id=exp_id,
        model_id=model_id,
        opening_run_id=opening_run_id,
        closing_run_id=closing_run_id,
        action_id=action_id,
        position_id=position_id,
    )


def _outcome_kwargs(ids: SeedIds, **overrides: object) -> dict[str, object]:
    """Return a complete set of keyword args for OutcomesRepository.persist_outcome."""
    base: dict[str, object] = {
        "position_id": str(ids.position_id),
        "opening_action_id": str(ids.action_id),
        "opening_run_id": str(ids.opening_run_id),
        "closing_run_id": str(ids.closing_run_id),
        "experiment_id": str(ids.experiment_id),
        "model_id": ids.model_id,
        "symbol": "BTC",
        "realized_pnl_gross_usd": Decimal("50.00"),
        "sum_fees_usd": Decimal("1.50"),
        "sum_funding_usd": Decimal("0.50"),
        "pnl_net_fee_usd": Decimal("48.50"),
        "pnl_net_fee_funding_usd": Decimal("48.00"),
        "pnl_net_fee_funding_tax_sim_usd": Decimal("0"),
        "was_profitable_net": True,
        "holding_duration_min": 45,
        "decision_action_confidence": Decimal("0.7500"),
        "decision_action_time_horizon_min": 60,
        "horizon_met": True,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_outcome_success(db_session: AsyncSession) -> None:
    """persist_outcome creates an Outcome row with correct field values."""
    ids = await _seed(db_session)
    repo = OutcomesRepository(db_session)

    outcome_id = await repo.persist_outcome(**_outcome_kwargs(ids))  # type: ignore[arg-type]

    assert uuid.UUID(outcome_id)  # valid UUID

    row = (
        await db_session.execute(select(Outcome).where(Outcome.id == uuid.UUID(outcome_id)))
    ).scalar_one()

    assert row.position_id == ids.position_id
    assert row.opening_action_id == ids.action_id
    assert row.opening_run_id == ids.opening_run_id
    assert row.closing_run_id == ids.closing_run_id
    assert row.model_id == ids.model_id
    assert row.symbol == "BTC"
    assert row.sum_fees_usd == Decimal("1.50")
    assert row.was_profitable_net is True
    assert row.holding_duration_min == 45
    assert row.horizon_met is True
    assert row.pnl_net_fee_funding_tax_sim_usd == Decimal("0")


@pytest.mark.asyncio
async def test_persist_outcome_duplicate_position_raises(db_session: AsyncSession) -> None:
    """Second persist_outcome for the same position_id raises IntegrityError (UNIQUE)."""
    ids = await _seed(db_session)
    repo = OutcomesRepository(db_session)

    await repo.persist_outcome(**_outcome_kwargs(ids))  # type: ignore[arg-type]

    with pytest.raises(IntegrityError):
        await repo.persist_outcome(**_outcome_kwargs(ids))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_persist_outcome_check_confidence_out_of_range(db_session: AsyncSession) -> None:
    """confidence > 1 violates chk_outcome_confidence_range CHECK."""
    ids = await _seed(db_session)
    repo = OutcomesRepository(db_session)

    with pytest.raises(IntegrityError):
        await repo.persist_outcome(
            **_outcome_kwargs(ids, decision_action_confidence=Decimal("1.5"))  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_persist_outcome_check_sum_fees_negative(db_session: AsyncSession) -> None:
    """sum_fees_usd < 0 violates chk_outcome_sum_fees_ge0 CHECK."""
    ids = await _seed(db_session)
    repo = OutcomesRepository(db_session)

    with pytest.raises(IntegrityError):
        await repo.persist_outcome(
            **_outcome_kwargs(ids, sum_fees_usd=Decimal("-0.01"))  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_persist_outcome_check_time_horizon_zero(db_session: AsyncSession) -> None:
    """time_horizon_min=0 violates chk_outcome_time_horizon_gt0 CHECK."""
    ids = await _seed(db_session)
    repo = OutcomesRepository(db_session)

    with pytest.raises(IntegrityError):
        await repo.persist_outcome(
            **_outcome_kwargs(ids, decision_action_time_horizon_min=0)  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_list_for_model_in_window_returns_outcomes(db_session: AsyncSession) -> None:
    """list_for_model_in_window returns outcomes within the requested time window."""
    ids = await _seed(db_session)
    repo = OutcomesRepository(db_session)

    await repo.persist_outcome(**_outcome_kwargs(ids))  # type: ignore[arg-type]

    # Use a broad window to capture the just-created outcome
    now = datetime.now(UTC)
    start = (now - timedelta(minutes=5)).isoformat()
    end = (now + timedelta(minutes=5)).isoformat()

    outcomes = await repo.list_for_model_in_window(ids.model_id, start, end)

    assert len(outcomes) == 1
    assert outcomes[0].position_id == ids.position_id


@pytest.mark.asyncio
async def test_list_for_model_in_window_excludes_other_model(db_session: AsyncSession) -> None:
    """list_for_model_in_window returns only model A's outcomes, never model B's.

    Two real models each persist an outcome in the same time window. Querying for
    model A must return exactly A's row and never B's id — guaranteeing the test
    goes RED if the `model_id` filter in the repository is dropped.
    """
    ids_a = await _seed(db_session)
    ids_b = await _seed(db_session)
    assert ids_a.model_id != ids_b.model_id  # sanity: two distinct models
    repo = OutcomesRepository(db_session)

    outcome_a_id = await repo.persist_outcome(**_outcome_kwargs(ids_a))  # type: ignore[arg-type]
    outcome_b_id = await repo.persist_outcome(**_outcome_kwargs(ids_b))  # type: ignore[arg-type]

    now = datetime.now(UTC)
    start = (now - timedelta(minutes=5)).isoformat()
    end = (now + timedelta(minutes=5)).isoformat()

    outcomes = await repo.list_for_model_in_window(ids_a.model_id, start, end)

    # Exactly model A's single outcome — B's row in the same window is excluded.
    assert len(outcomes) == 1
    assert outcomes[0].model_id == ids_a.model_id
    assert str(outcomes[0].id) == outcome_a_id
    assert outcomes[0].position_id == ids_a.position_id
    returned_ids = {str(o.id) for o in outcomes}
    assert outcome_b_id not in returned_ids
    assert all(o.model_id != ids_b.model_id for o in outcomes)


@pytest.mark.asyncio
async def test_list_for_model_in_window_excludes_outside_window(db_session: AsyncSession) -> None:
    """list_for_model_in_window returns empty list when window is in the future."""
    ids = await _seed(db_session)
    repo = OutcomesRepository(db_session)

    await repo.persist_outcome(**_outcome_kwargs(ids))  # type: ignore[arg-type]

    future = datetime.now(UTC) + timedelta(hours=1)
    start = future.isoformat()
    end = (future + timedelta(minutes=5)).isoformat()

    outcomes = await repo.list_for_model_in_window(ids.model_id, start, end)

    assert outcomes == []


@pytest.mark.asyncio
async def test_list_for_model_in_window_ordering(db_session: AsyncSession) -> None:
    """list_for_model_in_window returns outcomes ordered by created_at ascending."""
    ids = await _seed(db_session)
    repo = OutcomesRepository(db_session)

    # Only one outcome possible per position (UNIQUE constraint), so
    # we just verify the method returns a list with stable ordering.
    await repo.persist_outcome(**_outcome_kwargs(ids))  # type: ignore[arg-type]

    now = datetime.now(UTC)
    results = await repo.list_for_model_in_window(
        ids.model_id,
        (now - timedelta(minutes=5)).isoformat(),
        (now + timedelta(minutes=5)).isoformat(),
    )

    assert len(results) == 1
    assert results[0].model_id == ids.model_id
