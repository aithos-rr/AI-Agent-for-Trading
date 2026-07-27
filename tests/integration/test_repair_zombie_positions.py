"""Integration tests for scripts/repair_zombie_positions.py (ADR-0035, one-shot M6.1 repair).

Seeds the 5 corrupted zombie states of experiment 5555… into an ephemeral Postgres, then
exercises the four contract guarantees:

  (a) dry-run writes NOTHING;
  (b) --apply produces exactly the target on-chain values (and correct derived outcome fields
      via OutcomeResolver reuse + convention-2 closing_run + convention-5 funding reassign);
  (c) a second --apply after (b) SKIPs all 5 rows and writes nothing (idempotent);
  (d) a divergent pre-state SKIPs only that row while the others still repair.

The script opens its OWN committing session (like the real one-shot run), so the seed is
committed via a dedicated engine and every table is truncated after each test.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.db.models.action import DecisionAction
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.decision import Decision
from aiat.db.models.experiment import Experiment
from aiat.db.models.fee_event import FeeEvent
from aiat.db.models.funding_event import FundingEvent
from aiat.db.models.model import Model
from aiat.db.models.order import Order
from aiat.db.models.outcome import Outcome
from aiat.db.models.position import Position
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from scripts.repair_zombie_positions import CASES, EXPERIMENT_ID, RepairAbort, repair

_PT_TEXT = "You are a trading agent."
_PT_HASH = "a" * 64
_TICK = "2026-07-01T00:00:00"
_GIT = "abc1234"


def _dt(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)  # type: ignore[arg-type]


# Per-case seed knobs (opened_at / horizon / confidence / opening fee) that drive the derived
# outcome fields. position_id / model_id / symbol / pre-state come from the script's CASES.
@dataclass(frozen=True)
class Knob:
    opened_at: datetime
    entry_price: Decimal
    size_units: Decimal
    confidence: Decimal
    time_horizon_min: int
    taker_open_fee: Decimal


_KNOBS: dict[str, Knob] = {
    "CASE 1": Knob(
        _dt(2026, 7, 13, 12, 0, 0),
        Decimal("62880"),
        Decimal("0.00674"),
        Decimal("0.7000"),
        120,
        Decimal("0.25"),
    ),
    "CASE 2": Knob(
        _dt(2026, 7, 11, 0, 0, 0),
        Decimal("63200"),
        Decimal("0.00634"),
        Decimal("0.6500"),
        120,
        Decimal("0.24"),
    ),
    "CASE 3": Knob(
        _dt(2026, 7, 17, 12, 0, 0),
        Decimal("62896"),
        Decimal("0.00681"),
        Decimal("0.7200"),
        360,
        Decimal("0.19"),
    ),
    "CASE 4": Knob(
        _dt(2026, 7, 20, 12, 0, 0),
        Decimal("64014"),
        Decimal("0.0068"),
        Decimal("0.6000"),
        # 300 < holding (361 min) so horizon_met resolves False — gives that assertion teeth.
        300,
        Decimal("0.20"),
    ),
    "CASE 5": Knob(
        _dt(2026, 7, 19, 0, 0, 0),
        Decimal("74.601"),
        Decimal("4.66"),
        Decimal("0.5500"),
        240,
        Decimal("0.15"),
    ),
}


@dataclass
class Seeded:
    """Ids/values captured at seed time that the assertions need."""

    conv2_run: dict[str, uuid.UUID] = field(default_factory=dict)  # case label -> expected run
    taker_open_fee: dict[str, Decimal] = field(default_factory=dict)
    funding_target_id: uuid.UUID | None = None
    funding_move_ids: list[uuid.UUID] = field(default_factory=list)
    funding_stay_sum: Decimal = Decimal("0")
    case3_funding: Decimal = Decimal("0")


def _case(label: str):  # type: ignore[no-untyped-def]
    return next(c for c in CASES if c.label == label)


async def _model(session: AsyncSession, model_id: str, geo: str) -> None:
    session.add(
        Model(
            id=model_id,
            provider="openai" if geo == "USA" else "deepseek",
            model_name_api="m",
            tier="premium",
            geography=geo,
            wallet_address=f"0x{uuid.uuid4().hex}",
            pricing_input_usd_per_1m=Decimal("1.0"),
            pricing_output_usd_per_1m=Decimal("1.0"),
        )
    )
    await session.flush()


async def _run(
    session: AsyncSession,
    model_id: str,
    snap_id: uuid.UUID,
    started: datetime,
    status: str = "success",
) -> uuid.UUID:
    rid = uuid.uuid4()
    session.add(
        Run(
            id=rid,
            experiment_id=EXPERIMENT_ID,
            model_id=model_id,
            tick_id=_TICK,
            scheduled_for=started,
            run_started_at=started,
            status=status,
            prompt_template_hash=_PT_HASH,
            rendered_prompt_hash="hh",
            context_snapshot_id=snap_id,
            schema_version="v2",
            git_commit_sha=_GIT,
        )
    )
    await session.flush()
    return rid


async def _opening_action(
    session: AsyncSession, model_id: str, run_id: uuid.UUID, symbol: str, k: Knob
) -> uuid.UUID:
    did = uuid.uuid4()
    session.add(
        Decision(
            id=did,
            run_id=run_id,
            experiment_id=EXPERIMENT_ID,
            model_id=model_id,
            decided_at=k.opened_at,
            portfolio_reasoning="x",
            risk_assessment="y",
            latency_ms=1,
            raw_payload={},
        )
    )
    await session.flush()
    aid = uuid.uuid4()
    session.add(
        DecisionAction(
            id=aid,
            decision_id=did,
            experiment_id=EXPERIMENT_ID,
            model_id=model_id,
            run_id=run_id,
            symbol=symbol,
            confidence=k.confidence,
            time_horizon_min=k.time_horizon_min,
            action_reasoning="r",
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
    return aid


async def _flat_close_action(
    session: AsyncSession, model_id: str, run_id: uuid.UUID, symbol: str, action_id: uuid.UUID
) -> None:
    did = uuid.uuid4()
    session.add(
        Decision(
            id=did,
            run_id=run_id,
            experiment_id=EXPERIMENT_ID,
            model_id=model_id,
            decided_at=_dt(2026, 7, 14, 17, 0, 0),
            portfolio_reasoning="close",
            risk_assessment="derisk",
            latency_ms=1,
            raw_payload={},
        )
    )
    await session.flush()
    session.add(
        DecisionAction(
            id=action_id,
            decision_id=did,
            experiment_id=EXPERIMENT_ID,
            model_id=model_id,
            run_id=run_id,
            symbol=symbol,
            confidence=Decimal("0.5000"),
            time_horizon_min=60,
            action_reasoning="flat",
            action_key_signals=[],
            side_requested="FLAT",
            leverage_requested=Decimal("0"),
            size_pct_requested=Decimal("0"),
            stop_loss_pct=None,
            take_profit_pct=None,
            entry_type="none",
            side_executed="FLAT",
            leverage_executed=Decimal("0"),
            size_pct_executed=Decimal("0"),
            execution_status="not_applicable",
            executed=False,
        )
    )
    await session.flush()


async def _orders_and_open_fee(
    session: AsyncSession,
    model_id: str,
    run_id: uuid.UUID,
    action_id: uuid.UUID,
    pos_id: uuid.UUID,
    symbol: str,
    k: Knob,
) -> None:
    """Entry (filled, taker_open fee) + SL + TP trigger orders — mirrors open_position."""
    entry = Order(
        id=uuid.uuid4(),
        decision_action_id=action_id,
        experiment_id=EXPERIMENT_ID,
        model_id=model_id,
        run_id=run_id,
        symbol=symbol,
        order_kind="entry",
        hl_order_id=str(uuid.uuid4()),
        status="filled",
        requested_size_units=k.size_units,
        filled_size_units=k.size_units,
        filled_price=k.entry_price,
        raw_order_response={},
        submitted_at=k.opened_at,
        filled_at=k.opened_at,
    )
    session.add(entry)
    await session.flush()
    session.add(
        FeeEvent(
            id=uuid.uuid4(),
            order_id=entry.id,
            position_id=pos_id,
            experiment_id=EXPERIMENT_ID,
            model_id=model_id,
            run_id=run_id,
            fee_type="taker_open",
            fee_usd=k.taker_open_fee,
            occurred_at=k.opened_at,
        )
    )
    for kind in ("stop_loss", "take_profit"):
        session.add(
            Order(
                id=uuid.uuid4(),
                decision_action_id=action_id,
                experiment_id=EXPERIMENT_ID,
                model_id=model_id,
                run_id=run_id,
                symbol=symbol,
                order_kind=kind,
                hl_order_id=str(uuid.uuid4()),
                status="triggered",
                requested_size_units=k.size_units,
                raw_order_response={},
                submitted_at=k.opened_at,
            )
        )
    await session.flush()


async def _open_position(
    session: AsyncSession,
    snap_id: uuid.UUID,
    pos_id: uuid.UUID,
    model_id: str,
    symbol: str,
    k: Knob,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed an OPEN position + opening run/decision/action/orders. Returns (run_id, action_id)."""
    run_id = await _run(session, model_id, snap_id, k.opened_at)
    action_id = await _opening_action(session, model_id, run_id, symbol, k)
    notional = k.size_units * k.entry_price
    session.add(
        Position(
            id=pos_id,
            experiment_id=EXPERIMENT_ID,
            model_id=model_id,
            opening_run_id=run_id,
            symbol=symbol,
            side="LONG",
            opening_action_id=action_id,
            opened_at=k.opened_at,
            entry_price=k.entry_price,
            size_units=k.size_units,
            leverage=Decimal("3.00"),
            notional_value_usd=notional,
            initial_margin_usd=notional / Decimal("3"),
            stop_loss_price=k.entry_price * Decimal("0.98"),
            take_profit_price=k.entry_price * Decimal("1.04"),
        )
    )
    await session.flush()
    await _orders_and_open_fee(session, model_id, run_id, action_id, pos_id, symbol, k)
    return run_id, action_id


async def seed(engine_url: str) -> Seeded:  # noqa: C901 - linear seed, readability over splitting
    """Seed all 5 corrupt states + FK scaffold; commit; return captured ids/values."""
    engine = create_async_engine(engine_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    out = Seeded()
    try:
        async with factory() as s:
            s.add(
                Experiment(
                    id=EXPERIMENT_ID,
                    name="m6-smoke",
                    started_at=_dt(2026, 7, 1),
                    git_commit_sha=_GIT,
                    config_snapshot={},
                )
            )
            await s.flush()
            await _model(s, "usa-premium", "USA")
            await _model(s, "cn-premium", "CN")
            s.add(
                PromptTemplate(
                    sha256_hash=_PT_HASH,
                    label="pt",
                    template_text=_PT_TEXT,
                    confidence_def="d",
                    controlled_signals=[],
                )
            )
            await s.flush()
            snap_id = uuid.uuid4()
            s.add(
                ContextSnapshot(
                    id=snap_id,
                    experiment_id=EXPERIMENT_ID,
                    tick_id=_TICK,
                    tick_at=_dt(2026, 7, 1),
                    context_hash="dead",
                    context_json={},
                    source_timestamps={},
                    build_duration_ms=1,
                )
            )
            await s.flush()

            # --- open every case's position (all start OPEN) ---
            pos_ids = {c.label: c.position_id for c in CASES}
            open_action: dict[str, uuid.UUID] = {}
            open_run: dict[str, uuid.UUID] = {}
            for label, k in _KNOBS.items():
                c = _case(label)
                run_id, action_id = await _open_position(
                    s, snap_id, pos_ids[label], c.model_id, c.symbol, k
                )
                open_action[label] = action_id
                open_run[label] = run_id
                out.taker_open_fee[label] = k.taker_open_fee

            # --- corrupt the two CORRECTION rows into their model_close zombie state ---
            for label in ("CASE 1", "CASE 2"):
                c = _case(label)
                pos = await s.get(Position, c.position_id)
                assert pos is not None
                # a distinct closing run + FLAT closing action (the wrong close)
                close_run = await _run(
                    s, c.model_id, snap_id, c.pre_closed_at or c.closed_at + timedelta(days=1)
                )
                flat_id = c.pre_closing_action_id or uuid.uuid4()
                await _flat_close_action(s, c.model_id, close_run, c.symbol, flat_id)
                pos.closed_at = c.pre_closed_at or _dt(2026, 7, 14, 17, 0, 50, 463984)
                pos.exit_price = c.pre_exit_price
                pos.realized_pnl_usd = c.pre_realized_pnl_usd
                pos.close_reason = "model_close"
                pos.closing_action_id = flat_id
                # the mis-applied taker_close fee (linked to the CLOSE order of the wrong close)
                close_order = Order(
                    id=uuid.uuid4(),
                    decision_action_id=flat_id,
                    experiment_id=EXPERIMENT_ID,
                    model_id=c.model_id,
                    run_id=close_run,
                    symbol=c.symbol,
                    order_kind="close",
                    hl_order_id=str(uuid.uuid4()),
                    status="filled",
                    requested_size_units=_KNOBS[label].size_units,
                    filled_size_units=_KNOBS[label].size_units,
                    filled_price=c.pre_exit_price,
                    raw_order_response={},
                    submitted_at=pos.closed_at,
                    filled_at=pos.closed_at,
                )
                s.add(close_order)
                await s.flush()
                s.add(
                    FeeEvent(
                        id=uuid.uuid4(),
                        order_id=close_order.id,
                        position_id=pos.id,
                        experiment_id=EXPERIMENT_ID,
                        model_id=c.model_id,
                        run_id=close_run,
                        fee_type="taker_close",
                        fee_usd=c.pre_close_fee_usd,
                        occurred_at=pos.closed_at,
                    )
                )
                # the corrupt outcome (confidence/time_horizon match the opening action)
                k = _KNOBS[label]
                s.add(
                    Outcome(
                        id=c.outcome_id,
                        position_id=pos.id,
                        opening_action_id=open_action[label],
                        opening_run_id=open_run[label],
                        closing_run_id=close_run,
                        experiment_id=EXPERIMENT_ID,
                        model_id=c.model_id,
                        symbol=c.symbol,
                        realized_pnl_gross_usd=c.pre_outcome_gross,
                        sum_fees_usd=Decimal("0.5"),
                        sum_funding_usd=Decimal("0"),
                        pnl_net_fee_usd=Decimal("0"),
                        pnl_net_fee_funding_usd=Decimal("0"),
                        pnl_net_fee_funding_tax_sim_usd=Decimal("0"),
                        was_profitable_net=True,
                        holding_duration_min=999,
                        decision_action_confidence=k.confidence,
                        decision_action_time_horizon_min=k.time_horizon_min,
                        horizon_met=False,
                    )
                )
                await s.flush()

            # --- CASE 1 funding: 2 rows stay (created_at <= real close), 2 reassigned (> close) ---
            c1 = _case("CASE 1")
            real_close = c1.closed_at
            # the next usa-premium BTC position (13:45), the reassignment target (convention 5)
            next_id = uuid.uuid4()
            next_k = Knob(
                _dt(2026, 7, 13, 13, 45, 0),
                Decimal("62900"),
                Decimal("0.00674"),
                Decimal("0.7"),
                120,
                Decimal("0.22"),
            )
            await _open_position(s, snap_id, next_id, "usa-premium", "BTC", next_k)
            out.funding_target_id = next_id
            stay = [
                (real_close - timedelta(hours=1), Decimal("0.01")),
                (real_close - timedelta(minutes=30), Decimal("0.02")),
            ]
            move = [
                (real_close + timedelta(minutes=17), Decimal("0.03")),
                (real_close + timedelta(hours=1), Decimal("0.04")),
            ]
            out.funding_stay_sum = Decimal("0.03")
            for created, amt in stay + move:
                fid = uuid.uuid4()
                s.add(
                    FundingEvent(
                        id=fid,
                        position_id=c1.position_id,
                        experiment_id=EXPERIMENT_ID,
                        model_id="usa-premium",
                        funding_rate=Decimal("0.00001000"),
                        funding_amount_usd=amt,
                        funding_period_start=created - timedelta(hours=1),
                        funding_period_end=created,
                        created_at=created,
                    )
                )
                if (created, amt) in move:
                    out.funding_move_ids.append(fid)
            await s.flush()

            # --- CASE 3 funding: one row on the position (tests the funding sum on a closure) ---
            c3 = _case("CASE 3")
            out.case3_funding = Decimal("0.05")
            s.add(
                FundingEvent(
                    id=uuid.uuid4(),
                    position_id=c3.position_id,
                    experiment_id=EXPERIMENT_ID,
                    model_id="usa-premium",
                    funding_rate=Decimal("0.00001000"),
                    funding_amount_usd=Decimal("0.05"),
                    funding_period_start=_dt(2026, 7, 17, 15, 0, 0),
                    funding_period_end=_dt(2026, 7, 17, 16, 0, 0),
                    created_at=_dt(2026, 7, 17, 16, 0, 0),
                )
            )
            await s.flush()

            # --- convention-2 closing runs: one per case, 2s after the real/target close ---
            # Cases 4-5 closed AFTER the usa-premium agent died (2026-07-19 ~01:30), so their
            # first post-close run is a FAILED run — convention 2's "any status" MUST anchor to
            # it. Seeding these as 'failed' gives the assertions teeth: a regression narrowing
            # _find_closing_run to status='success' would mis-anchor CASE 5 to a later success
            # run and find NO run for CASE 4 (→ ERROR/abort), breaking these tests.
            conv2_status = {"CASE 4": "failed", "CASE 5": "failed"}
            for label in ("CASE 1", "CASE 2", "CASE 3", "CASE 4", "CASE 5"):
                c = _case(label)
                rid = await _run(
                    s,
                    c.model_id,
                    snap_id,
                    c.closed_at + timedelta(seconds=2),
                    status=conv2_status.get(label, "success"),
                )
                out.conv2_run[label] = rid

            await s.commit()
    finally:
        await engine.dispose()
    return out


async def _all_tables(engine_url: str) -> list[str]:
    engine = create_async_engine(engine_url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename <> 'alembic_version'"
                )
            )
            return [r[0] for r in rows]
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def clean(db_url: str):  # type: ignore[no-untyped-def]
    """Truncate every table after each test (the script commits to a shared session DB)."""
    yield
    tables = await _all_tables(db_url)
    engine = create_async_engine(db_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()


async def _fetch_pos(db_url: str, pos_id: uuid.UUID) -> Position | None:
    engine = create_async_engine(db_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as s:
            return await s.get(Position, pos_id)
    finally:
        await engine.dispose()


async def _fetch_outcome(db_url: str, pos_id: uuid.UUID) -> Outcome | None:
    engine = create_async_engine(db_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as s:
            return await s.scalar(select(Outcome).where(Outcome.position_id == pos_id))
    finally:
        await engine.dispose()


async def _fetch_close_fee(db_url: str, pos_id: uuid.UUID) -> FeeEvent | None:
    engine = create_async_engine(db_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as s:
            return await s.scalar(
                select(FeeEvent).where(
                    FeeEvent.position_id == pos_id, FeeEvent.fee_type == "taker_close"
                )
            )
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# (a) dry-run writes nothing                                                  #
# --------------------------------------------------------------------------- #


async def test_dry_run_writes_nothing(db_url: str, clean: None) -> None:
    s = await seed(db_url)
    summary = await repair(db_url, "testnet", apply=False)
    assert summary == {"corrected": 2, "closed": 3, "skipped": 0, "errored": 0}

    # corrections untouched, closures still open, no new outcomes for closures
    for label in ("CASE 1", "CASE 2"):
        c = _case(label)
        pos = await _fetch_pos(db_url, c.position_id)
        assert pos is not None
        assert pos.close_reason == "model_close"
        assert pos.exit_price == c.pre_exit_price
        assert pos.closing_action_id is not None
    for label in ("CASE 3", "CASE 4", "CASE 5"):
        c = _case(label)
        pos = await _fetch_pos(db_url, c.position_id)
        assert pos is not None and pos.closed_at is None
        assert await _fetch_outcome(db_url, c.position_id) is None

    # airtight "writes NOTHING": the mis-applied fee and the reassignable funding are untouched
    c1 = _case("CASE 1")
    fee1 = await _fetch_close_fee(db_url, c1.position_id)
    assert fee1 is not None and fee1.fee_usd == Decimal("0.259336")  # not the target 0.253915
    engine = create_async_engine(db_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as sess:
            for fid in s.funding_move_ids:
                fe = await sess.get(FundingEvent, fid)
                assert fe is not None and fe.position_id == c1.position_id  # not reassigned
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# (b) apply produces exactly the target values                                #
# --------------------------------------------------------------------------- #


async def test_apply_sets_target_values(db_url: str, clean: None) -> None:
    s = await seed(db_url)
    summary = await repair(db_url, "testnet", apply=True)
    assert summary == {"corrected": 2, "closed": 3, "skipped": 0, "errored": 0}

    # ---- CASE 1 correction ----
    c1 = _case("CASE 1")
    p1 = await _fetch_pos(db_url, c1.position_id)
    assert p1 is not None
    assert p1.close_reason == "stop_loss"
    assert p1.closing_action_id is None
    assert p1.exit_price == Decimal("62280")
    assert p1.realized_pnl_usd == Decimal("-8.784576")
    assert p1.closed_at == c1.closed_at
    fee1 = await _fetch_close_fee(db_url, c1.position_id)
    assert fee1 is not None and fee1.fee_usd == Decimal("0.253915")
    o1 = await _fetch_outcome(db_url, c1.position_id)
    assert o1 is not None
    assert o1.realized_pnl_gross_usd == Decimal("-8.784576")
    exp_fees_1 = s.taker_open_fee["CASE 1"] + Decimal("0.253915")
    assert o1.sum_fees_usd == exp_fees_1
    assert o1.sum_funding_usd == s.funding_stay_sum  # late funding reassigned away
    assert o1.pnl_net_fee_usd == Decimal("-8.784576") - exp_fees_1
    assert o1.pnl_net_fee_funding_usd == o1.pnl_net_fee_usd - s.funding_stay_sum
    assert o1.was_profitable_net is False
    assert o1.holding_duration_min == 103  # 12:00:00 -> 13:43:40.756
    assert o1.horizon_met is True
    assert o1.closing_run_id == s.conv2_run["CASE 1"]
    # confidence/time_horizon untouched
    assert o1.decision_action_confidence == _KNOBS["CASE 1"].confidence
    assert o1.decision_action_time_horizon_min == 120

    # funding reassignment (convention 5): both late rows now point at the next position
    engine = create_async_engine(db_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as sess:
            for fid in s.funding_move_ids:
                fe = await sess.get(FundingEvent, fid)
                assert fe is not None and fe.position_id == s.funding_target_id
            moved_sum = await sess.scalar(
                select(FundingEvent.funding_amount_usd).where(
                    FundingEvent.id == s.funding_move_ids[0]
                )
            )
            assert moved_sum == Decimal("0.03")
    finally:
        await engine.dispose()

    # ---- CASE 2 correction ----
    c2 = _case("CASE 2")
    p2 = await _fetch_pos(db_url, c2.position_id)
    assert p2 is not None
    assert p2.close_reason == "stop_loss" and p2.closing_action_id is None
    assert p2.exit_price == Decimal("62500")
    assert p2.realized_pnl_usd == Decimal("-5.72502")
    assert p2.closed_at == c2.closed_at
    fee2 = await _fetch_close_fee(db_url, c2.position_id)
    assert fee2 is not None and fee2.fee_usd == Decimal("0.178312")
    o2 = await _fetch_outcome(db_url, c2.position_id)
    assert o2 is not None
    assert o2.realized_pnl_gross_usd == Decimal("-5.72502")
    assert o2.sum_funding_usd == Decimal("0")  # no funding for case 2
    assert o2.closing_run_id == s.conv2_run["CASE 2"]

    # ---- CASE 3 closure ----
    c3 = _case("CASE 3")
    p3 = await _fetch_pos(db_url, c3.position_id)
    assert p3 is not None
    assert p3.closed_at == c3.closed_at
    assert p3.exit_price == Decimal("64056.3")
    assert p3.close_reason == "take_profit"
    assert p3.closing_action_id is None
    assert p3.realized_pnl_usd == Decimal("7.90145")
    fee3 = await _fetch_close_fee(db_url, c3.position_id)
    assert fee3 is not None and fee3.fee_usd == Decimal("0.196297")
    assert fee3.run_id == s.conv2_run["CASE 3"]
    o3 = await _fetch_outcome(db_url, c3.position_id)
    assert o3 is not None
    assert o3.realized_pnl_gross_usd == Decimal("7.90145")
    exp_fees_3 = s.taker_open_fee["CASE 3"] + Decimal("0.196297")
    assert o3.sum_fees_usd == exp_fees_3
    assert o3.sum_funding_usd == s.case3_funding
    assert o3.pnl_net_fee_funding_usd == Decimal("7.90145") - exp_fees_3 - s.case3_funding
    assert o3.was_profitable_net is True
    assert o3.closing_run_id == s.conv2_run["CASE 3"]
    assert o3.decision_action_confidence == _KNOBS["CASE 3"].confidence  # from opening action
    assert o3.decision_action_time_horizon_min == 360

    # ---- CASE 4 closure. Its conv2 run is a FAILED run (agent-death scenario): picking it
    # proves convention 2's "any status" clause. horizon_met is False here (holding 361 > 300).
    c4 = _case("CASE 4")
    p4 = await _fetch_pos(db_url, c4.position_id)
    assert p4 is not None and p4.exit_price == Decimal("65567.1")
    assert p4.realized_pnl_usd == Decimal("10.56115") and p4.close_reason == "take_profit"
    o4 = await _fetch_outcome(db_url, c4.position_id)
    assert o4 is not None
    assert o4.closing_run_id == s.conv2_run["CASE 4"]  # the FAILED run, per convention 2
    assert o4.holding_duration_min == 361  # 07-20 12:00:00 -> 18:01:55.471
    assert o4.horizon_met is False  # 361 > 300
    assert o4.decision_action_time_horizon_min == 300

    # ---- CASE 5 closure. Its conv2 run is also a FAILED run.
    c5 = _case("CASE 5")
    p5 = await _fetch_pos(db_url, c5.position_id)
    assert p5 is not None and p5.exit_price == Decimal("75.962")
    assert p5.realized_pnl_usd == Decimal("6.34226") and p5.closed_at == c5.closed_at
    o5 = await _fetch_outcome(db_url, c5.position_id)
    assert o5 is not None and o5.closing_run_id == s.conv2_run["CASE 5"]  # FAILED run, conv 2


# --------------------------------------------------------------------------- #
# (c) re-run after apply -> all 5 SKIP, no further writes                     #
# --------------------------------------------------------------------------- #


async def test_rerun_after_apply_is_idempotent(db_url: str, clean: None) -> None:
    await seed(db_url)
    await repair(db_url, "testnet", apply=True)

    # snapshot the repaired state
    before = {c.position_id: await _fetch_pos(db_url, c.position_id) for c in CASES}
    before_vals = {
        pid: (p.close_reason, p.exit_price, p.realized_pnl_usd, p.closed_at)
        for pid, p in before.items()
        if p is not None
    }

    summary = await repair(db_url, "testnet", apply=True)
    assert summary == {"corrected": 0, "closed": 0, "skipped": 5, "errored": 0}

    after = {c.position_id: await _fetch_pos(db_url, c.position_id) for c in CASES}
    after_vals = {
        pid: (p.close_reason, p.exit_price, p.realized_pnl_usd, p.closed_at)
        for pid, p in after.items()
        if p is not None
    }
    assert after_vals == before_vals

    # still exactly one outcome + one taker_close fee per case (no duplicate inserts)
    engine = create_async_engine(db_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as sess:
            for c in CASES:
                outs = (
                    await sess.scalars(select(Outcome).where(Outcome.position_id == c.position_id))
                ).all()
                assert len(outs) == 1
                fees = (
                    await sess.scalars(
                        select(FeeEvent).where(
                            FeeEvent.position_id == c.position_id,
                            FeeEvent.fee_type == "taker_close",
                        )
                    )
                ).all()
                assert len(fees) == 1
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# (d) divergent pre-state -> SKIP only that row, others repair                 #
# --------------------------------------------------------------------------- #


async def test_divergent_prestate_skips_only_that_row(db_url: str, clean: None) -> None:
    await seed(db_url)
    # Tamper CASE 5's size_units so its pre-state assertion diverges.
    c5 = _case("CASE 5")
    engine = create_async_engine(db_url)
    try:
        async with async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )() as sess:
            p5 = await sess.get(Position, c5.position_id)
            assert p5 is not None
            p5.size_units = Decimal("5.0")  # expected 4.66 -> divergent
            await sess.commit()
    finally:
        await engine.dispose()

    summary = await repair(db_url, "testnet", apply=True)
    assert summary == {"corrected": 2, "closed": 2, "skipped": 1, "errored": 0}

    # CASE 5 untouched (still open, no outcome)
    p5b = await _fetch_pos(db_url, c5.position_id)
    assert p5b is not None and p5b.closed_at is None
    assert await _fetch_outcome(db_url, c5.position_id) is None

    # the other four DID repair
    for label in ("CASE 1", "CASE 2", "CASE 3", "CASE 4"):
        c = _case(label)
        pos = await _fetch_pos(db_url, c.position_id)
        assert pos is not None and pos.closed_at is not None
        assert pos.closing_action_id is None
        assert await _fetch_outcome(db_url, c.position_id) is not None


# --------------------------------------------------------------------------- #
# network guard (inv #9) — no DB needed                                       #
# --------------------------------------------------------------------------- #


async def test_rejects_non_testnet() -> None:
    with pytest.raises(RuntimeError, match="testnet"):
        await repair("postgresql+asyncpg://x:x@localhost/x", "mainnet", apply=False)


async def test_abort_rolls_back_on_error(db_url: str, clean: None) -> None:
    """A corrupt row missing a required dependency aborts --apply (rollback, nothing written)."""
    await seed(db_url)
    # Delete CASE 3's take_profit trigger order so its fee_events.order_id cannot resolve.
    c3 = _case("CASE 3")
    engine = create_async_engine(db_url)
    try:
        async with async_sessionmaker(
            expire_on_commit=False, bind=engine, class_=AsyncSession
        )() as sess:
            pos3 = await sess.get(Position, c3.position_id)
            assert pos3 is not None
            tp = await sess.scalar(
                select(Order).where(
                    Order.decision_action_id == pos3.opening_action_id,
                    Order.order_kind == "take_profit",
                )
            )
            assert tp is not None
            await sess.delete(tp)
            await sess.commit()
    finally:
        await engine.dispose()

    with pytest.raises(RepairAbort):
        await repair(db_url, "testnet", apply=True)

    # NOTHING written: CASE 1 correction was not applied (still model_close).
    p1 = await _fetch_pos(db_url, _case("CASE 1").position_id)
    assert p1 is not None and p1.close_reason == "model_close"
