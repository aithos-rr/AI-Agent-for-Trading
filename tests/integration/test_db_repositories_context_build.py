"""Integration tests for ContextBuildRepository (§7.6 fix B.5, M3-T08)."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.context_build_run import ContextBuildRun
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.repositories.context_build import ContextBuildRepository
from aiat.domain.schemas import (
    ContextBundle,
    NewsItem,
    OnChainSnapshot,
    SentimentSnapshot,
    TechnicalIndicators,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_EXP_ID = str(uuid.uuid4())
_TICK_ID = "2026-01-01T00:00:00"
_TICK_AT = "2026-01-01T00:00:00+00:00"


def _make_bundle(tick_id: str = _TICK_ID, tick_at: str = _TICK_AT) -> ContextBundle:
    return ContextBundle(
        tick_id=tick_id,
        tick_at=tick_at,
        technical=[
            TechnicalIndicators(
                symbol="BTC",
                price_usd=Decimal("60000"),
                rsi_14=Decimal("55"),
                macd_signal_diff=Decimal("0.5"),
                ema_20=Decimal("59500"),
                ema_50=Decimal("58000"),
                bollinger_upper=Decimal("62000"),
                bollinger_lower=Decimal("57000"),
                atr_14=Decimal("1200"),
                volume_24h_usd=Decimal("20000000"),
            )
        ],
        sentiment=SentimentSnapshot(
            fear_greed_index=55,
            fear_greed_label="greed",
            fetched_at="2026-01-01T00:00:00+00:00",
        ),
        news=[
            NewsItem(
                title="BTC hits 60k",
                summary="Bitcoin reaches 60k for the first time in 2026.",
                source="CoinDesk",
                published_at="2026-01-01T00:00:00+00:00",
            )
        ],
        onchain=[
            OnChainSnapshot(
                symbol="BTC",
                funding_rate_8h=Decimal("0.0001"),
                open_interest_usd=Decimal("500000000"),
                premium=Decimal("-0.0002"),
                liquidations_24h_usd=Decimal("10000"),
            )
        ],
        source_timestamps={
            "technical_btc": "2026-01-01T00:00:00+00:00",
            "sentiment": "2026-01-01T00:00:00+00:00",
            "news": "2026-01-01T00:00:00+00:00",
            "onchain": "2026-01-01T00:00:00+00:00",
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_build_creates_running_row(db_session: AsyncSession) -> None:
    repo = ContextBuildRepository(db_session)

    build_run_id = await repo.start_build(
        experiment_id=_EXP_ID,
        tick_id=_TICK_ID,
        tick_at=_TICK_AT,
    )

    build_run = await db_session.get(ContextBuildRun, uuid.UUID(build_run_id))
    assert build_run is not None
    assert build_run.status == "running"
    assert build_run.tick_id == _TICK_ID
    assert build_run.experiment_id == uuid.UUID(_EXP_ID)
    assert build_run.context_snapshot_id is None
    assert build_run.completed_at is None


@pytest.mark.asyncio
async def test_complete_build_success(db_session: AsyncSession) -> None:
    repo = ContextBuildRepository(db_session)
    bundle = _make_bundle()

    build_run_id = await repo.start_build(
        experiment_id=_EXP_ID,
        tick_id=_TICK_ID,
        tick_at=_TICK_AT,
    )
    snapshot_id = await repo.complete_build(
        build_run_id=build_run_id,
        status="success",
        context_bundle=bundle,
        build_duration_ms=1234,
    )

    # Snapshot row created
    snapshot = await db_session.get(ContextSnapshot, uuid.UUID(snapshot_id))
    assert snapshot is not None
    assert snapshot.tick_id == _TICK_ID
    assert snapshot.experiment_id == uuid.UUID(_EXP_ID)
    assert snapshot.build_duration_ms == 1234
    assert len(snapshot.context_hash) == 64  # SHA-256 hex

    # Build run updated
    build_run = await db_session.get(ContextBuildRun, uuid.UUID(build_run_id))
    assert build_run is not None
    assert build_run.status == "success"
    assert build_run.context_snapshot_id == uuid.UUID(snapshot_id)
    assert build_run.completed_at is not None


@pytest.mark.asyncio
async def test_complete_build_partial(db_session: AsyncSession) -> None:
    repo = ContextBuildRepository(db_session)
    exp_id = str(uuid.uuid4())
    tick_id = "2026-01-02T00:00:00"
    bundle = _make_bundle(tick_id=tick_id)

    build_run_id = await repo.start_build(
        experiment_id=exp_id,
        tick_id=tick_id,
        tick_at="2026-01-02T00:00:00+00:00",
    )
    snapshot_id = await repo.complete_build(
        build_run_id=build_run_id,
        status="partial",
        context_bundle=bundle,
        build_duration_ms=500,
    )

    build_run = await db_session.get(ContextBuildRun, uuid.UUID(build_run_id))
    assert build_run is not None
    assert build_run.status == "partial"
    assert build_run.context_snapshot_id == uuid.UUID(snapshot_id)


@pytest.mark.asyncio
async def test_fail_build_failed(db_session: AsyncSession) -> None:
    repo = ContextBuildRepository(db_session)

    build_run_id = await repo.start_build(
        experiment_id=_EXP_ID,
        tick_id="2026-01-03T00:00:00",
        tick_at="2026-01-03T00:00:00+00:00",
    )
    await repo.fail_build(
        build_run_id=build_run_id,
        failure_stage="news",
        error_context={"error": "timeout", "source": "CoinDesk"},
        status="failed",
    )

    build_run = await db_session.get(ContextBuildRun, uuid.UUID(build_run_id))
    assert build_run is not None
    assert build_run.status == "failed"
    assert build_run.failure_stage == "news"
    assert build_run.error_context == {"error": "timeout", "source": "CoinDesk"}
    assert build_run.completed_at is not None
    assert build_run.context_snapshot_id is None  # no snapshot created


@pytest.mark.asyncio
async def test_fail_build_timeout(db_session: AsyncSession) -> None:
    repo = ContextBuildRepository(db_session)

    build_run_id = await repo.start_build(
        experiment_id=_EXP_ID,
        tick_id="2026-01-04T00:00:00",
        tick_at="2026-01-04T00:00:00+00:00",
    )
    await repo.fail_build(
        build_run_id=build_run_id,
        failure_stage="technical",
        error_context={"error": "deadline_exceeded"},
        status="timeout",
    )

    build_run = await db_session.get(ContextBuildRun, uuid.UUID(build_run_id))
    assert build_run is not None
    assert build_run.status == "timeout"
    assert build_run.context_snapshot_id is None


@pytest.mark.asyncio
async def test_get_snapshot_for_tick_returns_snapshot(db_session: AsyncSession) -> None:
    repo = ContextBuildRepository(db_session)
    exp_id = str(uuid.uuid4())
    tick_id = "2026-01-05T00:00:00"
    bundle = _make_bundle(tick_id=tick_id, tick_at="2026-01-05T00:00:00+00:00")

    build_run_id = await repo.start_build(
        experiment_id=exp_id,
        tick_id=tick_id,
        tick_at="2026-01-05T00:00:00+00:00",
    )
    snapshot_id = await repo.complete_build(
        build_run_id=build_run_id,
        status="success",
        context_bundle=bundle,
        build_duration_ms=800,
    )

    found = await repo.get_snapshot_for_tick(experiment_id=exp_id, tick_id=tick_id)
    assert found is not None
    assert str(found.id) == snapshot_id
    assert found.tick_id == tick_id


@pytest.mark.asyncio
async def test_get_snapshot_for_tick_returns_none_when_missing(db_session: AsyncSession) -> None:
    repo = ContextBuildRepository(db_session)

    found = await repo.get_snapshot_for_tick(
        experiment_id=str(uuid.uuid4()),
        tick_id="nonexistent-tick",
    )
    assert found is None
