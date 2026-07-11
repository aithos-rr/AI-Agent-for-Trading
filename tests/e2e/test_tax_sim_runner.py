"""E2E: TaxSimRunner aggregates closed-period outcomes into tax_sim_periods (ADR-0033).

Drives TaxSimRunner.run against a REAL Postgres. TRIPWIRE: TaxSimulationRepository existed but
had NO production caller (like the baselines), so tax_sim_periods stayed empty — these tests
assert a row IS computed for the closed period, with the 0.33 rate override, and is idempotent.

Commit-safe self-contained seed (unique ids + on_conflict on the shared prompt-template PK):
the runner opens its own committing session, so per-function rollback isolation is unavailable.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.db.models.action import DecisionAction
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.decision import Decision
from aiat.db.models.experiment import Experiment
from aiat.db.models.model import Model
from aiat.db.models.outcome import Outcome
from aiat.db.models.position import Position
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from aiat.db.models.tax_sim import TaxSimPeriod
from aiat.orchestration.tax_sim_runner import TaxSimRunner

_PT_TEXT = "You are a trading agent (tax e2e)."
_PT_HASH = hashlib.sha256(_PT_TEXT.encode()).hexdigest()
_GIT_SHA = "abc1234"

# Daily period: now → previous full day [2026-07-10, 2026-07-11)
_NOW = datetime(2026, 7, 11, 0, 5, tzinfo=UTC)
_IN_PERIOD = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_OUT_PERIOD = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture(scope="function")
async def session_factory(db_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


async def _seed_outcomes(
    session: AsyncSession,
    specs: list[tuple[str, datetime, Decimal, Decimal, Decimal]],
) -> tuple[uuid.UUID, str]:
    """Seed one experiment/model + one Outcome per spec (symbol, created_at, gross, fees, funding).

    Each outcome gets its own action + (open) position to satisfy the FKs / uniqueness; the
    position is left open (all close fields NULL) which satisfies chk_position_closed_consistency
    — the runner only reads outcomes, so the position's closed state is irrelevant here.
    """
    exp_id = uuid.uuid4()
    model_id = f"openai-gpt4o-tax-{uuid.uuid4().hex[:8]}"
    snap_id = uuid.uuid4()
    run_id = uuid.uuid4()
    decision_id = uuid.uuid4()

    session.add(
        Experiment(
            id=exp_id,
            name=f"tax-exp-{exp_id.hex[:8]}",
            started_at=datetime.now(UTC),
            git_commit_sha=_GIT_SHA,
            config_snapshot={},
        )
    )
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
    await session.execute(
        pg_insert(PromptTemplate)
        .values(
            sha256_hash=_PT_HASH,
            label="tax-pt-shared",
            template_text=_PT_TEXT,
            confidence_def="Probability that the action yields positive PnL.",
            controlled_signals=[],
        )
        .on_conflict_do_nothing(index_elements=["sha256_hash"])
    )
    session.add(
        ContextSnapshot(
            id=snap_id,
            experiment_id=exp_id,
            tick_id=_NOW.isoformat(),
            tick_at=_NOW,
            context_hash=hashlib.sha256(b"tax").hexdigest(),
            context_json={},
            source_timestamps={},
            build_duration_ms=100,
        )
    )
    await session.flush()
    session.add(
        Run(
            id=run_id,
            experiment_id=exp_id,
            model_id=model_id,
            tick_id=_NOW.isoformat(),
            scheduled_for=_NOW,
            run_started_at=_NOW,
            status="success",
            prompt_template_hash=_PT_HASH,
            rendered_prompt_hash="aabbcc",
            context_snapshot_id=snap_id,
            schema_version="v1",
            git_commit_sha=_GIT_SHA,
        )
    )
    await session.flush()
    session.add(
        Decision(
            id=decision_id,
            run_id=run_id,
            experiment_id=exp_id,
            model_id=model_id,
            decided_at=_NOW,
            portfolio_reasoning="x",
            risk_assessment="y",
            latency_ms=1,
            raw_payload={},
        )
    )
    await session.flush()

    for symbol, created_at, gross, fees, funding in specs:
        action_id = uuid.uuid4()
        position_id = uuid.uuid4()
        net_fee = gross - fees
        net_ff = net_fee - funding
        session.add(
            DecisionAction(
                id=action_id,
                decision_id=decision_id,
                experiment_id=exp_id,
                model_id=model_id,
                run_id=run_id,
                symbol=symbol,
                confidence=Decimal("0.7000"),
                time_horizon_min=60,
                action_reasoning="Enter LONG with defined risk on strong momentum.",
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
        session.add(
            Position(
                id=position_id,
                experiment_id=exp_id,
                model_id=model_id,
                opening_run_id=run_id,
                symbol=symbol,
                side="LONG",
                opening_action_id=action_id,
                opened_at=created_at,
                entry_price=Decimal("100.00"),
                size_units=Decimal("1.0"),
                leverage=Decimal("3.00"),
                notional_value_usd=Decimal("100.00"),
                initial_margin_usd=Decimal("33.33"),
                stop_loss_price=Decimal("98.00"),
                take_profit_price=Decimal("104.00"),
            )
        )
        await session.flush()
        session.add(
            Outcome(
                id=uuid.uuid4(),
                position_id=position_id,
                opening_action_id=action_id,
                opening_run_id=run_id,
                closing_run_id=run_id,
                experiment_id=exp_id,
                model_id=model_id,
                symbol=symbol,
                realized_pnl_gross_usd=gross,
                sum_fees_usd=fees,
                sum_funding_usd=funding,
                pnl_net_fee_usd=net_fee,
                pnl_net_fee_funding_usd=net_ff,
                pnl_net_fee_funding_tax_sim_usd=Decimal("0"),
                was_profitable_net=net_ff > 0,
                holding_duration_min=30,
                decision_action_confidence=Decimal("0.7000"),
                decision_action_time_horizon_min=60,
                horizon_met=True,
                created_at=created_at,  # override server_default so we control the period bucket
            )
        )
        await session.flush()
    return exp_id, model_id


@pytest.mark.asyncio
async def test_computes_tax_for_closed_daily_period(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        exp_id, model_id = await _seed_outcomes(
            s,
            [
                ("BTC", _IN_PERIOD, Decimal("10"), Decimal("1"), Decimal("0.5")),
                ("ETH", _IN_PERIOD, Decimal("-4"), Decimal("0.5"), Decimal("0.2")),
                ("SOL", _OUT_PERIOD, Decimal("100"), Decimal("1"), Decimal("1")),  # excluded
            ],
        )
        await s.commit()

    runner = TaxSimRunner(session_factory, str(exp_id), Decimal("0.33"), "daily")
    result = await runner.run(_NOW)

    assert result.period_label == "2026-07-10"
    assert result.created == 1

    async with session_factory() as s:
        row = await s.scalar(
            select(TaxSimPeriod).where(
                TaxSimPeriod.experiment_id == exp_id, TaxSimPeriod.model_id == model_id
            )
        )
        assert row is not None
        assert row.quarter_label == "2026-07-10"
        assert row.n_positions_closed == 2  # SOL excluded (out of period)
        assert row.total_pnl_gross_usd == Decimal("6")  # 10 + (-4)
        assert row.total_fees_usd == Decimal("1.5")
        assert row.total_funding_usd == Decimal("0.7")
        assert row.taxable_base_usd == Decimal("3.8")  # max(0, 6 - 1.5 - 0.7)
        assert row.tax_rate_pct == Decimal("0.33")  # 0.33 override, NOT the schema 0.26 default
        assert row.tax_due_usd == Decimal("1.254")  # 3.8 * 0.33


@pytest.mark.asyncio
async def test_taxable_base_floored_at_zero_when_net_loss(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§4.3 algebraic compensation: a net-loss period has taxable_base = 0, tax_due = 0."""
    async with session_factory() as s:
        exp_id, model_id = await _seed_outcomes(
            s, [("BTC", _IN_PERIOD, Decimal("-20"), Decimal("1"), Decimal("0.5"))]
        )
        await s.commit()

    runner = TaxSimRunner(session_factory, str(exp_id), Decimal("0.33"), "daily")
    await runner.run(_NOW)

    async with session_factory() as s:
        row = await s.scalar(select(TaxSimPeriod).where(TaxSimPeriod.experiment_id == exp_id))
        assert row is not None
        assert row.taxable_base_usd == Decimal("0")
        assert row.tax_due_usd == Decimal("0")


@pytest.mark.asyncio
async def test_run_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        exp_id, _ = await _seed_outcomes(
            s, [("BTC", _IN_PERIOD, Decimal("10"), Decimal("1"), Decimal("0.5"))]
        )
        await s.commit()

    runner = TaxSimRunner(session_factory, str(exp_id), Decimal("0.33"), "daily")
    first = await runner.run(_NOW)
    second = await runner.run(_NOW)

    assert first.created == 1
    assert second.created == 0
    assert second.skipped == 1  # UNIQUE (exp, model, quarter_label) → check-then-skip

    async with session_factory() as s:
        count = await s.scalar(
            select(func.count())
            .select_from(TaxSimPeriod)
            .where(TaxSimPeriod.experiment_id == exp_id)
        )
        assert count == 1
