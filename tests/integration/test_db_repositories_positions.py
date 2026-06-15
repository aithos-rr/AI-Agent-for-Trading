"""Integration tests for PositionsRepository (§7.6, M4-T05).

Tests the open→close→outcomes lifecycle on an ephemeral Postgres instance.
Each test function gets an isolated transaction (rolled back on teardown via db_session).
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.action import DecisionAction
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.decision import Decision
from aiat.db.models.experiment import Experiment
from aiat.db.models.model import Model
from aiat.db.models.outcome import Outcome
from aiat.db.models.position import Position
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run
from aiat.db.repositories.positions import PositionsRepository
from aiat.domain.enums import CloseReason, OrderKind
from aiat.execution.hyperliquid_client import OrderResult, PositionClosureInfo

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
    opening_run_id: uuid.UUID
    closing_run_id: uuid.UUID
    context_snapshot_id: uuid.UUID
    decision_id: uuid.UUID
    action_id: uuid.UUID


async def _seed(session: AsyncSession) -> SeedIds:
    """Insert the minimum FK chain needed to create a Position."""
    exp_id = uuid.uuid4()
    model_id = f"openai-gpt4o-{uuid.uuid4().hex[:8]}"
    snap_id = uuid.uuid4()
    opening_run_id = uuid.uuid4()
    closing_run_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    action_id = uuid.uuid4()
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

    # opening run
    opening_run = Run(
        id=opening_run_id,
        experiment_id=exp_id,
        model_id=model_id,
        tick_id=_TICK_ID,
        scheduled_for=tick_at,
        run_started_at=tick_at,
        status="success",
        prompt_template_hash=_PT_HASH,
        rendered_prompt_hash="aabbcc",
        context_snapshot_id=snap_id,
        schema_version=_SCHEMA_VERSION,
        git_commit_sha=_GIT_SHA,
    )
    session.add(opening_run)
    await session.flush()

    # closing run (different scheduled_for to avoid unique constraint)
    closing_run = Run(
        id=closing_run_id,
        experiment_id=exp_id,
        model_id=model_id,
        tick_id=_TICK_ID,
        scheduled_for=tick_at + timedelta(minutes=15),
        run_started_at=tick_at + timedelta(minutes=15),
        status="success",
        prompt_template_hash=_PT_HASH,
        rendered_prompt_hash="aabbcc",
        context_snapshot_id=snap_id,
        schema_version=_SCHEMA_VERSION,
        git_commit_sha=_GIT_SHA,
    )
    session.add(closing_run)
    await session.flush()

    decision = Decision(
        id=decision_id,
        run_id=opening_run_id,
        experiment_id=exp_id,
        model_id=model_id,
        decided_at=tick_at,
        portfolio_reasoning="Bull market",
        risk_assessment="Low risk",
        latency_ms=500,
        raw_payload={},
    )
    session.add(decision)
    await session.flush()

    action = DecisionAction(
        id=action_id,
        decision_id=decision_id,
        experiment_id=exp_id,
        model_id=model_id,
        run_id=opening_run_id,
        symbol="BTC",
        confidence=Decimal("0.7000"),
        time_horizon_min=60,
        action_reasoning="Strong momentum signal; enter LONG with defined risk.",
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
    session.add(action)
    await session.flush()

    return SeedIds(
        experiment_id=exp_id,
        model_id=model_id,
        opening_run_id=opening_run_id,
        closing_run_id=closing_run_id,
        context_snapshot_id=snap_id,
        decision_id=decision_id,
        action_id=action_id,
    )


def _make_order_results(
    entry_price: Decimal = Decimal("100.00"),
    size_units: Decimal = Decimal("1.0"),
    fee_usd: Decimal = Decimal("0.30"),
) -> list[OrderResult]:
    return [
        OrderResult(
            hl_order_id=str(uuid.uuid4()),
            client_order_id=str(uuid.uuid4()),
            order_kind=OrderKind.ENTRY,
            status="filled",
            requested_price=None,
            filled_price=entry_price,
            requested_size_units=size_units,
            filled_size_units=size_units,
            slippage_bps=Decimal("5"),
            fee_usd=fee_usd,
            raw_response={},
        ),
        OrderResult(
            hl_order_id=str(uuid.uuid4()),
            client_order_id=str(uuid.uuid4()),
            order_kind=OrderKind.STOP_LOSS,
            status="triggered",
            requested_price=None,
            filled_price=None,
            requested_size_units=size_units,
            filled_size_units=None,
            slippage_bps=None,
            fee_usd=None,
            raw_response={},
        ),
        OrderResult(
            hl_order_id=str(uuid.uuid4()),
            client_order_id=str(uuid.uuid4()),
            order_kind=OrderKind.TAKE_PROFIT,
            status="triggered",
            requested_price=None,
            filled_price=None,
            requested_size_units=size_units,
            filled_size_units=None,
            slippage_bps=None,
            fee_usd=None,
            raw_response={},
        ),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_position_creates_rows(db_session: AsyncSession) -> None:
    """open_position inserts position + orders + fee_events."""
    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)

    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(),
        run_id=str(ids.opening_run_id),
    )

    pos = await db_session.get(Position, uuid.UUID(pos_id))
    assert pos is not None
    assert pos.model_id == ids.model_id
    assert pos.symbol == "BTC"
    assert pos.side == "LONG"
    assert pos.entry_price == Decimal("100.00")
    assert pos.size_units == Decimal("1.0")
    assert pos.leverage == Decimal("3.00")
    assert pos.notional_value_usd == Decimal("100.00")  # 1.0 * 100
    # Postgres NUMERIC(20,8) truncates to 8 dp; round expected to match.
    assert pos.initial_margin_usd == round(Decimal("100.00") / Decimal("3.00"), 8)
    # stop_loss_price = 100 * (1 - 0.02) = 98
    assert pos.stop_loss_price == Decimal("100.00") * (1 - Decimal("0.0200"))
    # take_profit_price = 100 * (1 + 0.04) = 104
    assert pos.take_profit_price == Decimal("100.00") * (1 + Decimal("0.0400"))
    assert pos.closed_at is None
    assert pos.opening_action_id == ids.action_id


@pytest.mark.asyncio
async def test_open_position_falls_back_to_requested_size_when_fill_unknown(
    db_session: AsyncSession,
) -> None:
    """When the entry fill size is unknown (None), open_position uses requested_size_units.

    Guards the explicit `is not None` fallback (a real Decimal("0") fill is distinct
    from None and must not be silently rewritten — it would fail loud downstream).
    """
    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)

    orders = _make_order_results()
    entry = orders[0].model_copy(
        update={"requested_size_units": Decimal("2.0"), "filled_size_units": None}
    )
    orders[0] = entry

    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=orders,
        run_id=str(ids.opening_run_id),
    )
    pos = await db_session.get(Position, uuid.UUID(pos_id))
    assert pos is not None
    assert pos.size_units == Decimal("2.0")  # requested used as fallback
    assert pos.notional_value_usd == Decimal("200.00")  # 2.0 * 100


@pytest.mark.asyncio
async def test_open_position_orders_and_fees_created(db_session: AsyncSession) -> None:
    """open_position creates 3 orders; 1 fee_event (entry only has fee_usd)."""
    from sqlalchemy import select

    from aiat.db.models.fee_event import FeeEvent
    from aiat.db.models.order import Order

    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)
    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(fee_usd=Decimal("0.50")),
        run_id=str(ids.opening_run_id),
    )

    orders = (
        (await db_session.execute(select(Order).where(Order.decision_action_id == ids.action_id)))
        .scalars()
        .all()
    )
    assert len(orders) == 3

    fees = (
        (
            await db_session.execute(
                select(FeeEvent).where(FeeEvent.position_id == uuid.UUID(pos_id))
            )
        )
        .scalars()
        .all()
    )
    assert len(fees) == 1
    assert fees[0].fee_usd == Decimal("0.50")
    assert fees[0].fee_type == "taker_open"


@pytest.mark.asyncio
async def test_close_position_updates_and_creates_outcome(db_session: AsyncSession) -> None:
    """close_position updates position fields and inserts an outcomes row."""
    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)
    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(fee_usd=Decimal("0.30")),
        run_id=str(ids.opening_run_id),
    )

    closure = PositionClosureInfo(
        closed_at="2026-01-15T13:00:00+00:00",
        exit_price=Decimal("105.00"),
        close_reason=CloseReason.TAKE_PROFIT,
        realized_pnl_usd=Decimal("5.00"),
    )
    await repo.close_position(
        position_id=pos_id,
        closure=closure,
        closing_run_id=str(ids.closing_run_id),
    )

    pos = await db_session.get(Position, uuid.UUID(pos_id))
    assert pos is not None
    assert pos.exit_price == Decimal("105.00")
    assert pos.close_reason == "take_profit"
    assert pos.realized_pnl_usd == Decimal("5.00")
    assert pos.closed_at is not None

    outcome = (
        await db_session.execute(
            __import__("sqlalchemy", fromlist=["select"])
            .select(Outcome)
            .where(Outcome.position_id == uuid.UUID(pos_id))
        )
    ).scalar_one_or_none()
    assert outcome is not None
    assert outcome.realized_pnl_gross_usd == Decimal("5.00")
    assert outcome.sum_fees_usd == Decimal("0.30")
    assert outcome.pnl_net_fee_usd == Decimal("4.70")
    assert outcome.pnl_net_fee_funding_usd == Decimal("4.70")
    # tax-sim column is a placeholder populated by compute_tax_sim.py in M5 (ADR-0014);
    # close_position must leave it 0, consistent with OutcomeResolver — never net PnL.
    assert outcome.pnl_net_fee_funding_tax_sim_usd == Decimal("0")
    assert outcome.was_profitable_net is True
    assert outcome.decision_action_confidence == Decimal("0.7000")
    assert outcome.decision_action_time_horizon_min == 60
    assert outcome.opening_run_id == ids.opening_run_id
    assert outcome.closing_run_id == ids.closing_run_id
    # 60 min holding → horizon_met True (≤ 60)
    assert outcome.horizon_met is True


@pytest.mark.asyncio
async def test_close_position_unprofitable(db_session: AsyncSession) -> None:
    """was_profitable_net=False when pnl_net_fee_funding_usd ≤ 0."""
    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)
    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(fee_usd=Decimal("6.00")),
        run_id=str(ids.opening_run_id),
    )

    closure = PositionClosureInfo(
        closed_at="2026-01-15T13:00:00+00:00",
        exit_price=Decimal("95.00"),
        close_reason=CloseReason.STOP_LOSS,
        realized_pnl_usd=Decimal("-5.00"),
    )
    await repo.close_position(
        position_id=pos_id,
        closure=closure,
        closing_run_id=str(ids.closing_run_id),
    )

    from sqlalchemy import select

    outcome = (
        await db_session.execute(select(Outcome).where(Outcome.position_id == uuid.UUID(pos_id)))
    ).scalar_one()
    assert outcome.was_profitable_net is False
    assert outcome.realized_pnl_gross_usd == Decimal("-5.00")
    assert outcome.pnl_net_fee_usd == Decimal("-11.00")


@pytest.mark.asyncio
async def test_duplicate_opening_action_raises_integrity_error(
    db_session: AsyncSession,
) -> None:
    """opening_action_id UNIQUE: second open_position for same action → IntegrityError."""
    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)
    await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(),
        run_id=str(ids.opening_run_id),
    )

    with pytest.raises(IntegrityError):
        await repo.open_position(
            action_id=str(ids.action_id),
            order_results=_make_order_results(),
            run_id=str(ids.opening_run_id),
        )


@pytest.mark.asyncio
async def test_list_open_for_model_returns_open_only(db_session: AsyncSession) -> None:
    """list_open_for_model returns only positions with closed_at IS NULL."""
    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)

    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(),
        run_id=str(ids.opening_run_id),
    )

    open_positions = await repo.list_open_for_model(ids.model_id)
    assert len(open_positions) == 1
    assert str(open_positions[0].id) == pos_id

    closure = PositionClosureInfo(
        closed_at="2026-01-15T13:00:00+00:00",
        exit_price=Decimal("110.00"),
        close_reason=CloseReason.MODEL_CLOSE,
        realized_pnl_usd=Decimal("10.00"),
    )
    await repo.close_position(
        position_id=pos_id,
        closure=closure,
        closing_run_id=str(ids.closing_run_id),
    )

    open_after_close = await repo.list_open_for_model(ids.model_id)
    assert len(open_after_close) == 0


@pytest.mark.asyncio
async def test_fee_event_run_id_matches_opening_run(db_session: AsyncSession) -> None:
    """fee_events.run_id, model_id, experiment_id all match the FK chain from open_position."""
    from sqlalchemy import select

    from aiat.db.models.fee_event import FeeEvent

    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)
    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(fee_usd=Decimal("1.00")),
        run_id=str(ids.opening_run_id),
    )

    fees = (
        (
            await db_session.execute(
                select(FeeEvent).where(FeeEvent.position_id == uuid.UUID(pos_id))
            )
        )
        .scalars()
        .all()
    )
    assert len(fees) == 1
    assert fees[0].run_id == ids.opening_run_id
    assert fees[0].model_id == ids.model_id
    assert fees[0].experiment_id == ids.experiment_id


@pytest.mark.asyncio
async def test_close_position_with_funding_events(db_session: AsyncSession) -> None:
    """sum_funding_usd from funding_events is included in pnl_net_fee_funding_usd."""
    from sqlalchemy import select

    from aiat.db.models.funding_event import FundingEvent

    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)
    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(fee_usd=Decimal("0.50")),
        run_id=str(ids.opening_run_id),
    )

    tick_at = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    funding = FundingEvent(
        id=uuid.uuid4(),
        position_id=uuid.UUID(pos_id),
        experiment_id=ids.experiment_id,
        model_id=ids.model_id,
        funding_rate=Decimal("0.0001"),
        funding_amount_usd=Decimal("2.00"),
        funding_period_start=tick_at,
        funding_period_end=tick_at + timedelta(hours=8),
    )
    db_session.add(funding)
    await db_session.flush()

    closure = PositionClosureInfo(
        closed_at="2026-01-15T20:00:00+00:00",
        exit_price=Decimal("110.00"),
        close_reason=CloseReason.MODEL_CLOSE,
        realized_pnl_usd=Decimal("10.00"),
    )
    await repo.close_position(
        position_id=pos_id,
        closure=closure,
        closing_run_id=str(ids.closing_run_id),
    )

    outcome = (
        await db_session.execute(select(Outcome).where(Outcome.position_id == uuid.UUID(pos_id)))
    ).scalar_one()

    assert outcome.sum_fees_usd == Decimal("0.50")
    assert outcome.sum_funding_usd == Decimal("2.00")
    # pnl_net_fee = 10.00 - 0.50 = 9.50
    # pnl_net_fee_funding = 9.50 - 2.00 = 7.50
    assert outcome.pnl_net_fee_usd == Decimal("9.50")
    assert outcome.pnl_net_fee_funding_usd == Decimal("7.50")
    assert outcome.was_profitable_net is True


@pytest.mark.asyncio
async def test_close_position_consistency_check_enforced(db_session: AsyncSession) -> None:
    """chk_position_closed_consistency: only closed_at set → IntegrityError."""
    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)
    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(),
        run_id=str(ids.opening_run_id),
    )

    pos = await db_session.get(Position, uuid.UUID(pos_id))
    assert pos is not None
    # Partially close: set only closed_at; exit_price/realized_pnl_usd/close_reason stay NULL
    pos.closed_at = datetime.now(UTC)

    with pytest.raises(IntegrityError):
        await db_session.flush()
