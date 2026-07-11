"""E2E: DecisionLoop._reconcile_chain_state logs ChainDivergence + proceeds (ADR-0025).

Drives the loop's reconciliation step against a REAL Postgres. TRIPWIRE: pre-fix there was
NO DB↔chain comparison, so a position closed on-chain but still OPEN in the DB left no trace
(finding from smoke M6). These tests assert a ChainDivergence errors row IS written when they
diverge (and NONE when in sync). Reuses the funding e2e commit-safe seed for an open position.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiat.db.models.error import Error
from aiat.domain.schemas import OpenPositionSummary, PortfolioState
from aiat.orchestration.decision_loop import DecisionLoop
from tests.e2e.test_funding_reconciler import _seed_open_btc_position

_EMPTY_PORTFOLIO = PortfolioState(
    equity_usd=Decimal("10000"),
    available_usd=Decimal("10000"),
    margin_used_usd=Decimal("0"),
    n_open_positions=0,
    unrealized_pnl_usd=Decimal("0"),
    open_positions=[],
)


@pytest_asyncio.fixture(scope="function")
async def session_factory(db_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


def _loop(session_factory: async_sessionmaker[AsyncSession], seed: object) -> DecisionLoop:
    # _reconcile_chain_state only reads settings.model_id / experiment_id, so a light stub
    # settings object suffices (avoids constructing a full AgentSettings for this unit of work).
    settings = SimpleNamespace(
        model_id=seed.model_id,  # type: ignore[attr-defined]
        experiment_id=str(seed.experiment_id),  # type: ignore[attr-defined]
    )
    return DecisionLoop(
        settings=settings,  # type: ignore[arg-type]
        llm_client=AsyncMock(),
        hl_client=AsyncMock(),
        session_factory=session_factory,
    )


@pytest.mark.asyncio
async def test_divergence_logs_chain_divergence_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        seed = await _seed_open_btc_position(s)  # one OPEN BTC LONG position in the DB
        await s.commit()

    loop = _loop(session_factory, seed)

    # Chain holds NOTHING → the DB's open BTC is "missing_on_chain".
    async with session_factory() as s:
        await loop._reconcile_chain_state(s, _EMPTY_PORTFOLIO)
        await s.commit()

    async with session_factory() as s:
        errors = (
            await s.scalars(
                select(Error).where(
                    Error.model_id == seed.model_id, Error.error_kind == "ChainDivergence"
                )
            )
        ).all()
        assert len(errors) == 1
        ctx = errors[0].context
        assert ctx is not None
        divs = ctx["divergences"]
        assert len(divs) == 1
        assert divs[0]["symbol"] == "BTC"
        assert divs[0]["kind"] == "missing_on_chain"
        assert str(errors[0].experiment_id) == str(seed.experiment_id)


@pytest.mark.asyncio
async def test_in_sync_writes_no_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        seed = await _seed_open_btc_position(s)
        await s.commit()

    loop = _loop(session_factory, seed)

    # Chain holds the SAME BTC LONG 1.0 as the DB → no divergence.
    in_sync = PortfolioState(
        equity_usd=Decimal("10000"),
        available_usd=Decimal("10000"),
        margin_used_usd=Decimal("0"),
        n_open_positions=1,
        unrealized_pnl_usd=Decimal("0"),
        open_positions=[
            OpenPositionSummary(
                symbol="BTC",
                side="LONG",
                entry_price=Decimal("100.00"),
                current_price=Decimal("101.00"),
                size_units=Decimal("1.0"),
                leverage=Decimal("3.00"),
                unrealized_pnl_usd=Decimal("1.0"),
                age_minutes=15,
            )
        ],
    )
    async with session_factory() as s:
        await loop._reconcile_chain_state(s, in_sync)
        await s.commit()

    async with session_factory() as s:
        # No error row written when DB and chain agree.
        errors = (await s.scalars(select(Error).where(Error.model_id == seed.model_id))).all()
        assert errors == []
