"""Integration tests for BaselineRepository and TaxSimulationRepository (§7.6, M5-T02c).

Tests run against an ephemeral Postgres instance via pytest-postgresql.
Each test gets an isolated transaction (rolled back on teardown via db_session fixture).
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.baseline_config import BaselineConfig
from aiat.db.models.baseline_equity_snapshot import BaselineEquitySnapshot
from aiat.db.models.experiment import Experiment
from aiat.db.models.model import Model
from aiat.db.models.outcome import Outcome
from aiat.db.models.tax_sim import TaxSimPeriod
from aiat.db.repositories.baselines import BaselineRepository
from aiat.db.repositories.tax_simulation import TaxSimulationRepository

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

_GIT_SHA = "abc1234"
_PERIOD_START = "2026-01-01T00:00:00Z"
_PERIOD_END = "2026-04-01T00:00:00Z"
_TICK_AT = "2026-01-15T12:00:00Z"


async def _seed_experiment(session: AsyncSession) -> uuid.UUID:
    exp_id = uuid.uuid4()
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
    return exp_id


async def _seed_model(session: AsyncSession) -> str:
    model_id = f"openai-gpt4o-{uuid.uuid4().hex[:8]}"
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
    return model_id


def _make_outcome(
    realized_pnl_gross: Decimal,
    sum_fees: Decimal,
    sum_funding: Decimal,
) -> Outcome:
    """Create a transient Outcome for aggregation (not persisted to DB)."""
    return Outcome(
        id=uuid.uuid4(),
        position_id=uuid.uuid4(),
        opening_action_id=uuid.uuid4(),
        opening_run_id=uuid.uuid4(),
        closing_run_id=uuid.uuid4(),
        experiment_id=uuid.uuid4(),
        model_id="dummy",
        symbol="BTC",
        realized_pnl_gross_usd=realized_pnl_gross,
        sum_fees_usd=sum_fees,
        sum_funding_usd=sum_funding,
        pnl_net_fee_usd=realized_pnl_gross - sum_fees,
        pnl_net_fee_funding_usd=realized_pnl_gross - sum_fees - sum_funding,
        pnl_net_fee_funding_tax_sim_usd=Decimal("0"),
        was_profitable_net=realized_pnl_gross > sum_fees + sum_funding,
        holding_duration_min=60,
        decision_action_confidence=Decimal("0.7500"),
        decision_action_time_horizon_min=60,
        horizon_met=True,
    )


# ---------------------------------------------------------------------------
# BaselineRepository tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_baseline_config_success(db_session: AsyncSession) -> None:
    """register_baseline_config creates a row with correct config_hash."""
    exp_id = await _seed_experiment(db_session)
    repo = BaselineRepository(db_session)

    config_json = {"ema_fast": 20, "ema_slow": 50, "sl_pct": 0.03}
    config_id = await repo.register_baseline_config(
        str(exp_id), "naive_momentum_ema_20_50", config_json
    )

    assert uuid.UUID(config_id)

    # Verify hash is SHA-256 of canonical JSON
    expected_hash = hashlib.sha256(
        json.dumps(config_json, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()

    from sqlalchemy import select

    row = (
        await db_session.execute(
            select(BaselineConfig).where(BaselineConfig.id == uuid.UUID(config_id))
        )
    ).scalar_one()

    assert row.baseline_name == "naive_momentum_ema_20_50"
    assert row.config_hash == expected_hash
    assert row.experiment_id == exp_id


@pytest.mark.asyncio
async def test_register_baseline_config_hash_deterministic(db_session: AsyncSession) -> None:
    """Same config_json always produces the same config_hash regardless of dict ordering."""
    exp_id = await _seed_experiment(db_session)
    repo = BaselineRepository(db_session)

    config_a = {"z_key": 99, "a_key": 1}
    config_id = await repo.register_baseline_config(str(exp_id), "buy_and_hold", config_a)

    from sqlalchemy import select

    row = (
        await db_session.execute(
            select(BaselineConfig).where(BaselineConfig.id == uuid.UUID(config_id))
        )
    ).scalar_one()

    # Canonical JSON sorts keys alphabetically
    expected = hashlib.sha256(
        json.dumps({"a_key": 1, "z_key": 99}, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()
    assert row.config_hash == expected


@pytest.mark.asyncio
async def test_get_baseline_config_found(db_session: AsyncSession) -> None:
    """get_baseline_config returns the row for an existing (experiment, name) pair."""
    exp_id = await _seed_experiment(db_session)
    repo = BaselineRepository(db_session)

    await repo.register_baseline_config(str(exp_id), "cash", {"initial_usd": 10000})

    bc = await repo.get_baseline_config(str(exp_id), "cash")

    assert bc is not None
    assert bc.baseline_name == "cash"
    assert bc.experiment_id == exp_id


@pytest.mark.asyncio
async def test_get_baseline_config_not_found(db_session: AsyncSession) -> None:
    """get_baseline_config returns None when the config does not exist."""
    exp_id = await _seed_experiment(db_session)
    repo = BaselineRepository(db_session)

    result = await repo.get_baseline_config(str(exp_id), "buy_and_hold")

    assert result is None


@pytest.mark.asyncio
async def test_register_baseline_config_duplicate_raises(db_session: AsyncSession) -> None:
    """Second register for the same (experiment_id, baseline_name) raises IntegrityError."""
    exp_id = await _seed_experiment(db_session)
    repo = BaselineRepository(db_session)

    await repo.register_baseline_config(str(exp_id), "cash", {})

    with pytest.raises(IntegrityError):
        await repo.register_baseline_config(str(exp_id), "cash", {"extra": True})


@pytest.mark.asyncio
async def test_register_baseline_config_invalid_name_raises(db_session: AsyncSession) -> None:
    """register_baseline_config with an invalid baseline_name raises IntegrityError."""
    exp_id = await _seed_experiment(db_session)
    repo = BaselineRepository(db_session)

    with pytest.raises(IntegrityError):
        await repo.register_baseline_config(str(exp_id), "invalid_strategy", {})


@pytest.mark.asyncio
async def test_persist_equity_snapshot_success(db_session: AsyncSession) -> None:
    """persist_equity_snapshot creates a row linked to the correct baseline_config."""
    exp_id = await _seed_experiment(db_session)
    repo = BaselineRepository(db_session)

    config_id = await repo.register_baseline_config(str(exp_id), "buy_and_hold", {"hold": True})

    snap_id = await repo.persist_equity_snapshot(
        baseline_config_id=config_id,
        tick_id="2026-01-15T12:00:00",
        tick_at=_TICK_AT,
        equity_usd=Decimal("10500.00"),
        pnl_usd_cumulative=Decimal("500.00"),
        raw_state={"btc_price": 50000},
    )

    assert uuid.UUID(snap_id)

    from sqlalchemy import select

    row = (
        await db_session.execute(
            select(BaselineEquitySnapshot).where(BaselineEquitySnapshot.id == uuid.UUID(snap_id))
        )
    ).scalar_one()

    assert row.baseline_name == "buy_and_hold"
    assert row.experiment_id == exp_id
    assert row.equity_usd == Decimal("10500.00")
    assert row.pnl_usd_cumulative == Decimal("500.00")


@pytest.mark.asyncio
async def test_persist_equity_snapshot_invalid_config_id_raises(
    db_session: AsyncSession,
) -> None:
    """persist_equity_snapshot raises ValueError for a non-existent baseline_config_id."""
    repo = BaselineRepository(db_session)

    with pytest.raises(ValueError, match="not found"):
        await repo.persist_equity_snapshot(
            baseline_config_id=str(uuid.uuid4()),
            tick_id="2026-01-15T12:00:00",
            tick_at=_TICK_AT,
            equity_usd=Decimal("10000.00"),
            pnl_usd_cumulative=Decimal("0"),
            raw_state={},
        )


@pytest.mark.asyncio
async def test_list_equity_history_ordered(db_session: AsyncSession) -> None:
    """list_equity_history returns snapshots ordered by tick_at ascending."""
    exp_id = await _seed_experiment(db_session)
    repo = BaselineRepository(db_session)

    config_id = await repo.register_baseline_config(str(exp_id), "cash", {"initial_usd": 10000})

    tick_times = [
        ("tick-1", "2026-01-15T12:00:00Z"),
        ("tick-2", "2026-01-15T12:15:00Z"),
        ("tick-3", "2026-01-15T12:30:00Z"),
    ]
    for tick_id, tick_at in tick_times:
        await repo.persist_equity_snapshot(
            baseline_config_id=config_id,
            tick_id=tick_id,
            tick_at=tick_at,
            equity_usd=Decimal("10000.00"),
            pnl_usd_cumulative=Decimal("0"),
            raw_state={},
        )

    history = await repo.list_equity_history(str(exp_id), "cash")

    assert len(history) == 3
    assert [h.tick_id for h in history] == ["tick-1", "tick-2", "tick-3"]


# ---------------------------------------------------------------------------
# TaxSimulationRepository tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_and_persist_period_with_profit(db_session: AsyncSession) -> None:
    """compute_and_persist_period correctly aggregates profitable outcomes."""
    exp_id = await _seed_experiment(db_session)
    model_id = await _seed_model(db_session)
    repo = TaxSimulationRepository(db_session)

    # Two profitable outcomes: gross 100+80, fees 2+1.5, funding 0.5+0.5
    outcomes = [
        _make_outcome(Decimal("100.00"), Decimal("2.00"), Decimal("0.50")),
        _make_outcome(Decimal("80.00"), Decimal("1.50"), Decimal("0.50")),
    ]

    period_id = await repo.compute_and_persist_period(
        experiment_id=str(exp_id),
        model_id=model_id,
        quarter_label="Q1-2026",
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
        outcomes_in_period=outcomes,
    )

    assert uuid.UUID(period_id)

    from sqlalchemy import select

    row = (
        await db_session.execute(
            select(TaxSimPeriod).where(TaxSimPeriod.id == uuid.UUID(period_id))
        )
    ).scalar_one()

    assert row.total_pnl_gross_usd == Decimal("180.00")
    assert row.total_fees_usd == Decimal("3.50")
    assert row.total_funding_usd == Decimal("1.00")
    # net = 180 - 3.50 - 1.00 = 175.50
    assert row.taxable_base_usd == Decimal("175.50")
    # tax = 175.50 × 0.26
    assert row.tax_due_usd == Decimal("175.50") * Decimal("0.26")
    assert row.tax_rate_pct == Decimal("0.26")
    assert row.n_positions_closed == 2
    assert row.model_id == model_id


@pytest.mark.asyncio
async def test_compute_and_persist_period_with_net_loss(db_session: AsyncSession) -> None:
    """taxable_base is clamped to 0 when net PnL is negative (algebraic compensation §4.3)."""
    exp_id = await _seed_experiment(db_session)
    model_id = await _seed_model(db_session)
    repo = TaxSimulationRepository(db_session)

    # Net loss: gross 10, fees 5, funding 10 → net = -5
    outcomes = [_make_outcome(Decimal("10.00"), Decimal("5.00"), Decimal("10.00"))]

    period_id = await repo.compute_and_persist_period(
        experiment_id=str(exp_id),
        model_id=model_id,
        quarter_label="Q2-2026",
        period_start="2026-04-01T00:00:00Z",
        period_end="2026-07-01T00:00:00Z",
        outcomes_in_period=outcomes,
    )

    from sqlalchemy import select

    row = (
        await db_session.execute(
            select(TaxSimPeriod).where(TaxSimPeriod.id == uuid.UUID(period_id))
        )
    ).scalar_one()

    assert row.taxable_base_usd == Decimal("0")
    assert row.tax_due_usd == Decimal("0")


@pytest.mark.asyncio
async def test_compute_and_persist_period_empty_outcomes(db_session: AsyncSession) -> None:
    """compute_and_persist_period with empty outcomes produces all-zero financials."""
    exp_id = await _seed_experiment(db_session)
    model_id = await _seed_model(db_session)
    repo = TaxSimulationRepository(db_session)

    period_id = await repo.compute_and_persist_period(
        experiment_id=str(exp_id),
        model_id=model_id,
        quarter_label="Q3-2026",
        period_start="2026-07-01T00:00:00Z",
        period_end="2026-10-01T00:00:00Z",
        outcomes_in_period=[],
    )

    from sqlalchemy import select

    row = (
        await db_session.execute(
            select(TaxSimPeriod).where(TaxSimPeriod.id == uuid.UUID(period_id))
        )
    ).scalar_one()

    assert row.total_pnl_gross_usd == Decimal("0")
    assert row.total_fees_usd == Decimal("0")
    assert row.taxable_base_usd == Decimal("0")
    assert row.tax_due_usd == Decimal("0")
    assert row.n_positions_closed == 0


@pytest.mark.asyncio
async def test_list_for_model_returns_periods_ordered(db_session: AsyncSession) -> None:
    """list_for_model returns periods for the model ordered by period_start ascending."""
    exp_id = await _seed_experiment(db_session)
    model_id = await _seed_model(db_session)
    repo = TaxSimulationRepository(db_session)

    quarters = [
        ("Q1-2026", "2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z"),
        ("Q2-2026", "2026-04-01T00:00:00Z", "2026-07-01T00:00:00Z"),
    ]
    for label, start, end in quarters:
        await repo.compute_and_persist_period(
            experiment_id=str(exp_id),
            model_id=model_id,
            quarter_label=label,
            period_start=start,
            period_end=end,
            outcomes_in_period=[],
        )

    periods = await repo.list_for_model(model_id)

    assert len(periods) == 2
    assert [p.quarter_label for p in periods] == ["Q1-2026", "Q2-2026"]


@pytest.mark.asyncio
async def test_list_for_model_excludes_other_model(db_session: AsyncSession) -> None:
    """list_for_model returns only model A's periods, never model B's.

    Two real models each persist a tax period for the same quarter. Querying for
    model A must return exactly A's row and never B's — guaranteeing the test goes
    RED if the `model_id` filter in the repository is dropped.
    """
    exp_id = await _seed_experiment(db_session)
    model_a = await _seed_model(db_session)
    model_b = await _seed_model(db_session)
    assert model_a != model_b  # sanity: two distinct models
    repo = TaxSimulationRepository(db_session)

    period_a_id = await repo.compute_and_persist_period(
        experiment_id=str(exp_id),
        model_id=model_a,
        quarter_label="Q1-2026",
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
        outcomes_in_period=[],
    )
    period_b_id = await repo.compute_and_persist_period(
        experiment_id=str(exp_id),
        model_id=model_b,
        quarter_label="Q1-2026",
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
        outcomes_in_period=[],
    )

    periods = await repo.list_for_model(model_a)

    # Exactly model A's single period — B's row for the same quarter is excluded.
    assert len(periods) == 1
    assert periods[0].model_id == model_a
    assert str(periods[0].id) == period_a_id
    returned_ids = {str(p.id) for p in periods}
    assert period_b_id not in returned_ids
    assert all(p.model_id != model_b for p in periods)


@pytest.mark.asyncio
async def test_compute_and_persist_period_duplicate_quarter_raises(
    db_session: AsyncSession,
) -> None:
    """Duplicate (experiment, model, quarter_label) raises IntegrityError."""
    exp_id = await _seed_experiment(db_session)
    model_id = await _seed_model(db_session)
    repo = TaxSimulationRepository(db_session)

    await repo.compute_and_persist_period(
        experiment_id=str(exp_id),
        model_id=model_id,
        quarter_label="Q1-2026",
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
        outcomes_in_period=[],
    )

    with pytest.raises(IntegrityError):
        await repo.compute_and_persist_period(
            experiment_id=str(exp_id),
            model_id=model_id,
            quarter_label="Q1-2026",
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            outcomes_in_period=[],
        )
