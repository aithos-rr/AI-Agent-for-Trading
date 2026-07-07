"""Integration tests for PositionsRepository (§7.6, M4-T05).

Tests the open→close→outcomes lifecycle on an ephemeral Postgres instance.
Each test function gets an isolated transaction (rolled back on teardown via db_session).
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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
from aiat.db.repositories.positions import PositionsRepository
from aiat.domain.enums import CloseReason, OrderKind
from aiat.execution.hyperliquid_client import (
    MockHyperliquidClient,
    OrderResult,
    PositionClosureInfo,
)
from aiat.orchestration.decision_loop import DecisionLoop

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


def _make_close_order(
    size_units: Decimal = Decimal("1.0"),
    fee_usd: Decimal | None = None,
    filled_price: Decimal = Decimal("105.00"),
) -> OrderResult:
    """Build a CLOSE OrderResult (ADR-0027 fix (a)).

    fee_usd defaults to None so the existing close-path tests keep their PnL/fee
    assertions unchanged; the dedicated ADR-0027 test passes a real fee.
    """
    return OrderResult(
        hl_order_id=str(uuid.uuid4()),
        client_order_id=str(uuid.uuid4()),
        order_kind=OrderKind.CLOSE,
        status="filled",
        requested_price=None,
        filled_price=filled_price,
        requested_size_units=size_units,
        filled_size_units=size_units,
        slippage_bps=Decimal("5"),
        fee_usd=fee_usd,
        raw_response={},
    )


async def _seed_flat_closing_action(session: AsyncSession, ids: SeedIds) -> uuid.UUID:
    """Seed a DISTINCT FLAT DecisionAction (the closing action) under a closing-run decision.

    closing_action_id must point to the FLAT close action, NOT the opening action —
    reusing the opening action would be a valid FK but semantically false (it would
    mask decision->closure traceability bugs, ADR-0027 fix (b)). Per chk_hold_flat_no_sizing,
    a FLAT action has size_pct=0, leverage=0, entry_type='none', SL/TP NULL.
    """
    closing_decision_id = uuid.uuid4()
    closing_action_id = uuid.uuid4()
    decided_at = datetime(2026, 1, 15, 12, 15, 0, tzinfo=UTC)

    closing_decision = Decision(
        id=closing_decision_id,
        run_id=ids.closing_run_id,
        experiment_id=ids.experiment_id,
        model_id=ids.model_id,
        decided_at=decided_at,
        portfolio_reasoning="Momentum faded",
        risk_assessment="De-risk BTC",
        latency_ms=500,
        raw_payload={},
    )
    session.add(closing_decision)
    await session.flush()

    flat_action = DecisionAction(
        id=closing_action_id,
        decision_id=closing_decision_id,
        experiment_id=ids.experiment_id,
        model_id=ids.model_id,
        run_id=ids.closing_run_id,
        symbol="BTC",
        confidence=Decimal("0.6000"),
        time_horizon_min=60,
        action_reasoning="Momentum faded; close the BTC position (FLAT).",
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
        execution_status="filled",
        executed=True,
    )
    session.add(flat_action)
    await session.flush()
    return closing_action_id


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

    closing_action_id = await _seed_flat_closing_action(db_session, ids)
    # A model-driven close carries a closing_action_id, so under the ADR-0030 conditional
    # CHECK its reason must be model_close (stop_loss/take_profit/liquidated require the
    # action to be NULL). This test exercises the outcome math, not the reason label.
    closure = PositionClosureInfo(
        closed_at="2026-01-15T13:00:00+00:00",
        exit_price=Decimal("105.00"),
        close_reason=CloseReason.MODEL_CLOSE,
        realized_pnl_usd=Decimal("5.00"),
    )
    await repo.close_position(
        position_id=pos_id,
        closure=closure,
        closing_run_id=str(ids.closing_run_id),
        closing_action_id=str(closing_action_id),
        close_order=_make_close_order(),
    )

    pos = await db_session.get(Position, uuid.UUID(pos_id))
    assert pos is not None
    assert pos.exit_price == Decimal("105.00")
    assert pos.close_reason == "model_close"
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

    closing_action_id = await _seed_flat_closing_action(db_session, ids)
    # Model-driven close (has a closing_action_id) → reason must be model_close under the
    # ADR-0030 conditional CHECK. This test exercises the unprofitable-net branch, not the
    # reason label.
    closure = PositionClosureInfo(
        closed_at="2026-01-15T13:00:00+00:00",
        exit_price=Decimal("95.00"),
        close_reason=CloseReason.MODEL_CLOSE,
        realized_pnl_usd=Decimal("-5.00"),
    )
    await repo.close_position(
        position_id=pos_id,
        closure=closure,
        closing_run_id=str(ids.closing_run_id),
        closing_action_id=str(closing_action_id),
        close_order=_make_close_order(),
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

    closing_action_id = await _seed_flat_closing_action(db_session, ids)
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
        closing_action_id=str(closing_action_id),
        close_order=_make_close_order(),
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

    closing_action_id = await _seed_flat_closing_action(db_session, ids)
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
        closing_action_id=str(closing_action_id),
        close_order=_make_close_order(),
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


@pytest.mark.asyncio
async def test_close_position_consistency_check_requires_closing_action_id(
    db_session: AsyncSession,
) -> None:
    """ADR-0027 fix (c) / migration 004: a closed position with every OTHER closing
    field set but closing_action_id NULL must be REJECTED.

    This is the discriminating case for migration 004: under the pre-004 CHECK (whose
    closed branch omitted closing_action_id) this row PASSED; the post-004 CHECK adds
    `closing_action_id IS NOT NULL` to the closed branch, so it must now raise
    IntegrityError. Distinct failure mode from test_close_position_consistency_check_enforced
    (which trips on the OTHER NULL fields and would pass under both the old and new CHECK).
    """
    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)
    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(),
        run_id=str(ids.opening_run_id),
    )

    pos = await db_session.get(Position, uuid.UUID(pos_id))
    assert pos is not None
    # Set every closing field EXCEPT closing_action_id (left NULL): the closed branch of
    # the pre-004 CHECK was satisfied by these four alone; 004 additionally requires
    # closing_action_id IS NOT NULL.
    pos.closed_at = datetime.now(UTC)
    pos.exit_price = Decimal("105.00")
    pos.realized_pnl_usd = Decimal("5.00")
    pos.close_reason = "model_close"
    # pos.closing_action_id intentionally left NULL

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_close_position_persists_close_order_and_action_id(
    db_session: AsyncSession,
) -> None:
    """ADR-0027 fix (a)+(b): the close path inserts exactly one orders row with
    order_kind='close' (hl_order_id populated), populates positions.closing_action_id
    with the FLAT close action, and includes the close fee in sum_fees_usd / net PnL.
    """
    from sqlalchemy import func, select

    from aiat.db.models.fee_event import FeeEvent

    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)
    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(fee_usd=Decimal("0.30")),
        run_id=str(ids.opening_run_id),
    )

    closing_action_id = await _seed_flat_closing_action(db_session, ids)
    close_order = _make_close_order(fee_usd=Decimal("0.50"))
    closure = PositionClosureInfo(
        closed_at="2026-01-15T13:00:00+00:00",
        exit_price=Decimal("105.00"),
        close_reason=CloseReason.MODEL_CLOSE,
        realized_pnl_usd=Decimal("5.00"),
    )
    await repo.close_position(
        position_id=pos_id,
        closure=closure,
        closing_run_id=str(ids.closing_run_id),
        closing_action_id=str(closing_action_id),
        close_order=close_order,
    )

    # (a) exactly one orders row with order_kind='close', hl_order_id populated
    close_rows = (
        (
            await db_session.execute(
                select(Order).where(
                    Order.decision_action_id == closing_action_id,
                    Order.order_kind == "close",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(close_rows) == 1
    close_row = close_rows[0]
    assert close_row.hl_order_id == close_order.hl_order_id
    assert close_row.hl_order_id is not None
    assert close_row.symbol == "BTC"
    assert close_row.run_id == ids.closing_run_id

    # (b) closing_action_id populated, points to the FLAT close action (not the opening one)
    pos = await db_session.get(Position, uuid.UUID(pos_id))
    assert pos is not None
    assert pos.closing_action_id == closing_action_id
    assert pos.closing_action_id != ids.action_id

    # (a) close fee included in sum_fees_usd (0.30 entry + 0.50 close) → net PnL reflects it
    fee_total = (
        await db_session.execute(
            select(func.coalesce(func.sum(FeeEvent.fee_usd), Decimal("0"))).where(
                FeeEvent.position_id == uuid.UUID(pos_id)
            )
        )
    ).scalar_one()
    assert fee_total == Decimal("0.80")

    outcome = (
        await db_session.execute(select(Outcome).where(Outcome.position_id == uuid.UUID(pos_id)))
    ).scalar_one()
    assert outcome.sum_fees_usd == Decimal("0.80")
    # pnl_net_fee = 5.00 - 0.80 = 4.20
    assert outcome.pnl_net_fee_usd == Decimal("4.20")


# ---------------------------------------------------------------------------
# ADR-0030: autonomous closures (SL/TP trigger, liquidation) — no model action,
# no close order of ours. close_position accepts closing_action_id/close_order=None;
# the revised conditional CHECK admits NULL closing_action_id for those reasons.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("close_reason", "exit_price", "realized_pnl"),
    [
        (CloseReason.STOP_LOSS, Decimal("95.00"), Decimal("-5.00")),
        (CloseReason.TAKE_PROFIT, Decimal("105.00"), Decimal("5.00")),
        (CloseReason.LIQUIDATED, Decimal("90.00"), Decimal("-10.00")),
    ],
)
async def test_close_position_autonomous_persists_without_action(
    db_session: AsyncSession,
    close_reason: CloseReason,
    exit_price: Decimal,
    realized_pnl: Decimal,
) -> None:
    """ADR-0030: an autonomous closure has no model action and no close order of ours.

    close_position must accept closing_action_id=None + close_order=None: it persists the
    closing fields with closing_action_id NULL (the revised conditional CHECK admits NULL
    for stop_loss/take_profit/liquidated), creates the Outcome, and writes NO orders row of
    kind 'close' (the SL/TP trigger rows already exist from the open, left
    status='triggered' — reconciliation deferred, ADR-0025).
    """
    from sqlalchemy import select

    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)
    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(fee_usd=Decimal("0.30")),
        run_id=str(ids.opening_run_id),
    )

    closure = PositionClosureInfo(
        closed_at="2026-01-15T13:00:00+00:00",
        exit_price=exit_price,
        close_reason=close_reason,
        realized_pnl_usd=realized_pnl,
    )
    # Autonomous path: no closing action, no close order.
    await repo.close_position(
        position_id=pos_id,
        closure=closure,
        closing_run_id=str(ids.closing_run_id),
        closing_action_id=None,
        close_order=None,
    )

    pos = await db_session.get(Position, uuid.UUID(pos_id))
    assert pos is not None
    assert pos.close_reason == close_reason.value
    assert pos.closing_action_id is None  # conditional CHECK admits NULL for these reasons
    assert pos.closed_at is not None
    assert pos.exit_price == exit_price

    # No 'close' orders row created; only the original entry/stop_loss/take_profit remain.
    order_kinds = (
        (
            await db_session.execute(
                select(Order.order_kind).where(Order.decision_action_id == ids.action_id)
            )
        )
        .scalars()
        .all()
    )
    assert sorted(order_kinds) == ["entry", "stop_loss", "take_profit"]

    # Outcome created; only the entry fee counts (no close fee reconciled on this path).
    outcome = (
        await db_session.execute(select(Outcome).where(Outcome.position_id == uuid.UUID(pos_id)))
    ).scalar_one()
    assert outcome.realized_pnl_gross_usd == realized_pnl
    assert outcome.sum_fees_usd == Decimal("0.30")


@pytest.mark.asyncio
async def test_close_position_autonomous_rejects_model_close_without_action(
    db_session: AsyncSession,
) -> None:
    """ADR-0030: the revised conditional CHECK still REQUIRES closing_action_id for
    close_reason='model_close'.

    Reaching close_position on the autonomous path (closing_action_id=None, close_order=None)
    with a model_close reason must be rejected by chk_position_closed_consistency — a
    model_close means a model FLAT decision, which always has an action. The decision loop
    never does this; the CHECK is the backstop that keeps model_close traceable.
    """
    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)
    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(),
        run_id=str(ids.opening_run_id),
    )
    closure = PositionClosureInfo(
        closed_at="2026-01-15T13:00:00+00:00",
        exit_price=Decimal("105.00"),
        close_reason=CloseReason.MODEL_CLOSE,
        realized_pnl_usd=Decimal("5.00"),
    )
    with pytest.raises(IntegrityError):
        await repo.close_position(
            position_id=pos_id,
            closure=closure,
            closing_run_id=str(ids.closing_run_id),
            closing_action_id=None,
            close_order=None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exit_price", "expected_reason"),
    [(Decimal("95.00"), "stop_loss"), (Decimal("105.00"), "take_profit")],
)
async def test_check_pending_closures_persists_autonomous_sltp_real_repo(
    db_session: AsyncSession,
    exit_price: Decimal,
    expected_reason: str,
) -> None:
    """ADR-0030 Problema 2 regression guard: _check_pending_closures drives the REAL
    close_position 5-arg signature (no TypeError) against the REAL repository, and the
    per-side attribution lands the right close_reason end-to-end.

    The seed opens a LONG BTC at entry=100. The mock client reports the closure the way the
    real client does (model_close, non-liquidated); the loop re-attributes per-side from the
    exit price and persists an autonomous closure (closing_action_id NULL, no close order).
    """
    from sqlalchemy import select

    ids = await _seed(db_session)
    repo = PositionsRepository(db_session)
    pos_id = await repo.open_position(
        action_id=str(ids.action_id),
        order_results=_make_order_results(),
        run_id=str(ids.opening_run_id),
    )

    closure = PositionClosureInfo(
        closed_at="2026-01-15T13:00:00+00:00",
        exit_price=exit_price,
        close_reason=CloseReason.MODEL_CLOSE,  # client's default; the loop re-attributes
        realized_pnl_usd=(exit_price - Decimal("100.00")),  # LONG, size 1.0
    )
    loop = DecisionLoop(
        settings=MagicMock(model_id=ids.model_id),
        llm_client=MagicMock(),
        hl_client=MockHyperliquidClient(closed_positions={"BTC": closure}),
        session_factory=MagicMock(),
        guardrails=MagicMock(),
    )

    # Drives the real PositionsRepository(db_session).close_position via the real call-site.
    await loop._check_pending_closures(db_session, str(ids.closing_run_id))

    pos = await db_session.get(Position, uuid.UUID(pos_id))
    assert pos is not None
    assert pos.closed_at is not None
    assert pos.close_reason == expected_reason  # per-side attribution
    assert pos.closing_action_id is None  # autonomous: no model action
    assert pos.exit_price == exit_price

    close_rows = (
        (
            await db_session.execute(
                select(Order).where(
                    Order.decision_action_id == ids.action_id,
                    Order.order_kind == "close",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(close_rows) == 0  # no close order row on the autonomous path

    outcome = (
        await db_session.execute(select(Outcome).where(Outcome.position_id == uuid.UUID(pos_id)))
    ).scalar_one()
    assert outcome is not None
