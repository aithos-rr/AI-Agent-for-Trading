"""E2E: DecisionLoop._reconcile_chain_state logs ChainDivergence + proceeds (ADR-0025).

Drives the loop's reconciliation step against a REAL Postgres. TRIPWIRE: pre-fix there was NO
DB↔chain comparison, so a position closed on-chain but still OPEN in the DB left no trace. The
headline scenario is the empirical cn-premium zombie (2026-07-11): TWO open BTC LONG rows in the
DB but ONE netted BTC position on-chain — a row-by-row check misses it, the netted sum flags it.

Self-contained commit-safe seed (unique ids + on_conflict on the shared prompt-template PK):
the reconciliation reads via a real session, so per-function rollback isolation is unavailable.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.db.models.action import DecisionAction
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.decision import Decision
from aiat.db.models.error import Error
from aiat.db.models.experiment import Experiment
from aiat.db.models.model import Model
from aiat.db.models.position import Position
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from aiat.domain.schemas import OpenPositionSummary, PortfolioState
from aiat.orchestration.decision_loop import DecisionLoop

_PT_TEXT = "You are a trading agent (chain-recon e2e)."
_PT_HASH = hashlib.sha256(_PT_TEXT.encode()).hexdigest()
_GIT_SHA = "abc1234"
_NOW = datetime(2026, 7, 11, tzinfo=UTC)


@pytest_asyncio.fixture(scope="function")
async def session_factory(db_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


async def _seed_open_btc_positions(
    session: AsyncSession, sizes: list[Decimal]
) -> tuple[uuid.UUID, str, list[uuid.UUID]]:
    """Seed one experiment/model + one OPEN BTC LONG position per size. Returns
    (experiment_id, model_id, [position_ids])."""
    exp_id = uuid.uuid4()
    model_id = f"cn-premium-{uuid.uuid4().hex[:8]}"
    snap_id = uuid.uuid4()

    session.add(
        Experiment(
            id=exp_id,
            name=f"recon-exp-{exp_id.hex[:8]}",
            started_at=datetime.now(UTC),
            git_commit_sha=_GIT_SHA,
            config_snapshot={},
        )
    )
    session.add(
        Model(
            id=model_id,
            provider="anthropic",
            model_name_api="claude",
            tier="premium",
            geography="CN",
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
            label="recon-pt-shared",
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
            context_hash=hashlib.sha256(b"recon").hexdigest(),
            context_json={},
            source_timestamps={},
            build_duration_ms=100,
        )
    )
    await session.flush()

    position_ids: list[uuid.UUID] = []
    for i, size in enumerate(sizes):
        # Distinct run + decision + action per position: runs are UNIQUE on
        # (experiment, model, scheduled_for) and decision_actions on (decision_id, symbol),
        # so two BTC rows need their own run/decision (mirrors two ticks reopening BTC).
        run_id = uuid.uuid4()
        decision_id = uuid.uuid4()
        action_id = uuid.uuid4()
        position_id = uuid.uuid4()
        scheduled = _NOW.replace(day=10 + i)
        session.add(
            Run(
                id=run_id,
                experiment_id=exp_id,
                model_id=model_id,
                tick_id=_NOW.isoformat(),
                scheduled_for=scheduled,
                run_started_at=scheduled,
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
                decided_at=scheduled,
                portfolio_reasoning="x",
                risk_assessment="y",
                latency_ms=1,
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
                run_id=run_id,
                symbol="BTC",
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
                symbol="BTC",
                side="LONG",
                opening_action_id=action_id,
                opened_at=_NOW.replace(day=10 + i),  # distinct opened_at per row
                entry_price=Decimal("62690.2") if i else Decimal("63403.0"),
                size_units=size,
                leverage=Decimal("3.00"),
                notional_value_usd=size * Decimal("62690"),
                initial_margin_usd=Decimal("33.33"),
                stop_loss_price=Decimal("61000"),
                take_profit_price=Decimal("65000"),
            )
        )
        await session.flush()
        position_ids.append(position_id)
    return exp_id, model_id, position_ids


def _loop(
    session_factory: async_sessionmaker[AsyncSession], exp_id: uuid.UUID, model_id: str
) -> DecisionLoop:
    # _reconcile_chain_state only reads settings.model_id / experiment_id → light stub settings.
    settings = SimpleNamespace(model_id=model_id, experiment_id=str(exp_id))
    return DecisionLoop(
        settings=settings,  # type: ignore[arg-type]
        llm_client=AsyncMock(),
        hl_client=AsyncMock(),
        session_factory=session_factory,
    )


def _portfolio(open_positions: list[OpenPositionSummary]) -> PortfolioState:
    return PortfolioState(
        equity_usd=Decimal("10000"),
        available_usd=Decimal("10000"),
        margin_used_usd=Decimal("0"),
        n_open_positions=len(open_positions),
        unrealized_pnl_usd=Decimal("0"),
        open_positions=open_positions,
    )


def _chain_btc(size: str) -> OpenPositionSummary:
    return OpenPositionSummary(
        symbol="BTC",
        side="LONG",
        entry_price=Decimal("62690.2"),
        current_price=Decimal("62700"),
        size_units=Decimal(size),
        leverage=Decimal("3.00"),
        unrealized_pnl_usd=Decimal("0"),
        age_minutes=15,
    )


@pytest.mark.asyncio
async def test_zombie_two_db_rows_one_chain_position(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Empirical cn-premium case: DB has TWO open BTC LONG rows (zombie 0.01 + real 0.00425),
    chain nets ONE BTC LONG 0.00425 → ONE zombie_row divergence, delta 0.01, BOTH position_ids
    in context for manual repair. A row-by-row check would have missed the zombie."""
    async with session_factory() as s:
        exp_id, model_id, pids = await _seed_open_btc_positions(
            s, [Decimal("0.01000"), Decimal("0.00425")]
        )
        await s.commit()

    loop = _loop(session_factory, exp_id, model_id)
    async with session_factory() as s:
        await loop._reconcile_chain_state(s, _portfolio([_chain_btc("0.00425")]))
        await s.commit()

    async with session_factory() as s:
        errors = (
            await s.scalars(
                select(Error).where(
                    Error.model_id == model_id, Error.error_kind == "ChainDivergence"
                )
            )
        ).all()
        assert len(errors) == 1
        divs = errors[0].context["divergences"]  # type: ignore[index]
        assert len(divs) == 1
        div = divs[0]
        assert div["symbol"] == "BTC"
        assert div["kind"] == "zombie_row"
        assert div["chain_size"] == "0.00425"
        # DB sum 0.01425000 − chain 0.00425 (DB size_units is Numeric(20,8) → scale 8)
        assert div["delta"] == "0.01000000"
        reported = {p["position_id"] for p in div["db_positions"]}
        assert reported == {str(p) for p in pids}  # both rows reported for repair


@pytest.mark.asyncio
async def test_chain_flat_single_row_is_zombie(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        exp_id, model_id, _ = await _seed_open_btc_positions(s, [Decimal("1.0")])
        await s.commit()

    loop = _loop(session_factory, exp_id, model_id)
    async with session_factory() as s:
        await loop._reconcile_chain_state(s, _portfolio([]))  # chain flat
        await s.commit()

    async with session_factory() as s:
        errors = (await s.scalars(select(Error).where(Error.model_id == model_id))).all()
        assert len(errors) == 1
        assert errors[0].context["divergences"][0]["kind"] == "zombie_row"  # type: ignore[index]


@pytest.mark.asyncio
async def test_in_sync_writes_no_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        exp_id, model_id, _ = await _seed_open_btc_positions(s, [Decimal("1.0")])
        await s.commit()

    loop = _loop(session_factory, exp_id, model_id)
    async with session_factory() as s:
        await loop._reconcile_chain_state(s, _portfolio([_chain_btc("1.0")]))  # matches DB
        await s.commit()

    async with session_factory() as s:
        errors = (await s.scalars(select(Error).where(Error.model_id == model_id))).all()
        assert errors == []
