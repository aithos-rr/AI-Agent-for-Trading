"""Integration tests for ClosureReconciler — T4b root-cause fix (ADR-0038).

The orchestrator-level closure pass replaces the old in-run
``decision_loop._check_pending_closures`` that left zombie positions (open in DB, closed on-chain
— 5 occurrences in 20 days). Each test seeds real positions + trigger orders into an ephemeral
Postgres, feeds canned ``user_fills`` (NO HL calls), runs ``reconcile`` and asserts the DB outcome.
The two headline scenarios are mutation-proof against the two failure mechanisms the fix eliminates:

  * (a) M1 — an SL fires between ticks AND the same symbol is reopened the same tick: detection is
    PER POSITION (by the position's own trigger oid), so the old position closes while the fresh
    same-symbol position stays open. A per-symbol / ``szi != 0`` short-circuit would get this wrong.
  * (b) M2 — the agent is dead (its only run FAILED, no run reached the old step 9): the pass still
    books the closure, anchoring ``closing_run_id`` to the latest run of any status.

(c) no false positive (trigger not fired → still open) and (d) idempotence (a closed position is
never re-processed) round out the contract.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.db.models.action import DecisionAction
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.decision import Decision
from aiat.db.models.experiment import Experiment
from aiat.db.models.model import Model
from aiat.db.models.order import Order
from aiat.db.models.outcome import Outcome
from aiat.db.models.position import Position
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from aiat.orchestration.closure_reconciler import ClosureReconciler

EXPERIMENT_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
_PT_HASH = "a" * 64
_TICK = "2026-07-01T00:00:00"
_GIT = "abc1234"
_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
_NOW_MS = int(_NOW.timestamp() * 1000)


def _dt(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)  # type: ignore[arg-type]


def _fill(
    *,
    oid: str,
    coin: str = "BTC",
    fee: str = "0.5",
    closed_pnl: str = "0.0",
    px: str = "95.0",
    sz: str = "0.5",
    dir_: str = "Close Long",
    time_ms: int,
    liquidation: object | None = None,
) -> dict[str, object]:
    """A Hyperliquid ``user_fills`` record (the shape the venue returns)."""
    out: dict[str, object] = {
        "coin": coin,
        "oid": int(oid),
        "dir": dir_,
        "px": px,
        "sz": sz,
        "fee": fee,
        "closedPnl": closed_pnl,
        "time": time_ms,
    }
    if liquidation is not None:
        out["liquidation"] = liquidation
    return out


class _FakeFills:
    """Read-only fills source (satisfies the FillsSource Protocol). Records its calls so the
    per-model window can be asserted; NO network."""

    def __init__(
        self,
        by_wallet: dict[str, list[dict[str, object]]],
        raise_for: set[str] | None = None,
    ) -> None:
        self._by_wallet = by_wallet
        self._raise_for = raise_for or set()
        self.calls: list[tuple[str, int, int | None]] = []

    async def user_fills_by_time(
        self, user: str, start_time_ms: int, end_time_ms: int | None = None
    ) -> list[dict[str, object]]:
        self.calls.append((user, start_time_ms, end_time_ms))
        if user in self._raise_for:
            raise RuntimeError(f"HL fills fetch failed for {user}")
        return list(self._by_wallet.get(user, []))


async def _model(session: AsyncSession, model_id: str, wallet: str) -> None:
    session.add(
        Model(
            id=model_id,
            provider="openai",
            model_name_api="m",
            tier="premium",
            geography="USA",
            wallet_address=wallet,
            pricing_input_usd_per_1m=Decimal("1.0"),
            pricing_output_usd_per_1m=Decimal("1.0"),
        )
    )
    await session.flush()


async def _run(
    session: AsyncSession, model_id: str, snap_id: uuid.UUID, started: datetime, status: str
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
    session: AsyncSession, model_id: str, run_id: uuid.UUID, symbol: str, opened_at: datetime
) -> uuid.UUID:
    did = uuid.uuid4()
    session.add(
        Decision(
            id=did,
            run_id=run_id,
            experiment_id=EXPERIMENT_ID,
            model_id=model_id,
            decided_at=opened_at,
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
            confidence=Decimal("0.7000"),
            time_horizon_min=120,
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


async def _open_position(
    session: AsyncSession,
    snap_id: uuid.UUID,
    model_id: str,
    symbol: str,
    opened_at: datetime,
    sl_oid: str,
    tp_oid: str,
    *,
    entry_price: Decimal = Decimal("100"),
    size_units: Decimal = Decimal("0.5"),
    run_status: str = "success",
) -> uuid.UUID:
    """Seed one OPEN position + its opening run/decision/action + entry/SL/TP orders.

    ``sl_oid``/``tp_oid`` become the trigger orders' ``hl_order_id`` — the ClosureReconciler matches
    ``user_fills[*].oid`` against these. Returns the position id.
    """
    run_id = await _run(session, model_id, snap_id, opened_at, run_status)
    action_id = await _opening_action(session, model_id, run_id, symbol, opened_at)
    pos_id = uuid.uuid4()
    notional = size_units * entry_price
    session.add(
        Position(
            id=pos_id,
            experiment_id=EXPERIMENT_ID,
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
    session.add(
        Order(
            id=uuid.uuid4(),
            decision_action_id=action_id,
            experiment_id=EXPERIMENT_ID,
            model_id=model_id,
            run_id=run_id,
            symbol=symbol,
            order_kind="entry",
            hl_order_id=str(uuid.uuid4()),
            status="filled",
            requested_size_units=size_units,
            filled_size_units=size_units,
            filled_price=entry_price,
            raw_order_response={},
            submitted_at=opened_at,
            filled_at=opened_at,
        )
    )
    for kind, oid in (("stop_loss", sl_oid), ("take_profit", tp_oid)):
        session.add(
            Order(
                id=uuid.uuid4(),
                decision_action_id=action_id,
                experiment_id=EXPERIMENT_ID,
                model_id=model_id,
                run_id=run_id,
                symbol=symbol,
                order_kind=kind,
                hl_order_id=oid,
                status="triggered",
                requested_size_units=size_units,
                raw_order_response={},
                submitted_at=opened_at,
            )
        )
    await session.flush()
    return pos_id


async def _seed_scaffold(session: AsyncSession) -> uuid.UUID:
    """Experiment + prompt template + context snapshot. Returns the snapshot id."""
    session.add(
        Experiment(
            id=EXPERIMENT_ID,
            name="m6-smoke",
            started_at=_dt(2026, 7, 1),
            git_commit_sha=_GIT,
            config_snapshot={},
        )
    )
    session.add(
        PromptTemplate(
            sha256_hash=_PT_HASH,
            label="pt",
            template_text="You are a trading agent.",
            confidence_def="d",
            controlled_signals=[],
        )
    )
    await session.flush()
    snap_id = uuid.uuid4()
    session.add(
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
    await session.flush()
    return snap_id


def _factory(db_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(db_url)
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _fetch_pos(db_url: str, pos_id: uuid.UUID) -> Position | None:
    async with _factory(db_url)() as s:
        return await s.get(Position, pos_id)


async def _fetch_outcome(db_url: str, pos_id: uuid.UUID) -> Outcome | None:
    async with _factory(db_url)() as s:
        return await s.scalar(select(Outcome).where(Outcome.position_id == pos_id))


async def _count_outcomes(db_url: str, pos_id: uuid.UUID) -> int:
    async with _factory(db_url)() as s:
        rows = (await s.scalars(select(Outcome).where(Outcome.position_id == pos_id))).all()
        return len(rows)


@pytest_asyncio.fixture
async def clean(db_url: str):  # type: ignore[no-untyped-def]
    """Truncate every table after each test (the reconciler commits to the shared DB)."""
    yield
    engine = create_async_engine(db_url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename <> 'alembic_version'"
                )
            )
            tables = [r[0] for r in rows]
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# (a) M1: SL fires between ticks + same-symbol reopen same tick               #
#     -> only the OLD position closes; the fresh same-symbol one stays open   #
# --------------------------------------------------------------------------- #


async def test_sl_fires_with_same_symbol_reopen_closes_only_the_old_position(
    db_url: str, clean: None
) -> None:
    wallet = "0xwalletA"
    old_sl = "900001"
    factory = _factory(db_url)
    async with factory() as s:
        snap = await _seed_scaffold(s)
        await _model(s, "usa-premium", wallet)
        # OLD BTC position, opened at 11:30; its SL (oid 900001) fired at 11:45.
        old_pos = await _open_position(
            s, snap, "usa-premium", "BTC", _dt(2026, 7, 20, 11, 30), old_sl, "900002"
        )
        # NEW BTC position, reopened same symbol at 11:50 (same-symbol reopen). Its triggers
        # (910001/910002) have NOT fired.
        new_pos = await _open_position(
            s, snap, "usa-premium", "BTC", _dt(2026, 7, 20, 11, 50), "910001", "910002"
        )
        await s.commit()

    # The wallet's fills carry ONLY the old position's SL oid firing (exit 95 < entry 100 → SL).
    fills = _FakeFills(
        {
            wallet: [
                _fill(
                    oid=old_sl,
                    px="95.0",
                    closed_pnl="-2.5",
                    time_ms=int(_dt(2026, 7, 20, 11, 45).timestamp() * 1000),
                )
            ]
        }  # noqa: E501
    )
    result = await ClosureReconciler(factory, fills, str(EXPERIMENT_ID)).reconcile(_NOW_MS)

    assert result.closed == 1
    assert result.still_open == 1
    # OLD closed as stop_loss (per-side attribution: LONG exit<entry).
    old = await _fetch_pos(db_url, old_pos)
    assert old is not None and old.closed_at is not None
    assert old.close_reason == "stop_loss"
    assert old.exit_price == Decimal("95.0")
    old_out = await _fetch_outcome(db_url, old_pos)
    assert old_out is not None and old_out.realized_pnl_gross_usd == Decimal("-2.5")
    # NEW same-symbol position UNTOUCHED — a per-symbol/szi short-circuit would have missed this.
    new = await _fetch_pos(db_url, new_pos)
    assert new is not None and new.closed_at is None
    assert await _fetch_outcome(db_url, new_pos) is None
    # The fills window started before the OLDEST open position's opened_at (11:30) minus buffer.
    assert fills.calls and fills.calls[0][0] == wallet
    assert fills.calls[0][1] < int(_dt(2026, 7, 20, 11, 30).timestamp() * 1000)


# --------------------------------------------------------------------------- #
# (b) M2: dead agent — the model's only run is FAILED, no run reached step 9   #
#     -> the pass still books the closure, anchoring closing_run to that run   #
# --------------------------------------------------------------------------- #


async def test_dead_agent_closure_booked_during_blackout(db_url: str, clean: None) -> None:
    wallet = "0xwalletB"
    sl_oid = "800001"
    factory = _factory(db_url)
    async with factory() as s:
        snap = await _seed_scaffold(s)
        await _model(s, "cn-premium", wallet)
        # The position's opening run is a FAILED run (the agent never had a healthy tick after).
        pos_id = await _open_position(
            s,
            snap,
            "cn-premium",
            "BTC",
            _dt(2026, 7, 20, 11, 0),
            sl_oid,
            "800002",
            run_status="failed",
        )
        await s.commit()

    async with factory() as s:  # capture the (only, failed) run for the closing_run assertion
        failed_run = await s.scalar(select(Run.id).where(Run.model_id == "cn-premium"))

    fills = _FakeFills(
        {
            wallet: [
                _fill(
                    oid=sl_oid,
                    px="95.0",
                    closed_pnl="-2.5",
                    time_ms=int(_dt(2026, 7, 20, 11, 15).timestamp() * 1000),
                )
            ]
        }  # noqa: E501
    )
    result = await ClosureReconciler(factory, fills, str(EXPERIMENT_ID)).reconcile(_NOW_MS)

    assert result.closed == 1 and result.no_run == 0
    pos = await _fetch_pos(db_url, pos_id)
    assert pos is not None and pos.closed_at is not None and pos.close_reason == "stop_loss"
    out = await _fetch_outcome(db_url, pos_id)
    assert out is not None
    # closing_run anchors to the model's latest run of ANY status (the FAILED one) — a
    # success-only narrowing would leave the position a zombie (no_run) forever.
    assert out.closing_run_id == failed_run


# --------------------------------------------------------------------------- #
# (c) no false positive: a position whose trigger never fired stays open       #
# --------------------------------------------------------------------------- #


async def test_no_false_positive_when_trigger_did_not_fire(db_url: str, clean: None) -> None:
    wallet = "0xwalletC"
    factory = _factory(db_url)
    async with factory() as s:
        snap = await _seed_scaffold(s)
        await _model(s, "usa-premium", wallet)
        pos_id = await _open_position(
            s, snap, "usa-premium", "BTC", _dt(2026, 7, 20, 11, 0), "700001", "700002"
        )
        await s.commit()

    # The wallet traded an UNRELATED order (an open of another position) — no trigger oid present.
    fills = _FakeFills(
        {
            wallet: [
                _fill(
                    oid="123456",
                    dir_="Open Long",
                    closed_pnl="0.0",
                    time_ms=int(_dt(2026, 7, 20, 11, 30).timestamp() * 1000),
                )
            ]
        }  # noqa: E501
    )
    result = await ClosureReconciler(factory, fills, str(EXPERIMENT_ID)).reconcile(_NOW_MS)

    assert result.closed == 0 and result.still_open == 1
    pos = await _fetch_pos(db_url, pos_id)
    assert pos is not None and pos.closed_at is None
    assert await _fetch_outcome(db_url, pos_id) is None


# --------------------------------------------------------------------------- #
# (d) idempotence: a second pass over an already-closed position writes nothing #
# --------------------------------------------------------------------------- #


async def test_second_pass_is_idempotent(db_url: str, clean: None) -> None:
    wallet = "0xwalletD"
    sl_oid = "600001"
    factory = _factory(db_url)
    async with factory() as s:
        snap = await _seed_scaffold(s)
        await _model(s, "usa-premium", wallet)
        pos_id = await _open_position(
            s, snap, "usa-premium", "BTC", _dt(2026, 7, 20, 11, 0), sl_oid, "600002"
        )
        await s.commit()

    fills = _FakeFills(
        {
            wallet: [
                _fill(
                    oid=sl_oid,
                    px="95.0",
                    closed_pnl="-2.5",
                    time_ms=int(_dt(2026, 7, 20, 11, 15).timestamp() * 1000),
                )
            ]
        }  # noqa: E501
    )
    reconciler = ClosureReconciler(factory, fills, str(EXPERIMENT_ID))

    first = await reconciler.reconcile(_NOW_MS)
    assert first.closed == 1
    # The very same fills are still returned on the second pass (the on-chain record does not
    # disappear); idempotence must come from the position no longer being open, not from the fills.
    second = await reconciler.reconcile(_NOW_MS)
    assert second.closed == 0 and second.models == 0  # no open positions left → model not visited

    assert await _count_outcomes(db_url, pos_id) == 1


# --------------------------------------------------------------------------- #
# per-model isolation: one wallet's fills fetch fails, the other still closes   #
# --------------------------------------------------------------------------- #


async def test_one_failing_wallet_does_not_abort_the_batch(db_url: str, clean: None) -> None:
    wallet_ok = "0xwalletOK"
    wallet_bad = "0xwalletBAD"
    sl_oid = "500001"
    factory = _factory(db_url)
    async with factory() as s:
        snap = await _seed_scaffold(s)
        await _model(s, "usa-premium", wallet_ok)
        await _model(s, "cn-premium", wallet_bad)
        pos_ok = await _open_position(
            s, snap, "usa-premium", "BTC", _dt(2026, 7, 20, 11, 0), sl_oid, "500002"
        )
        pos_bad = await _open_position(
            s, snap, "cn-premium", "ETH", _dt(2026, 7, 20, 11, 0), "510001", "510002"
        )
        await s.commit()

    fills = _FakeFills(
        {
            wallet_ok: [
                _fill(
                    oid=sl_oid,
                    px="95.0",
                    closed_pnl="-2.5",
                    time_ms=int(_dt(2026, 7, 20, 11, 15).timestamp() * 1000),
                )
            ]
        },
        raise_for={wallet_bad},  # this wallet's fetch blows up
    )
    result = await ClosureReconciler(factory, fills, str(EXPERIMENT_ID)).reconcile(_NOW_MS)

    assert result.closed == 1 and result.model_errors == 1
    # The healthy model closed despite the other wallet failing.
    ok = await _fetch_pos(db_url, pos_ok)
    assert ok is not None and ok.closed_at is not None
    # The failing model's position is untouched (no partial write).
    bad = await _fetch_pos(db_url, pos_bad)
    assert bad is not None and bad.closed_at is None
    assert await _fetch_outcome(db_url, pos_bad) is None
