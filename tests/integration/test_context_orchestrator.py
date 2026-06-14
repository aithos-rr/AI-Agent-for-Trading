"""Integration tests for ContextOrchestrator (PRD §7.1, M3-T09)."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.context.builder import ContextBuilder
from aiat.db.repositories.context_build import ContextBuildRepository
from aiat.domain.exceptions import ContextBuildError
from aiat.domain.schemas import (
    ContextBundle,
    NewsItem,
    OnChainSnapshot,
    SentimentSnapshot,
    TechnicalIndicators,
)
from aiat.orchestration.context_orchestrator import ContextOrchestrator

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _make_tech(symbol: str) -> TechnicalIndicators:
    return TechnicalIndicators(
        symbol=symbol,  # type: ignore[arg-type]
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


def _make_sentiment() -> SentimentSnapshot:
    return SentimentSnapshot(
        fear_greed_index=60,
        fear_greed_label="greed",
        fetched_at="2026-01-01T00:00:00+00:00",
    )


def _make_news() -> list[NewsItem]:
    return [
        NewsItem(
            title="BTC surges",
            summary="Bitcoin reaches a new high.",
            source="CoinDesk",
            published_at="2026-01-01T00:00:00+00:00",
        )
    ]


def _make_onchain() -> list[OnChainSnapshot]:
    return [
        OnChainSnapshot(
            symbol=sym,  # type: ignore[arg-type]
            funding_rate_8h=Decimal("0.0001"),
            open_interest_usd=Decimal("500000000"),
            premium=Decimal("-0.0002"),
            liquidations_24h_usd=Decimal("10000"),
        )
        for sym in ("BTC", "ETH", "SOL")
    ]


def _mock_collector(return_value: Any) -> Any:
    mock = AsyncMock()
    mock.collect = AsyncMock(return_value=return_value)
    return mock


def _mock_collector_raises(exc: Exception) -> Any:
    mock = AsyncMock()
    mock.collect = AsyncMock(side_effect=exc)
    return mock


def _mock_collector_sleep(delay: float) -> Any:
    """Collector that sleeps for `delay` seconds before returning."""

    async def _slow_collect() -> TechnicalIndicators:
        await asyncio.sleep(delay)
        return _make_tech("BTC")

    mock = AsyncMock()
    mock.collect = _slow_collect
    return mock


def _make_success_builder() -> ContextBuilder:
    return ContextBuilder(
        technical_btc=_mock_collector(_make_tech("BTC")),
        technical_eth=_mock_collector(_make_tech("ETH")),
        technical_sol=_mock_collector(_make_tech("SOL")),
        sentiment=_mock_collector(_make_sentiment()),
        news=_mock_collector(_make_news()),
        onchain=_mock_collector(_make_onchain()),
    )


def _make_failing_builder() -> ContextBuilder:
    """Builder whose first collector raises ContextBuildError (via CollectorSourceError)."""
    from aiat.context.collectors.base import CollectorSourceError

    return ContextBuilder(
        technical_btc=_mock_collector_raises(CollectorSourceError("BTC down")),
        technical_eth=_mock_collector(_make_tech("ETH")),
        technical_sol=_mock_collector(_make_tech("SOL")),
        sentiment=_mock_collector(_make_sentiment()),
        news=_mock_collector(_make_news()),
        onchain=_mock_collector(_make_onchain()),
    )


def _make_slow_builder(delay: float) -> ContextBuilder:
    """Builder whose BTC collector sleeps for `delay` seconds."""
    return ContextBuilder(
        technical_btc=_mock_collector_sleep(delay),
        technical_eth=_mock_collector(_make_tech("ETH")),
        technical_sol=_mock_collector(_make_tech("SOL")),
        sentiment=_mock_collector(_make_sentiment()),
        news=_mock_collector(_make_news()),
        onchain=_mock_collector(_make_onchain()),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def orch_session_factory(db_url: str) -> Any:
    """Function-scoped async_sessionmaker for orchestrator tests.

    The orchestrator creates its own sessions and commits; function scope ensures
    each test gets a fresh engine while sharing the session-scoped DB schema.
    """
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


# ---------------------------------------------------------------------------
# Tests — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_returns_context_bundle(orch_session_factory: Any) -> None:
    exp_id = str(uuid.uuid4())
    tick_id = "2026-06-14T14:00:00"
    tick_at = "2026-06-14T14:00:00+00:00"

    orchestrator = ContextOrchestrator(_make_success_builder(), orch_session_factory)
    bundle = await orchestrator.build_tick_context(tick_id, tick_at, exp_id)

    assert isinstance(bundle, ContextBundle)
    assert bundle.tick_id == tick_id
    assert bundle.tick_at == tick_at


@pytest.mark.asyncio
async def test_success_persists_context_snapshot(orch_session_factory: Any) -> None:
    exp_id = str(uuid.uuid4())
    tick_id = "2026-06-14T14:15:00"
    tick_at = "2026-06-14T14:15:00+00:00"

    orchestrator = ContextOrchestrator(_make_success_builder(), orch_session_factory)
    await orchestrator.build_tick_context(tick_id, tick_at, exp_id)

    async with orch_session_factory() as session:
        repo = ContextBuildRepository(session)
        snapshot = await repo.get_snapshot_for_tick(exp_id, tick_id)

    assert snapshot is not None
    assert snapshot.tick_id == tick_id
    assert snapshot.experiment_id == uuid.UUID(exp_id)
    assert len(snapshot.context_hash) == 64  # SHA-256 hex


@pytest.mark.asyncio
async def test_success_build_run_has_success_status(orch_session_factory: Any) -> None:
    from aiat.db.models.context_build_run import ContextBuildRun

    exp_id = str(uuid.uuid4())
    tick_id = "2026-06-14T14:30:00"
    tick_at = "2026-06-14T14:30:00+00:00"

    orchestrator = ContextOrchestrator(_make_success_builder(), orch_session_factory)
    await orchestrator.build_tick_context(tick_id, tick_at, exp_id)

    async with orch_session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(ContextBuildRun).where(
                ContextBuildRun.experiment_id == uuid.UUID(exp_id),
                ContextBuildRun.tick_id == tick_id,
            )
        )
        build_run = result.scalar_one_or_none()

    assert build_run is not None
    assert build_run.status == "success"
    assert build_run.completed_at is not None
    assert build_run.context_snapshot_id is not None


# ---------------------------------------------------------------------------
# Tests — collector failure (partial source failure → failed build run)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collector_failure_raises_context_build_error(
    orch_session_factory: Any,
) -> None:
    exp_id = str(uuid.uuid4())
    tick_id = "2026-06-14T15:00:00"
    tick_at = "2026-06-14T15:00:00+00:00"

    orchestrator = ContextOrchestrator(_make_failing_builder(), orch_session_factory)

    with pytest.raises(ContextBuildError):
        await orchestrator.build_tick_context(tick_id, tick_at, exp_id)


@pytest.mark.asyncio
async def test_collector_failure_persists_failed_build_run(
    orch_session_factory: Any,
) -> None:
    from aiat.db.models.context_build_run import ContextBuildRun

    exp_id = str(uuid.uuid4())
    tick_id = "2026-06-14T15:15:00"
    tick_at = "2026-06-14T15:15:00+00:00"

    orchestrator = ContextOrchestrator(_make_failing_builder(), orch_session_factory)

    with pytest.raises(ContextBuildError):
        await orchestrator.build_tick_context(tick_id, tick_at, exp_id)

    async with orch_session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(ContextBuildRun).where(
                ContextBuildRun.experiment_id == uuid.UUID(exp_id),
                ContextBuildRun.tick_id == tick_id,
            )
        )
        build_run = result.scalar_one_or_none()

    assert build_run is not None
    assert build_run.status == "failed"
    assert build_run.context_snapshot_id is None


@pytest.mark.asyncio
async def test_collector_failure_no_snapshot_created(orch_session_factory: Any) -> None:
    exp_id = str(uuid.uuid4())
    tick_id = "2026-06-14T15:30:00"
    tick_at = "2026-06-14T15:30:00+00:00"

    orchestrator = ContextOrchestrator(_make_failing_builder(), orch_session_factory)

    with pytest.raises(ContextBuildError):
        await orchestrator.build_tick_context(tick_id, tick_at, exp_id)

    async with orch_session_factory() as session:
        repo = ContextBuildRepository(session)
        snapshot = await repo.get_snapshot_for_tick(exp_id, tick_id)

    assert snapshot is None


# ---------------------------------------------------------------------------
# Tests — timeout (hard deadline exceeded → timeout build run)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_raises_context_build_error(orch_session_factory: Any) -> None:
    exp_id = str(uuid.uuid4())
    tick_id = "2026-06-14T16:00:00"
    tick_at = "2026-06-14T16:00:00+00:00"

    # Builder sleeps 10s but hard timeout is 0.05s
    orchestrator = ContextOrchestrator(
        _make_slow_builder(delay=10.0),
        orch_session_factory,
        hard_timeout_seconds=0.05,
    )

    with pytest.raises(ContextBuildError, match="timed out"):
        await orchestrator.build_tick_context(tick_id, tick_at, exp_id)


@pytest.mark.asyncio
async def test_timeout_persists_timeout_build_run(orch_session_factory: Any) -> None:
    from aiat.db.models.context_build_run import ContextBuildRun

    exp_id = str(uuid.uuid4())
    tick_id = "2026-06-14T16:15:00"
    tick_at = "2026-06-14T16:15:00+00:00"

    orchestrator = ContextOrchestrator(
        _make_slow_builder(delay=10.0),
        orch_session_factory,
        hard_timeout_seconds=0.05,
    )

    with pytest.raises(ContextBuildError):
        await orchestrator.build_tick_context(tick_id, tick_at, exp_id)

    async with orch_session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(ContextBuildRun).where(
                ContextBuildRun.experiment_id == uuid.UUID(exp_id),
                ContextBuildRun.tick_id == tick_id,
            )
        )
        build_run = result.scalar_one_or_none()

    assert build_run is not None
    assert build_run.status == "timeout"
    assert build_run.context_snapshot_id is None
    assert build_run.failure_stage == "builder"
