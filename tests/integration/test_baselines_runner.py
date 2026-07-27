"""Integration tests for baseline equity computation (ADR-0036): live step + backfill.

Seeds an experiment + 3 baseline_configs + a few context_snapshots into ephemeral Postgres,
then exercises: dry-run writes nothing; backfill writes the 3 baselines per tick with the
hand-verifiable buy&hold/cash values; re-run is idempotent (no duplicates); catch-up fills only
the missing ticks while carrying state across runs; and the live per-tick step is idempotent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.baselines.runner import BaselineRunner
from aiat.db.models.baseline_equity_snapshot import BaselineEquitySnapshot
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.experiment import Experiment
from aiat.db.repositories.baselines import BaselineRepository
from aiat.domain.schemas import (
    ContextBundle,
    OnChainSnapshot,
    SentimentSnapshot,
    TechnicalIndicators,
)
from scripts.compute_baselines import backfill

_EXP = uuid.UUID("55555555-5555-5555-5555-555555555555")
_T0 = datetime(2026, 7, 1, 0, 0, 0, tzinfo=UTC)

# per-symbol tick spec: (price, ema20, ema50, funding_rate_8h)
Spec = tuple[str, str, str, str]


def _tech(symbol: str, price: str, ema20: str, ema50: str) -> TechnicalIndicators:
    return TechnicalIndicators(
        symbol=symbol,  # type: ignore[arg-type]
        price_usd=Decimal(price),
        rsi_14=Decimal("50"),
        macd_signal_diff=Decimal("0"),
        ema_20=Decimal(ema20),
        ema_50=Decimal(ema50),
        bollinger_upper=Decimal("1"),
        bollinger_lower=Decimal("1"),
        atr_14=Decimal("1"),
        volume_24h_usd=Decimal("1"),
    )


def _onchain(symbol: str, funding: str) -> OnChainSnapshot:
    return OnChainSnapshot(
        symbol=symbol,  # type: ignore[arg-type]
        funding_rate_8h=Decimal(funding),
        open_interest_usd=Decimal("1"),
        premium=Decimal("0"),
        liquidations_24h_usd=Decimal("0"),
    )


def _bundle(tick_at: datetime, btc: Spec, eth: Spec, sol: Spec) -> ContextBundle:
    tick_id = tick_at.isoformat()
    specs = {"BTC": btc, "ETH": eth, "SOL": sol}
    return ContextBundle(
        tick_id=tick_id,
        tick_at=tick_id,
        technical=[_tech(s, v[0], v[1], v[2]) for s, v in specs.items()],
        sentiment=SentimentSnapshot(
            fear_greed_index=50, fear_greed_label="neutral", fetched_at=tick_id
        ),
        news=[],
        onchain=[_onchain(s, v[3]) for s, v in specs.items()],
        source_timestamps={},
    )


# A 4-tick scenario. BTC: up-cross at t1 (opens momentum LONG @100, tp=106); +6% at t2 (TP);
# ETH/SOL flat (ema20==ema50, never cross). buy&hold: units fixed at t0, marked-to-market.
_FLAT = ("100", "100", "100", "0")
_TICKS: list[tuple[datetime, Spec, Spec, Spec]] = [
    (_T0, ("100", "99", "100", "0"), _FLAT, _FLAT),  # t0: below, flat momentum
    (_T0 + timedelta(minutes=15), ("100", "101", "100", "0"), _FLAT, _FLAT),  # t1: up-cross -> LONG
    (_T0 + timedelta(minutes=30), ("106", "102", "100", "0"), _FLAT, _FLAT),  # t2: +6% -> TP
    (_T0 + timedelta(minutes=45), ("106", "103", "100", "0"), _FLAT, _FLAT),  # t3: flat
]


async def _seed(db_url: str, n_ticks: int) -> None:
    """Seed the experiment, 3 baseline_configs, and the first n_ticks context snapshots."""
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            if await s.get(Experiment, _EXP) is None:
                s.add(
                    Experiment(
                        id=_EXP,
                        name="baseline-it",
                        started_at=_T0,
                        git_commit_sha="abc",
                        config_snapshot={},
                    )
                )
                await s.flush()
                repo = BaselineRepository(s)
                for name in ("buy_and_hold", "cash", "naive_momentum_ema_20_50"):
                    await repo.register_baseline_config(str(_EXP), name, {"strategy": name})
            existing = set(
                (
                    await s.scalars(
                        select(ContextSnapshot.tick_id).where(ContextSnapshot.experiment_id == _EXP)
                    )
                ).all()
            )
            for tick_at, btc, eth, sol in _TICKS[:n_ticks]:
                bundle = _bundle(tick_at, btc, eth, sol)
                if bundle.tick_id in existing:  # idempotent seed (catch-up test re-seeds)
                    continue
                s.add(
                    ContextSnapshot(
                        id=uuid.uuid4(),
                        experiment_id=_EXP,
                        tick_id=bundle.tick_id,
                        tick_at=tick_at,
                        context_hash=f"h{tick_at.isoformat()}",
                        context_json=bundle.model_dump(mode="json"),
                        source_timestamps={},
                        build_duration_ms=1,
                    )
                )
            await s.commit()
    finally:
        await engine.dispose()


async def _seed_indices(db_url: str, indices: list[int]) -> None:
    """Seed experiment + configs (idempotent) + only the given tick indices (for gap tests)."""
    await _seed(db_url, 0)  # experiment + 3 baseline_configs, no context ticks
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            for i in indices:
                tick_at, btc, eth, sol = _TICKS[i]
                bundle = _bundle(tick_at, btc, eth, sol)
                s.add(
                    ContextSnapshot(
                        id=uuid.uuid4(),
                        experiment_id=_EXP,
                        tick_id=bundle.tick_id,
                        tick_at=tick_at,
                        context_hash=f"h{tick_at.isoformat()}",
                        context_json=bundle.model_dump(mode="json"),
                        source_timestamps={},
                        build_duration_ms=1,
                    )
                )
            await s.commit()
    finally:
        await engine.dispose()


async def _insert_malformed(db_url: str, tick_at: datetime) -> None:
    """Insert a structurally-invalid context_snapshot (empty context_json) — a 'gap'."""
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            s.add(
                ContextSnapshot(
                    id=uuid.uuid4(),
                    experiment_id=_EXP,
                    tick_id=tick_at.isoformat(),
                    tick_at=tick_at,
                    context_hash="bad",
                    context_json={},  # not a valid ContextBundle
                    source_timestamps={},
                    build_duration_ms=1,
                )
            )
            await s.commit()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def clean(db_url: str):  # type: ignore[no-untyped-def]
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


async def _count_snapshots(db_url: str) -> int:
    engine = create_async_engine(db_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as s:
            return await s.scalar(select(func.count()).select_from(BaselineEquitySnapshot)) or 0
    finally:
        await engine.dispose()


async def _equity(db_url: str, name: str, tick_at: datetime) -> Decimal | None:
    engine = create_async_engine(db_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as s:
            return await s.scalar(
                select(BaselineEquitySnapshot.equity_usd).where(
                    BaselineEquitySnapshot.baseline_name == name,
                    BaselineEquitySnapshot.tick_at == tick_at,
                )
            )
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #


async def test_dry_run_writes_nothing(db_url: str, clean: None) -> None:
    await _seed(db_url, 4)
    summary = await backfill(db_url, str(_EXP), apply=False)
    assert summary.context_ticks == 4
    assert await _count_snapshots(db_url) == 0
    # dry-run still reports what it WOULD write
    assert summary.per_baseline["cash"].write == 4


async def test_backfill_writes_all_baselines_with_correct_values(db_url: str, clean: None) -> None:
    await _seed(db_url, 4)
    await backfill(db_url, str(_EXP), apply=True)

    assert await _count_snapshots(db_url) == 12  # 3 baselines × 4 ticks

    # cash: constant $1000 every tick
    for tick_at, *_ in _TICKS:
        assert await _equity(db_url, "cash", tick_at) == Decimal("1000.00000000")

    # buy&hold: t0 = 1000; t2 (BTC +6%, rest flat) = (1000/3)*3.06 = 1020
    assert await _equity(db_url, "buy_and_hold", _T0) == Decimal("1000.00000000")
    assert await _equity(db_url, "buy_and_hold", _TICKS[2][0]) == Decimal("1020.00000000")

    # momentum: LONG opened at t1, closed by TP at t2 (position cleared)
    engine = create_async_engine(db_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as s:
            snap_t1 = await s.scalar(
                select(BaselineEquitySnapshot).where(
                    BaselineEquitySnapshot.baseline_name == "naive_momentum_ema_20_50",
                    BaselineEquitySnapshot.tick_at == _TICKS[1][0],
                )
            )
            snap_t2 = await s.scalar(
                select(BaselineEquitySnapshot).where(
                    BaselineEquitySnapshot.baseline_name == "naive_momentum_ema_20_50",
                    BaselineEquitySnapshot.tick_at == _TICKS[2][0],
                )
            )
            assert snap_t1 is not None and snap_t1.raw_state["books"]["BTC"]["position"] is not None
            assert snap_t1.raw_state["books"]["BTC"]["position"]["direction"] == "LONG"
            assert snap_t2 is not None and snap_t2.raw_state["books"]["BTC"]["position"] is None
    finally:
        await engine.dispose()


async def test_backfill_is_idempotent(db_url: str, clean: None) -> None:
    await _seed(db_url, 4)
    await backfill(db_url, str(_EXP), apply=True)
    first = await _count_snapshots(db_url)
    bh_t2 = await _equity(db_url, "buy_and_hold", _TICKS[2][0])

    summary = await backfill(db_url, str(_EXP), apply=True)  # re-run
    assert await _count_snapshots(db_url) == first == 12
    assert summary.per_baseline["cash"].write == 0  # nothing new written
    assert summary.per_baseline["cash"].skip_exists == 4
    assert await _equity(db_url, "buy_and_hold", _TICKS[2][0]) == bh_t2  # unchanged


async def test_catch_up_fills_only_missing_and_carries_state(db_url: str, clean: None) -> None:
    # First: only the first 2 ticks exist.
    await _seed(db_url, 2)
    await backfill(db_url, str(_EXP), apply=True)
    assert await _count_snapshots(db_url) == 6  # 3 × 2

    # Now the last 2 ticks arrive; re-run catches up (skips 2, writes 2).
    await _seed(db_url, 4)  # adds t2, t3 context snapshots
    summary = await backfill(db_url, str(_EXP), apply=True)
    assert await _count_snapshots(db_url) == 12
    assert summary.per_baseline["buy_and_hold"].skip_exists == 2
    assert summary.per_baseline["buy_and_hold"].write == 2
    # state carried across the two runs: buy&hold units fixed at t0 → t2 still marks to 1020
    assert await _equity(db_url, "buy_and_hold", _TICKS[2][0]) == Decimal("1020.00000000")


async def test_gap_no_snapshot_invented_curve_resumes(db_url: str, clean: None) -> None:
    # Seed ticks 0, 1, 3 (tick 2 is a MissedTick gap). No snapshot is invented for t2; buy&hold
    # units set at t0 persist across the gap, so t3 still marks to the t3 price (BTC 106 -> 1020).
    await _seed_indices(db_url, [0, 1, 3])
    summary = await backfill(db_url, str(_EXP), apply=True)
    assert summary.context_ticks == 3
    assert await _count_snapshots(db_url) == 9  # 3 baselines × 3 present ticks (no t2)
    assert await _equity(db_url, "buy_and_hold", _TICKS[3][0]) == Decimal("1020.00000000")
    assert await _equity(db_url, "buy_and_hold", _TICKS[2][0]) is None  # gap: not invented
    assert await _equity(db_url, "cash", _TICKS[3][0]) == Decimal("1000.00000000")


async def test_malformed_snapshot_is_skipped_as_gap(db_url: str, clean: None) -> None:
    await _seed(db_url, 4)
    await _insert_malformed(db_url, _T0 + timedelta(minutes=60))  # a 5th, invalid snapshot
    summary = await backfill(db_url, str(_EXP), apply=True)
    assert summary.malformed_snapshots == 1
    assert summary.context_ticks == 4  # the 4 valid ticks still processed
    assert await _count_snapshots(db_url) == 12  # nothing written for the malformed tick


async def test_live_step_is_idempotent_and_carries_state(db_url: str, clean: None) -> None:
    await _seed(db_url, 2)
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        runner = BaselineRunner(str(_EXP))
        b0 = _bundle(*_TICKS[0])
        b1 = _bundle(*_TICKS[1])
        await runner.run_live_tick(factory, b0)
        await runner.run_live_tick(factory, b0)  # re-fire same tick → idempotent
        await runner.run_live_tick(factory, b1)
    finally:
        await engine.dispose()

    assert await _count_snapshots(db_url) == 6  # 3 baselines × 2 ticks (no dup for t0)
    # live carried state from DB: momentum opened LONG at t1
    engine2 = create_async_engine(db_url)
    try:
        async with async_sessionmaker(engine2, class_=AsyncSession)() as s:
            snap = await s.scalar(
                select(BaselineEquitySnapshot).where(
                    BaselineEquitySnapshot.baseline_name == "naive_momentum_ema_20_50",
                    BaselineEquitySnapshot.tick_at == _TICKS[1][0],
                )
            )
            assert snap is not None
            assert snap.raw_state["books"]["BTC"]["position"]["direction"] == "LONG"
    finally:
        await engine2.dispose()
