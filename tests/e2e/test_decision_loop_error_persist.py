"""E2E: failed/timed-out ticks persist a queryable ``errors`` row + per-class stage.

Regression guard for finding D (smoke M6): before this fix ``run_once`` marked
``runs.failure_stage='error'`` and logged the exception to structlog, but NEVER wrote
the message/stack to the ``errors`` table — 36 failed smoke runs left only 8 queryable
error rows (all ``MissedTick``) — and every non-timeout failure collapsed to the single
stage ``'error'`` (auth vs rate-limit vs parse indistinguishable in the DB).

These tests drive ``run_once`` against a REAL Postgres + the REAL ``RunsRepository`` —
the persist layer is deliberately NOT mocked, because a mock that diverged from the real
format is exactly what hid finding A for four days. Each test injects a specific
exception class into the LLM client and asserts on the rows that actually land in the DB.
Run against the pre-fix code, every case fails (no error row; wrong failure_stage).
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.config.settings import AgentSettings
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.error import Error
from aiat.db.models.experiment import Experiment
from aiat.db.models.model import Model
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from aiat.domain.enums import RunStatus
from aiat.domain.schemas import (
    ContextBundle,
    NewsItem,
    OnChainSnapshot,
    PortfolioState,
    SentimentSnapshot,
    TechnicalIndicators,
)
from aiat.llm.exceptions import LLMAuthError, LLMRateLimitError, LLMUnrecoverableError
from aiat.orchestration.decision_loop import DecisionLoop

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TICK_ID = "2026-06-14T14:30:00+00:00"
_TICK_AT = datetime(2026, 6, 14, 14, 30, tzinfo=UTC)
_GIT_SHA = "abc1234"
_SCHEMA_VERSION = "v1"
_PT_TEXT = "You are a trading agent. Make decisions based on the context provided below."
_PT_HASH = hashlib.sha256(_PT_TEXT.encode()).hexdigest()
_PT_CONFIDENCE_DEF = "Probability that the action yields positive net PnL within time_horizon_min."


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def session_factory(db_url: str) -> async_sessionmaker[AsyncSession]:
    """Session factory connected to the ephemeral test DB."""
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def seed_ids(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    """Seed experiment, model, prompt_template, and context_snapshot rows.

    Committed so the DecisionLoop (separate sessions/connections) can read them. Uses
    fresh UUIDs per test so parametrized cases don't collide on the runs UNIQUE key.
    """
    exp_id = uuid.uuid4()
    model_id = f"openai-gpt4o-err-{uuid.uuid4().hex[:6]}"
    snap_id = uuid.uuid4()

    async with session_factory() as session:
        session.add(
            Experiment(
                id=exp_id,
                name=f"err-test-{exp_id.hex[:8]}",
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
                wallet_address=f"0x{uuid.uuid4().hex[:40]}",
                pricing_input_usd_per_1m=Decimal("5.000000"),
                pricing_output_usd_per_1m=Decimal("15.000000"),
            )
        )
        await session.flush()
        # PromptTemplate PK is sha256_hash — shared across tests, insert-if-absent.
        await session.execute(
            pg_insert(PromptTemplate)
            .values(
                sha256_hash=_PT_HASH,
                label="err-pt-shared",
                template_text=_PT_TEXT,
                confidence_def=_PT_CONFIDENCE_DEF,
                controlled_signals=[],
            )
            .on_conflict_do_nothing(index_elements=["sha256_hash"])
        )
        await session.flush()
        session.add(
            ContextSnapshot(
                id=snap_id,
                experiment_id=exp_id,
                tick_id=_TICK_ID,
                tick_at=_TICK_AT,
                context_hash=hashlib.sha256(b"err-test").hexdigest(),
                context_json=_make_context_bundle().model_dump(mode="json"),
                source_timestamps={},
                build_duration_ms=50,
            )
        )
        await session.commit()

    return {
        "experiment_id": str(exp_id),
        "model_id": model_id,
        "snapshot_id": str(snap_id),
        "prompt_template_hash": _PT_HASH,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_settings(
    seed_ids: dict[str, str], *, hard_timeout_seconds: int = 180
) -> AgentSettings:
    return AgentSettings(  # type: ignore[call-arg]
        experiment_id=seed_ids["experiment_id"],
        git_commit_sha=_GIT_SHA,
        database_url="postgresql+asyncpg://x:x@localhost/x",  # overridden by session_factory
        network="testnet",
        service_role="agent",
        model_id=seed_ids["model_id"],
        prompt_template_hash=seed_ids["prompt_template_hash"],
        schema_version=_SCHEMA_VERSION,
        llm_provider="openai",
        model_name_api="gpt-4o",
        openai_api_key="sk-test",
        hl_wallet_private_key="0x" + "0" * 64,
        hl_wallet_address="0x" + "0" * 40,
        llm_gateway="direct",
        max_size_pct=Decimal("0.20"),
        hard_max_leverage=Decimal("10"),
        min_open_confidence=Decimal("0.4"),
        hard_timeout_seconds=hard_timeout_seconds,
    )


def _make_context_bundle() -> ContextBundle:
    tech = TechnicalIndicators(
        symbol="BTC",
        price_usd=Decimal("65000"),
        rsi_14=Decimal("55"),
        macd_signal_diff=Decimal("100"),
        ema_20=Decimal("64000"),
        ema_50=Decimal("63000"),
        bollinger_upper=Decimal("68000"),
        bollinger_lower=Decimal("62000"),
        atr_14=Decimal("500"),
        volume_24h_usd=Decimal("1000000000"),
    )
    return ContextBundle(
        tick_id=_TICK_ID,
        tick_at=_TICK_ID,
        technical=[
            tech,
            tech.model_copy(update={"symbol": "ETH", "price_usd": Decimal("3500")}),
            tech.model_copy(update={"symbol": "SOL", "price_usd": Decimal("180")}),
        ],
        sentiment=SentimentSnapshot(
            fear_greed_index=55,
            fear_greed_label="greed",
            fetched_at=_TICK_ID,
        ),
        news=[
            NewsItem(
                title="BTC consolidates near ATH",
                summary="Bitcoin price holds steady near all-time high levels",
                source="coindesk",
                published_at=_TICK_ID,
            )
        ],
        onchain=[
            OnChainSnapshot(
                symbol="BTC",
                funding_rate_8h=Decimal("0.0001"),
                open_interest_usd=Decimal("10000000"),
                premium=Decimal("-0.0002"),
                liquidations_24h_usd=Decimal("1000000"),
            )
        ],
        source_timestamps={"technical_btc": _TICK_ID},
    )


def _stub_hl_client() -> AsyncMock:
    client = AsyncMock()
    client.fetch_portfolio_state = AsyncMock(
        return_value=PortfolioState(
            equity_usd=Decimal("10000"),
            available_usd=Decimal("10000"),
            margin_used_usd=Decimal("0"),
            n_open_positions=0,
            unrealized_pnl_usd=Decimal("0"),
            open_positions=[],
        )
    )
    client.execute_action = AsyncMock(return_value=[])
    client.check_position_closure = AsyncMock(return_value=None)
    return client


# Exception injected at the LLM invoke step → (errors.error_kind, runs.failure_stage).
# LLMUnrecoverableError needs (primary, fallback) exceptions per its constructor.
_ERROR_CASES = [
    pytest.param(LLMAuthError("invalid api key"), "LLMAuthError", "llm_auth", id="auth"),
    pytest.param(
        LLMRateLimitError("429 too many requests"), "LLMRateLimitError", "llm_rate", id="rate"
    ),
    pytest.param(
        LLMUnrecoverableError(ValueError("primary parse fail"), ValueError("fallback parse fail")),
        "LLMUnrecoverableError",
        "llm_parse",
        id="parse",
    ),
    pytest.param(RuntimeError("unexpected boom"), "RuntimeError", "error", id="default"),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFailedRunErrorPersistence:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("exc", "expected_kind", "expected_stage"), _ERROR_CASES)
    async def test_failed_run_persists_error_row_and_failure_stage(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed_ids: dict[str, str],
        exc: Exception,
        expected_kind: str,
        expected_stage: str,
    ) -> None:
        """A tick that raises at LLM invoke ends FAILED with a per-class failure_stage AND
        a single ``errors`` row carrying the class name, message and stack trace."""
        settings = _make_agent_settings(seed_ids)
        llm_client = AsyncMock()
        llm_client.invoke = AsyncMock(side_effect=exc)

        loop = DecisionLoop(
            settings=settings,
            llm_client=llm_client,
            hl_client=_stub_hl_client(),
            session_factory=session_factory,
        )

        # Generic handler re-raises (unchanged control flow); the persist happens first.
        with pytest.raises(type(exc)):
            await loop.run_once(_TICK_ID, _TICK_AT)

        async with session_factory() as session:
            run = await session.scalar(
                select(Run).where(Run.model_id == seed_ids["model_id"], Run.tick_id == _TICK_ID)
            )
            assert run is not None
            assert run.status == RunStatus.FAILED.value
            assert run.failure_stage == expected_stage
            assert run.run_completed_at is not None

            errors = (await session.scalars(select(Error).where(Error.run_id == run.id))).all()
            # Exactly one error row — not zero (the finding-D bug) and not duplicated.
            assert len(errors) == 1
            err = errors[0]
            assert err.error_kind == expected_kind
            assert err.error_message == str(exc)
            assert err.stack_trace is not None
            assert expected_kind in err.stack_trace  # real traceback, not a placeholder
            assert str(err.experiment_id) == seed_ids["experiment_id"]
            assert err.model_id == seed_ids["model_id"]

    @pytest.mark.asyncio
    async def test_timeout_run_persists_error_row_and_timeout_stage(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed_ids: dict[str, str],
    ) -> None:
        """A whole-tick timeout ends TIMEOUT/failure_stage='timeout' and also persists a
        TimeoutError row — the timeout branch now records to ``errors`` too (finding D)."""
        settings = _make_agent_settings(seed_ids, hard_timeout_seconds=1)

        async def _slow_invoke(prompt: str, *, timeout_seconds: int = 90) -> object:
            await asyncio.sleep(10)  # exceeds the 1s hard timeout → asyncio TimeoutError

        llm_client = AsyncMock()
        llm_client.invoke = AsyncMock(side_effect=_slow_invoke)

        loop = DecisionLoop(
            settings=settings,
            llm_client=llm_client,
            hl_client=_stub_hl_client(),
            session_factory=session_factory,
        )

        # Timeout branch swallows and returns the run_id (does not re-raise).
        run_id = await loop.run_once(_TICK_ID, _TICK_AT)
        assert run_id is not None

        async with session_factory() as session:
            run = await session.get(Run, uuid.UUID(run_id))
            assert run is not None
            assert run.status == RunStatus.TIMEOUT.value
            assert run.failure_stage == "timeout"

            errors = (await session.scalars(select(Error).where(Error.run_id == run.id))).all()
            assert len(errors) == 1
            assert errors[0].error_kind == "TimeoutError"
            assert errors[0].stack_trace is not None
