"""E2E: open-position lookups are scoped to the CURRENT experiment (ADR-0039).

Reproduces the cross-experiment leakage proven on the 2026-08-04 prod dump. On 2026-07-29
18:30 UTC a FLAT decided inside the smoke experiment (6666…) closed position
``f2207b7b-bf25-4cb9-89f7-959a238f35f1`` — an ARCHIVED M6.1 row (experiment 5555…, cn-cheap
ETH, opened 2026-07-27) — because the FLAT bookkeeping looked up open positions by
``(model_id, symbol)`` with NO ``experiment_id`` filter and took the oldest match. It wrote the
smoke's exit price (1909.60) onto the M6.1 entry, and from that moment the closure queue was
permanently shifted by one: every later FLAT closed the previous position's row (~147
contaminated ``model_close`` rows) and left one orphan per active ``(model, symbol)`` — 7
permanent zombies whose SL/TP triggers had been cancelled on-chain by the very FLAT that
"closed" them, so the ClosureReconciler (ADR-0038) could never match their oids either.
The same unscoped read fed the DB↔chain detector (ADR-0025): the 8 open M6.1 rows raised
``ChainDivergence`` from tick 1 of the smoke.

Both tests are mutation-proof — they are RED on the pre-fix code (drop the
``Position.experiment_id`` predicate in ``list_open_for_model`` and they fail):

  * (a) ``test_flat_closes_current_experiment_row_not_the_archived_one`` — the exact 29/07
    scenario: two open rows for the same (model, symbol), one per experiment. Pre-fix the
    archived row is closed and stamped with the current exit price.
  * (b) ``test_detection_ignores_archived_experiment_rows`` — an archived open row + a flat
    chain raises ZERO divergences for the current experiment. Pre-fix: one ``zombie_row``.
    Paired with a positive control so "detection found nothing" can never pass vacuously.

Self-contained commit-safe seed (fresh uuids per test + on_conflict on the shared
prompt-template PK): the loop reads through a real session, so per-function rollback
isolation is unavailable.
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
from aiat.db.models.outcome import Outcome
from aiat.db.models.position import Position
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from aiat.domain.enums import EntryType, OrderKind, Side
from aiat.domain.schemas import ActionDecision, OpenPositionSummary, PortfolioState, TradeDecision
from aiat.execution.hyperliquid_client import OrderResult
from aiat.orchestration.decision_loop import DecisionLoop

_PT_TEXT = "You are a trading agent (cross-experiment scoping e2e)."
_PT_HASH = hashlib.sha256(_PT_TEXT.encode()).hexdigest()
_GIT_SHA = "abc1234"

# The archived M6.1 row was opened on 27/07; the smoke FLAT that wrongly closed it fired
# 29/07 18:30 UTC at 1909.60 (the smoke's ETH price, not M6.1's).
_ARCHIVED_OPENED_AT = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)
_CURRENT_OPENED_AT = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)
_FLAT_AT = datetime(2026, 7, 29, 18, 30, tzinfo=UTC)
_SMOKE_EXIT_PRICE = Decimal("1909.60")
_ARCHIVED_ENTRY_PRICE = Decimal("2100.00")
_CURRENT_ENTRY_PRICE = Decimal("1950.00")


@pytest_asyncio.fixture(scope="function")
async def session_factory(db_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


# --------------------------------------------------------------------------- #
# Seed helpers                                                                 #
# --------------------------------------------------------------------------- #


async def _seed_model(session: AsyncSession) -> str:
    """One shared model (the cn-cheap of the incident) used by BOTH experiments."""
    model_id = f"cn-cheap-{uuid.uuid4().hex[:8]}"
    session.add(
        Model(
            id=model_id,
            provider="deepseek",
            model_name_api="deepseek-chat",
            tier="cheap_alt",
            geography="CN",
            wallet_address=f"0x{uuid.uuid4().hex}",
            pricing_input_usd_per_1m=Decimal("0.140000"),
            pricing_output_usd_per_1m=Decimal("0.280000"),
        )
    )
    await session.execute(
        pg_insert(PromptTemplate)
        .values(
            sha256_hash=_PT_HASH,
            label="scoping-pt-shared",
            template_text=_PT_TEXT,
            confidence_def="Probability that the action yields positive PnL.",
            controlled_signals=[],
        )
        .on_conflict_do_nothing(index_elements=["sha256_hash"])
    )
    await session.flush()
    return model_id


async def _seed_experiment(session: AsyncSession, label: str, started_at: datetime) -> uuid.UUID:
    exp_id = uuid.uuid4()
    session.add(
        Experiment(
            id=exp_id,
            name=f"{label}-{exp_id.hex[:8]}",
            started_at=started_at,
            git_commit_sha=_GIT_SHA,
            config_snapshot={},
        )
    )
    await session.flush()
    return exp_id


async def _seed_run(
    session: AsyncSession, exp_id: uuid.UUID, model_id: str, at: datetime
) -> uuid.UUID:
    """A run + its context snapshot (runs are UNIQUE on experiment+model+scheduled_for)."""
    snap_id = uuid.uuid4()
    tick_id = at.isoformat()
    session.add(
        ContextSnapshot(
            id=snap_id,
            experiment_id=exp_id,
            tick_id=tick_id,
            tick_at=at,
            context_hash=hashlib.sha256(f"{exp_id}{tick_id}".encode()).hexdigest(),
            context_json={},
            source_timestamps={},
            build_duration_ms=10,
        )
    )
    await session.flush()
    run_id = uuid.uuid4()
    session.add(
        Run(
            id=run_id,
            experiment_id=exp_id,
            model_id=model_id,
            tick_id=tick_id,
            scheduled_for=at,
            run_started_at=at,
            status="success",
            prompt_template_hash=_PT_HASH,
            rendered_prompt_hash="aabbcc",
            context_snapshot_id=snap_id,
            schema_version="v2",
            git_commit_sha=_GIT_SHA,
        )
    )
    await session.flush()
    return run_id


async def _seed_decision(
    session: AsyncSession, exp_id: uuid.UUID, model_id: str, run_id: uuid.UUID, at: datetime
) -> uuid.UUID:
    decision_id = uuid.uuid4()
    session.add(
        Decision(
            id=decision_id,
            run_id=run_id,
            experiment_id=exp_id,
            model_id=model_id,
            decided_at=at,
            portfolio_reasoning="Seeded decision for the cross-experiment scoping scenario.",
            risk_assessment="Seeded.",
            latency_ms=1000,
            raw_payload={},
        )
    )
    await session.flush()
    return decision_id


async def _seed_long_action(
    session: AsyncSession,
    exp_id: uuid.UUID,
    model_id: str,
    run_id: uuid.UUID,
    decision_id: uuid.UUID,
    symbol: str,
) -> uuid.UUID:
    action_id = uuid.uuid4()
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
    return action_id


async def _seed_passive_action(
    session: AsyncSession,
    exp_id: uuid.UUID,
    model_id: str,
    run_id: uuid.UUID,
    decision_id: uuid.UUID,
    symbol: str,
    side: str,
) -> uuid.UUID:
    """A HOLD/FLAT decision_action row (chk_hold_flat_no_sizing: no size/leverage/SL/TP)."""
    action_id = uuid.uuid4()
    session.add(
        DecisionAction(
            id=action_id,
            decision_id=decision_id,
            experiment_id=exp_id,
            model_id=model_id,
            run_id=run_id,
            symbol=symbol,
            confidence=Decimal("0.6000"),
            time_horizon_min=60,
            action_reasoning=f"Passive {side} on {symbol} for this tick; no new risk taken.",
            action_key_signals=[],
            side_requested=side,
            leverage_requested=Decimal("0"),
            size_pct_requested=Decimal("0"),
            stop_loss_pct=None,
            take_profit_pct=None,
            entry_type="none",
            side_executed=side,
            leverage_executed=Decimal("0"),
            size_pct_executed=Decimal("0"),
        )
    )
    await session.flush()
    return action_id


async def _seed_open_position(
    session: AsyncSession,
    exp_id: uuid.UUID,
    model_id: str,
    symbol: str,
    opened_at: datetime,
    entry_price: Decimal,
    size_units: Decimal,
) -> uuid.UUID:
    """One OPEN LONG position (+ its own run/decision/opening action) in `exp_id`."""
    run_id = await _seed_run(session, exp_id, model_id, opened_at)
    decision_id = await _seed_decision(session, exp_id, model_id, run_id, opened_at)
    action_id = await _seed_long_action(session, exp_id, model_id, run_id, decision_id, symbol)
    position_id = uuid.uuid4()
    notional = size_units * entry_price
    session.add(
        Position(
            id=position_id,
            experiment_id=exp_id,
            model_id=model_id,
            opening_run_id=run_id,
            symbol=symbol,
            side="LONG",
            opening_action_id=action_id,
            opened_at=opened_at,
            entry_price=entry_price,
            size_units=size_units,
            leverage=Decimal("3.00"),
            notional_value_usd=notional,
            initial_margin_usd=notional / Decimal("3"),
            stop_loss_price=entry_price * Decimal("0.98"),
            take_profit_price=entry_price * Decimal("1.04"),
        )
    )
    await session.flush()
    return position_id


def _loop(
    session_factory: async_sessionmaker[AsyncSession],
    exp_id: uuid.UUID,
    model_id: str,
    hl_client: object | None = None,
) -> DecisionLoop:
    # The paths under test read only settings.experiment_id / model_id → light stub settings.
    settings = SimpleNamespace(model_id=model_id, experiment_id=str(exp_id))
    return DecisionLoop(
        settings=settings,  # type: ignore[arg-type]
        llm_client=AsyncMock(),
        hl_client=hl_client if hl_client is not None else AsyncMock(),  # type: ignore[arg-type]
        session_factory=session_factory,
    )


def _hold(symbol: str) -> ActionDecision:
    return ActionDecision(
        symbol=symbol,  # type: ignore[arg-type]
        side=Side.HOLD,
        leverage=Decimal("0"),
        size_pct=Decimal("0"),
        entry_type=EntryType.NONE,
        confidence=Decimal("0.6"),
        time_horizon_min=60,
        action_reasoning=f"No edge on {symbol} this tick; stay flat and preserve margin.",
    )


def _flat(symbol: str) -> ActionDecision:
    return ActionDecision(
        symbol=symbol,  # type: ignore[arg-type]
        side=Side.FLAT,
        leverage=Decimal("0"),
        size_pct=Decimal("0"),
        entry_type=EntryType.NONE,
        confidence=Decimal("0.6"),
        time_horizon_min=60,
        action_reasoning=f"Momentum faded on {symbol}; close the position and de-risk.",
    )


def _eth_summary(size_units: Decimal, entry_price: Decimal) -> OpenPositionSummary:
    return OpenPositionSummary(
        symbol="ETH",
        side="LONG",
        entry_price=entry_price,
        current_price=_SMOKE_EXIT_PRICE,
        size_units=size_units,
        leverage=Decimal("3.00"),
        unrealized_pnl_usd=Decimal("0"),
        age_minutes=30,
    )


def _portfolio(open_positions: list[OpenPositionSummary]) -> PortfolioState:
    return PortfolioState(
        equity_usd=Decimal("1000"),
        available_usd=Decimal("800"),
        margin_used_usd=Decimal("200"),
        n_open_positions=len(open_positions),
        unrealized_pnl_usd=Decimal("0"),
        open_positions=open_positions,
    )


def _close_order(size_units: Decimal) -> OrderResult:
    return OrderResult(
        hl_order_id=str(uuid.uuid4()),
        client_order_id=str(uuid.uuid4()),
        order_kind=OrderKind.CLOSE,
        status="filled",
        requested_price=None,
        filled_price=_SMOKE_EXIT_PRICE,
        requested_size_units=size_units,
        filled_size_units=size_units,
        slippage_bps=Decimal("5"),
        fee_usd=Decimal("0.35"),
        raw_response={},
    )


# --------------------------------------------------------------------------- #
# (a) FLAT bookkeeping — the 2026-07-29 18:30 incident                         #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_flat_closes_current_experiment_row_not_the_archived_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A FLAT closes THIS experiment's row; the archived experiment's row is untouched.

    TRIPWIRE for ADR-0039. Pre-fix ``list_open_for_model`` filtered on ``(model_id,
    closed_at IS NULL)`` only and the FLAT took the oldest match, so the archived M6.1 row
    was closed and stamped with the smoke's exit price (1909.60) while the row the model
    actually closed on-chain stayed open forever — the head of the 147-row shift.
    """
    current_size = Decimal("0.40")
    async with session_factory() as s:
        model_id = await _seed_model(s)
        archived_exp = await _seed_experiment(s, "m6-1-archived", datetime(2026, 7, 20, tzinfo=UTC))
        current_exp = await _seed_experiment(s, "m6-2-smoke", datetime(2026, 7, 29, tzinfo=UTC))
        archived_pos = await _seed_open_position(
            s,
            archived_exp,
            model_id,
            "ETH",
            _ARCHIVED_OPENED_AT,
            _ARCHIVED_ENTRY_PRICE,
            Decimal("0.50"),
        )
        current_pos = await _seed_open_position(
            s, current_exp, model_id, "ETH", _CURRENT_OPENED_AT, _CURRENT_ENTRY_PRICE, current_size
        )
        # The tick that decides FLAT on ETH (HOLD on the other two symbols).
        flat_run = await _seed_run(s, current_exp, model_id, _FLAT_AT)
        flat_decision = await _seed_decision(s, current_exp, model_id, flat_run, _FLAT_AT)
        flat_action = await _seed_passive_action(
            s, current_exp, model_id, flat_run, flat_decision, "ETH", "FLAT"
        )
        for sym in ("BTC", "SOL"):
            await _seed_passive_action(
                s, current_exp, model_id, flat_run, flat_decision, sym, "HOLD"
            )
        await s.commit()

    hl_client = AsyncMock()
    hl_client.execute_action = AsyncMock(return_value=[_close_order(current_size)])
    loop = _loop(session_factory, current_exp, model_id, hl_client)
    decision = TradeDecision(
        portfolio_reasoning=(
            "ETH momentum faded into the close; exit the position and hold the rest of the book."
        ),
        risk_assessment="De-risking ETH; BTC and SOL stay untouched this tick.",
        actions=[_flat("ETH"), _hold("BTC"), _hold("SOL")],
    )

    async with session_factory() as s:
        failed = await loop._execute_actions(
            s,
            str(flat_run),
            decision,
            # The chain shows ONE netted ETH position — the current experiment's.
            _portfolio([_eth_summary(current_size, _CURRENT_ENTRY_PRICE)]),
        )
        await s.commit()
    assert failed == 0

    async with session_factory() as s:
        current = await s.get(Position, current_pos)
        archived = await s.get(Position, archived_pos)
        assert current is not None and archived is not None

        # The CURRENT experiment's row is the one that gets closed.
        assert current.closed_at is not None
        assert current.exit_price == _SMOKE_EXIT_PRICE
        assert current.close_reason == "model_close"
        assert current.closing_action_id == flat_action

        # The ARCHIVED row is byte-for-byte untouched — no exit, no PnL, still open.
        assert archived.closed_at is None
        assert archived.exit_price is None
        assert archived.realized_pnl_usd is None
        assert archived.close_reason is None
        assert archived.closing_action_id is None
        assert archived.entry_price == _ARCHIVED_ENTRY_PRICE

        # Exactly one outcome, and it belongs to the current row (pre-fix it was the
        # archived row's, with the smoke exit price against the M6.1 entry).
        outcomes = (await s.scalars(select(Outcome).where(Outcome.model_id == model_id))).all()
        assert len(outcomes) == 1
        assert outcomes[0].position_id == current_pos
        assert outcomes[0].experiment_id == current_exp


# --------------------------------------------------------------------------- #
# (b) DB↔chain detection (ADR-0025) ignores archived experiments' open rows    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_detection_ignores_archived_experiment_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An archived experiment's open row + a flat chain → ZERO divergences.

    TRIPWIRE for ADR-0039: pre-fix the 8 still-open M6.1 rows were compared against the
    smoke wallet's chain state and raised ``ChainDivergence`` from tick 1. With scoping they
    are invisible by construction, which is what makes "annotate not repair" permanently safe.
    """
    async with session_factory() as s:
        model_id = await _seed_model(s)
        archived_exp = await _seed_experiment(s, "m6-1-archived", datetime(2026, 7, 20, tzinfo=UTC))
        current_exp = await _seed_experiment(s, "m6-2-smoke", datetime(2026, 7, 29, tzinfo=UTC))
        await _seed_open_position(
            s,
            archived_exp,
            model_id,
            "ETH",
            _ARCHIVED_OPENED_AT,
            _ARCHIVED_ENTRY_PRICE,
            Decimal("0.50"),
        )
        await s.commit()

    loop = _loop(session_factory, current_exp, model_id)
    async with session_factory() as s:
        await loop._reconcile_chain_state(s, _portfolio([]))  # chain flat
        await s.commit()

    async with session_factory() as s:
        errors = (await s.scalars(select(Error).where(Error.model_id == model_id))).all()
        assert errors == [], (
            "archived experiment's open rows leaked into DB↔chain detection: "
            f"{[e.error_kind for e in errors]}"
        )


@pytest.mark.asyncio
async def test_detection_still_flags_current_experiment_zombie(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Positive control for the test above: the SAME zombie shape inside the CURRENT
    experiment must still raise exactly one ``zombie_row`` — scoping must narrow the query,
    not disable detection."""
    async with session_factory() as s:
        model_id = await _seed_model(s)
        current_exp = await _seed_experiment(s, "m6-2-smoke", datetime(2026, 7, 29, tzinfo=UTC))
        await _seed_open_position(
            s,
            current_exp,
            model_id,
            "ETH",
            _CURRENT_OPENED_AT,
            _CURRENT_ENTRY_PRICE,
            Decimal("0.40"),
        )
        await s.commit()

    loop = _loop(session_factory, current_exp, model_id)
    async with session_factory() as s:
        await loop._reconcile_chain_state(s, _portfolio([]))  # chain flat
        await s.commit()

    async with session_factory() as s:
        errors = (await s.scalars(select(Error).where(Error.model_id == model_id))).all()
        assert len(errors) == 1
        assert errors[0].error_kind == "ChainDivergence"
        divs = errors[0].context["divergences"]  # type: ignore[index]
        assert len(divs) == 1
        assert divs[0]["kind"] == "zombie_row"
        assert divs[0]["symbol"] == "ETH"
