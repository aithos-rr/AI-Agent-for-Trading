"""Integration tests for SnapshotsRepository and RunsRepository (§7.6, M5-T02a).

Tests account/context snapshot operations and run lifecycle management on an
ephemeral Postgres instance. Each test gets an isolated transaction via the
db_session fixture (rolled back on teardown).
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.account_snapshot import AccountSnapshot
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.error import Error
from aiat.db.models.experiment import Experiment
from aiat.db.models.model import Model
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from aiat.db.repositories.runs import RunsRepository
from aiat.db.repositories.snapshots import SnapshotsRepository
from aiat.domain.enums import RunStatus
from aiat.domain.schemas import OpenPositionSummary, PortfolioState

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

_TICK_ID = "2026-01-15T12:00:00"
_SCHEMA_VERSION = "v2"
_GIT_SHA = "abc1234"
_PT_TEXT = "You are a trading agent."
_PT_HASH = hashlib.sha256(_PT_TEXT.encode()).hexdigest()


@dataclass
class SeedIds:
    experiment_id: uuid.UUID
    model_id: str
    context_snapshot_id: uuid.UUID


async def _seed_base(session: AsyncSession) -> SeedIds:
    """Insert the minimum FK chain: experiment + model + prompt_template + context_snapshot."""
    exp_id = uuid.uuid4()
    model_id = f"openai-gpt4o-{uuid.uuid4().hex[:8]}"
    snap_id = uuid.uuid4()
    tick_at = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

    experiment = Experiment(
        id=exp_id,
        name=f"test-exp-{exp_id.hex[:8]}",
        started_at=datetime.now(UTC),
        git_commit_sha=_GIT_SHA,
        config_snapshot={},
    )
    session.add(experiment)
    await session.flush()

    model = Model(
        id=model_id,
        provider="openai",
        model_name_api="gpt-4o",
        tier="premium",
        geography="USA",
        wallet_address=f"0x{uuid.uuid4().hex}",
        pricing_input_usd_per_1m=Decimal("5.000000"),
        pricing_output_usd_per_1m=Decimal("15.000000"),
    )
    session.add(model)
    await session.flush()

    prompt_tmpl = PromptTemplate(
        sha256_hash=_PT_HASH,
        label=f"test-pt-{uuid.uuid4().hex[:8]}",
        template_text=_PT_TEXT,
        confidence_def="Probability that the action yields positive PnL.",
        controlled_signals=[],
    )
    session.add(prompt_tmpl)
    await session.flush()

    snapshot = ContextSnapshot(
        id=snap_id,
        experiment_id=exp_id,
        tick_id=_TICK_ID,
        tick_at=tick_at,
        context_hash="deadbeef",
        context_json={},
        source_timestamps={},
        build_duration_ms=100,
    )
    session.add(snapshot)
    await session.flush()

    return SeedIds(
        experiment_id=exp_id,
        model_id=model_id,
        context_snapshot_id=snap_id,
    )


async def _seed_run(session: AsyncSession, ids: SeedIds) -> uuid.UUID:
    """Insert a Run row and return its id."""
    run_id = uuid.uuid4()
    run = Run(
        id=run_id,
        experiment_id=ids.experiment_id,
        model_id=ids.model_id,
        tick_id=_TICK_ID,
        scheduled_for=datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC),
        run_started_at=datetime.now(UTC),
        status="running",
        prompt_template_hash=_PT_HASH,
        rendered_prompt_hash="aabbcc",
        context_snapshot_id=ids.context_snapshot_id,
        schema_version=_SCHEMA_VERSION,
        git_commit_sha=_GIT_SHA,
    )
    session.add(run)
    await session.flush()
    return run_id


def _make_portfolio(*, n_positions: int = 0) -> PortfolioState:
    positions = []
    if n_positions > 0:
        positions = [
            OpenPositionSummary(
                symbol="BTC",
                side="LONG",
                entry_price=Decimal("50000"),
                current_price=Decimal("51000"),
                size_units=Decimal("0.01"),
                leverage=Decimal("3"),
                unrealized_pnl_usd=Decimal("10"),
                age_minutes=15,
            )
        ]
    return PortfolioState(
        equity_usd=Decimal("1000.00"),
        available_usd=Decimal("800.00"),
        margin_used_usd=Decimal("200.00"),
        n_open_positions=len(positions),
        unrealized_pnl_usd=Decimal("0.00") if not positions else Decimal("10.00"),
        open_positions=positions,
    )


# ---------------------------------------------------------------------------
# SnapshotsRepository — persist_account_snapshot
# ---------------------------------------------------------------------------


async def test_persist_account_snapshot_creates_row(db_session: AsyncSession) -> None:
    """persist_account_snapshot inserts an account_snapshots row."""
    ids = await _seed_base(db_session)
    run_id = await _seed_run(db_session, ids)
    repo = SnapshotsRepository(db_session)

    snap_id = await repo.persist_account_snapshot(
        str(run_id),
        _make_portfolio(),
    )

    row = await db_session.get(AccountSnapshot, uuid.UUID(snap_id))
    assert row is not None
    assert row.run_id == run_id
    assert row.equity_usd == Decimal("1000.00")
    assert row.available_usd == Decimal("800.00")
    assert row.margin_used_usd == Decimal("200.00")
    assert row.n_open_positions == 0
    assert row.unrealized_pnl_usd == Decimal("0.00")


async def test_persist_account_snapshot_sets_portfolio_state_hash(db_session: AsyncSession) -> None:
    """portfolio_state_hash is set to the SHA-256 of the portfolio JSON."""
    ids = await _seed_base(db_session)
    run_id = await _seed_run(db_session, ids)
    repo = SnapshotsRepository(db_session)
    portfolio = _make_portfolio()

    import hashlib

    expected_hash = hashlib.sha256(portfolio.model_dump_json().encode()).hexdigest()
    snap_id = await repo.persist_account_snapshot(str(run_id), portfolio)

    row = await db_session.get(AccountSnapshot, uuid.UUID(snap_id))
    assert row is not None
    assert row.portfolio_state_hash == expected_hash


async def test_persist_account_snapshot_total_position_value(db_session: AsyncSession) -> None:
    """total_position_value_usd is sum of current_price × size_units."""
    ids = await _seed_base(db_session)
    run_id = await _seed_run(db_session, ids)
    repo = SnapshotsRepository(db_session)
    portfolio = _make_portfolio(n_positions=1)  # BTC: 51000 * 0.01 = 510

    snap_id = await repo.persist_account_snapshot(str(run_id), portfolio)
    row = await db_session.get(AccountSnapshot, uuid.UUID(snap_id))
    assert row is not None
    assert row.total_position_value_usd == Decimal("510.00")


async def test_persist_account_snapshot_inherits_experiment_model(db_session: AsyncSession) -> None:
    """experiment_id and model_id are inherited from the Run row."""
    ids = await _seed_base(db_session)
    run_id = await _seed_run(db_session, ids)
    repo = SnapshotsRepository(db_session)

    snap_id = await repo.persist_account_snapshot(str(run_id), _make_portfolio())
    row = await db_session.get(AccountSnapshot, uuid.UUID(snap_id))
    assert row is not None
    assert row.experiment_id == ids.experiment_id
    assert row.model_id == ids.model_id


async def test_persist_account_snapshot_run_unique_constraint(db_session: AsyncSession) -> None:
    """A second snapshot for the same run raises IntegrityError (run_id UNIQUE)."""
    ids = await _seed_base(db_session)
    run_id = await _seed_run(db_session, ids)
    repo = SnapshotsRepository(db_session)

    await repo.persist_account_snapshot(str(run_id), _make_portfolio())
    with pytest.raises(IntegrityError):
        await repo.persist_account_snapshot(str(run_id), _make_portfolio())


async def test_persist_account_snapshot_missing_run_raises(db_session: AsyncSession) -> None:
    """persist_account_snapshot with unknown run_id raises ValueError."""
    await _seed_base(db_session)
    repo = SnapshotsRepository(db_session)

    with pytest.raises(ValueError, match="not found"):
        await repo.persist_account_snapshot(str(uuid.uuid4()), _make_portfolio())


# ---------------------------------------------------------------------------
# SnapshotsRepository — get_context_snapshot
# ---------------------------------------------------------------------------


async def test_get_context_snapshot_returns_existing(db_session: AsyncSession) -> None:
    """get_context_snapshot returns the ContextSnapshot for the given tick."""
    ids = await _seed_base(db_session)
    repo = SnapshotsRepository(db_session)

    result = await repo.get_context_snapshot(str(ids.experiment_id), _TICK_ID)
    assert result is not None
    assert result.id == ids.context_snapshot_id
    assert result.tick_id == _TICK_ID


async def test_get_context_snapshot_returns_none_if_missing(db_session: AsyncSession) -> None:
    """get_context_snapshot returns None for a non-existent (experiment_id, tick_id)."""
    ids = await _seed_base(db_session)
    repo = SnapshotsRepository(db_session)

    result = await repo.get_context_snapshot(str(ids.experiment_id), "2099-01-01T00:00:00")
    assert result is None


async def test_get_context_snapshot_none_for_wrong_experiment(db_session: AsyncSession) -> None:
    """get_context_snapshot returns None when experiment_id does not match."""
    await _seed_base(db_session)
    repo = SnapshotsRepository(db_session)

    result = await repo.get_context_snapshot(str(uuid.uuid4()), _TICK_ID)
    assert result is None


# ---------------------------------------------------------------------------
# RunsRepository — create_run
# ---------------------------------------------------------------------------


async def test_create_run_creates_row_with_running_status(db_session: AsyncSession) -> None:
    """create_run inserts a Run row with status='running'."""
    ids = await _seed_base(db_session)
    repo = RunsRepository(db_session)

    run_id = await repo.create_run(
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        tick_id=_TICK_ID,
        scheduled_for=datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC),
        prompt_template_hash=_PT_HASH,
        rendered_prompt_hash="aabbcc",
        context_snapshot_id=str(ids.context_snapshot_id),
        schema_version=_SCHEMA_VERSION,
        git_commit_sha=_GIT_SHA,
    )

    row = await db_session.get(Run, uuid.UUID(run_id))
    assert row is not None
    assert row.status == "running"
    assert row.experiment_id == ids.experiment_id
    assert row.model_id == ids.model_id
    assert row.tick_id == _TICK_ID
    assert row.run_completed_at is None


async def test_create_run_duplicate_exp_model_sched_raises(db_session: AsyncSession) -> None:
    """Two runs with same (experiment_id, model_id, scheduled_for) raise IntegrityError."""
    ids = await _seed_base(db_session)
    repo = RunsRepository(db_session)
    sched = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

    await repo.create_run(
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        tick_id=_TICK_ID,
        scheduled_for=sched,
        prompt_template_hash=_PT_HASH,
        rendered_prompt_hash="aabbcc",
        context_snapshot_id=str(ids.context_snapshot_id),
        schema_version=_SCHEMA_VERSION,
        git_commit_sha=_GIT_SHA,
    )
    with pytest.raises(IntegrityError):
        await repo.create_run(
            experiment_id=str(ids.experiment_id),
            model_id=ids.model_id,
            tick_id=_TICK_ID,
            scheduled_for=sched,
            prompt_template_hash=_PT_HASH,
            rendered_prompt_hash="aabbcc",
            context_snapshot_id=str(ids.context_snapshot_id),
            schema_version=_SCHEMA_VERSION,
            git_commit_sha=_GIT_SHA,
        )


# ---------------------------------------------------------------------------
# RunsRepository — update_status
# ---------------------------------------------------------------------------


async def test_update_status_success_sets_completed_at(db_session: AsyncSession) -> None:
    """update_status to SUCCESS sets run_completed_at."""
    ids = await _seed_base(db_session)
    run_id = await _seed_run(db_session, ids)
    repo = RunsRepository(db_session)

    await repo.update_status(str(run_id), RunStatus.SUCCESS)

    row = await db_session.get(Run, run_id)
    assert row is not None
    assert row.status == "success"
    assert row.run_completed_at is not None


async def test_update_status_failed_with_failure_stage(db_session: AsyncSession) -> None:
    """update_status to FAILED sets failure_stage."""
    ids = await _seed_base(db_session)
    run_id = await _seed_run(db_session, ids)
    repo = RunsRepository(db_session)

    await repo.update_status(str(run_id), RunStatus.FAILED, failure_stage="persist")

    row = await db_session.get(Run, run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.failure_stage == "persist"


async def test_update_status_unknown_run_raises(db_session: AsyncSession) -> None:
    """update_status with unknown run_id raises ValueError."""
    await _seed_base(db_session)
    repo = RunsRepository(db_session)

    with pytest.raises(ValueError, match="not found"):
        await repo.update_status(str(uuid.uuid4()), RunStatus.SUCCESS)


# ---------------------------------------------------------------------------
# RunsRepository — log_error
# ---------------------------------------------------------------------------


async def test_log_error_inserts_error_row(db_session: AsyncSession) -> None:
    """log_error persists an errors row with all nullable FKs as None."""
    repo = RunsRepository(db_session)

    await repo.log_error(
        error_kind="LLMTimeoutError",
        error_message="primary attempt timed out after 90s",
    )

    result = await db_session.execute(select(Error).where(Error.error_kind == "LLMTimeoutError"))
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.error_message == "primary attempt timed out after 90s"
    assert row.run_id is None
    assert row.experiment_id is None
    assert row.model_id is None


async def test_log_error_with_run_fk(db_session: AsyncSession) -> None:
    """log_error links to an existing run via run_id."""
    ids = await _seed_base(db_session)
    run_id = await _seed_run(db_session, ids)
    repo = RunsRepository(db_session)

    await repo.log_error(
        run_id=str(run_id),
        experiment_id=str(ids.experiment_id),
        model_id=ids.model_id,
        error_kind="ExecutionRejectedError",
        error_message="order rejected by HL",
        stack_trace="Traceback ...",
        context={"symbol": "BTC"},
    )

    result = await db_session.execute(select(Error).where(Error.run_id == run_id))
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.error_kind == "ExecutionRejectedError"
    assert row.run_id == run_id
    assert row.experiment_id == ids.experiment_id
    assert row.model_id == ids.model_id
    assert row.stack_trace == "Traceback ..."
    assert row.context == {"symbol": "BTC"}
