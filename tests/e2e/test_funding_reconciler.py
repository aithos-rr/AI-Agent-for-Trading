"""E2E: FundingReconciler writes funding_events from HL userFunding (finding B / ADR-0031).

Drives FundingReconciler.reconcile against a REAL Postgres with a fake funding source that
returns records in the REAL Hyperliquid ``userFunding`` shape. TRIPWIRE: pre-fix there was
NO funding writer, so funding_events stayed at 0 over 4 days (finding B) — these tests assert
rows ARE created (and are idempotent), so they only pass with the ledger implemented.

Self-contained commit-safe seed (unique ids + on_conflict on the shared prompt-template PK):
these tests COMMIT so the reconciler's own session sees the data, so they cannot rely on the
per-function rollback isolation the ``db_session``-based repository tests use.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
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
from aiat.db.models.funding_event import FundingEvent
from aiat.db.models.model import Model
from aiat.db.models.position import Position
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from aiat.orchestration.funding_reconciler import FundingReconciler

_PT_TEXT = "You are a trading agent (funding e2e)."
_PT_HASH = hashlib.sha256(_PT_TEXT.encode()).hexdigest()
_GIT_SHA = "abc1234"

# opened_at and funding-record times chosen so exactly one record lands after the open.
_OPENED_AT = datetime(2023, 5, 12, 0, 0, 0, tzinfo=UTC)
_AFTER_MS = int(datetime(2023, 5, 12, 1, 0, 0, tzinfo=UTC).timestamp() * 1000)  # 1h after open
_BEFORE_MS = int(datetime(2023, 5, 11, 23, 0, 0, tzinfo=UTC).timestamp() * 1000)  # before open
_NOW_MS = int(datetime(2023, 5, 12, 2, 0, 0, tzinfo=UTC).timestamp() * 1000)


@pytest_asyncio.fixture(scope="function")
async def session_factory(db_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


class _Seed:
    def __init__(self, experiment_id: uuid.UUID, model_id: str, wallet: str) -> None:
        self.experiment_id = experiment_id
        self.model_id = model_id
        self.wallet = wallet


async def _seed_open_btc_position(session: AsyncSession, opened_at: datetime = _OPENED_AT) -> _Seed:
    """Insert the full FK chain + one OPEN BTC LONG position, committing nothing here."""
    exp_id = uuid.uuid4()
    model_id = f"openai-gpt4o-fund-{uuid.uuid4().hex[:8]}"
    wallet = f"0x{uuid.uuid4().hex}"
    snap_id = uuid.uuid4()
    run_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    action_id = uuid.uuid4()
    tick_at = opened_at

    session.add(
        Experiment(
            id=exp_id,
            name=f"fund-exp-{exp_id.hex[:8]}",
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
            wallet_address=wallet,
            pricing_input_usd_per_1m=Decimal("5.000000"),
            pricing_output_usd_per_1m=Decimal("15.000000"),
        )
    )
    await session.flush()
    # Shared PK across tests → insert-if-absent (these tests commit, no rollback isolation).
    await session.execute(
        pg_insert(PromptTemplate)
        .values(
            sha256_hash=_PT_HASH,
            label="fund-pt-shared",
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
            tick_id=tick_at.isoformat(),
            tick_at=tick_at,
            context_hash=hashlib.sha256(b"fund").hexdigest(),
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
            tick_id=tick_at.isoformat(),
            scheduled_for=tick_at,
            run_started_at=tick_at,
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
            decided_at=tick_at,
            portfolio_reasoning="Bull",
            risk_assessment="Low",
            latency_ms=500,
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
            action_reasoning="Strong momentum; enter LONG with defined risk.",
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
            id=uuid.uuid4(),
            experiment_id=exp_id,
            model_id=model_id,
            opening_run_id=run_id,
            symbol="BTC",
            side="LONG",
            opening_action_id=action_id,
            opened_at=opened_at,
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
    return _Seed(exp_id, model_id, wallet)


class _FakeFundingSource:
    """Returns canned userFunding records (real HL shape); records the wallets queried."""

    def __init__(self, records: list[dict]) -> None:
        self._records = records
        self.queried_users: list[str] = []

    async def user_funding_history(
        self, user: str, start_time_ms: int, end_time_ms: int | None = None
    ) -> list[dict]:
        self.queried_users.append(user)
        return self._records


def _funding_rec(coin: str, time_ms: int, usdc: str, rate: str = "0.0000125") -> dict:
    return {
        "time": time_ms,
        "hash": "0xabc",
        "delta": {"type": "funding", "coin": coin, "usdc": usdc, "szi": "1.0", "fundingRate": rate},
    }


@pytest.mark.asyncio
async def test_reconcile_creates_funding_event_for_open_position(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        seed = await _seed_open_btc_position(s)
        await s.commit()

    source = _FakeFundingSource(
        [
            _funding_rec("BTC", _AFTER_MS, "-0.31", "0.0000125"),  # after open → created
            _funding_rec("BTC", _BEFORE_MS, "-0.99"),  # before open → not attributed
            _funding_rec("ETH", _AFTER_MS, "-5.00"),  # no open ETH position → skipped
        ]
    )
    reconciler = FundingReconciler(session_factory, source, str(seed.experiment_id))

    result = await reconciler.reconcile(_NOW_MS)

    assert result.created == 1
    assert source.queried_users == [seed.wallet]  # queried the model's real wallet address

    async with session_factory() as s:
        rows = (
            await s.scalars(
                select(FundingEvent).where(FundingEvent.experiment_id == seed.experiment_id)
            )
        ).all()
        assert len(rows) == 1
        fe = rows[0]
        assert fe.funding_amount_usd == Decimal("-0.31")
        assert fe.funding_rate == Decimal("0.0000125")
        assert fe.funding_period_end == datetime.fromtimestamp(_AFTER_MS / 1000, tz=UTC)
        assert fe.funding_period_start == fe.funding_period_end - timedelta(hours=1)
        assert fe.model_id == seed.model_id


@pytest.mark.asyncio
async def test_reconcile_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        seed = await _seed_open_btc_position(s)
        await s.commit()

    source = _FakeFundingSource([_funding_rec("BTC", _AFTER_MS, "-0.31")])
    reconciler = FundingReconciler(session_factory, source, str(seed.experiment_id))

    first = await reconciler.reconcile(_NOW_MS)
    second = await reconciler.reconcile(_NOW_MS)

    assert first.created == 1
    assert second.created == 0
    assert second.skipped == 1  # same (position, period_end) natural key → not duplicated

    async with session_factory() as s:
        count = await s.scalar(
            select(func.count())
            .select_from(FundingEvent)
            .where(FundingEvent.experiment_id == seed.experiment_id)
        )
        assert count == 1
