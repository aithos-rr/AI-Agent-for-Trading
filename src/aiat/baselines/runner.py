"""DB glue for the baseline equity computation (ADR-0036).

Shared by the live orchestrator step (``__main__._orchestrator_tick`` after the snapshot is
persisted) and ``scripts/compute_baselines.py`` (catch-up/backfill). The pure per-tick strategy
math lives in :mod:`aiat.baselines.compute`; this module only extracts market data from a
``ContextBundle``, carries per-baseline state, and persists idempotently.

Idempotency: one row per ``(experiment_id, baseline_name, tick_id)`` (UNIQUE). A tick that
already has a snapshot is a no-op — its stored ``raw_state`` is carried forward so the sequence
stays correct on re-run / resume. Prices come only from the tick's context snapshot (no HL calls).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiat.baselines.compute import (
    BASELINE_NAMES,
    SYMBOLS,
    SymbolTick,
    TickMarket,
    compute_baseline,
)
from aiat.db.repositories.baselines import BaselineRepository
from aiat.domain.schemas import ContextBundle

logger = structlog.get_logger(__name__)

_QUANT = Decimal("0.00000001")  # baseline_equity_snapshots.equity_usd is Numeric(20, 8)

# Per-tick action outcomes.
WRITE = "WRITE"
SKIP_EXISTS = "SKIP_EXISTS"  # snapshot already present (idempotent) — state carried, no write
SKIP_NO_CONFIG = "SKIP_NO_CONFIG"  # baseline_configs row missing (unseeded) — cannot persist


@dataclass
class BaselineTickPlan:
    """The planned/performed action for one baseline at one tick (dry-run display + carry)."""

    baseline_name: str
    action: str
    equity_usd: Decimal | None
    pnl_usd_cumulative: Decimal | None
    new_state: dict[str, Any] | None  # state to carry into the next tick


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def bundle_to_market(bundle: ContextBundle) -> TickMarket:
    """Extract per-symbol (price, ema20, ema50, funding_rate_8h) from a context snapshot bundle."""
    tech = {t.symbol: t for t in bundle.technical}
    onchain = {o.symbol: o for o in bundle.onchain}
    market: TickMarket = {}
    for s in SYMBOLS:
        if s not in tech or s not in onchain:
            raise ValueError(f"context bundle missing technical/onchain data for {s}")
        market[s] = SymbolTick(
            price=tech[s].price_usd,
            ema20=tech[s].ema_20,
            ema50=tech[s].ema_50,
            funding_rate_8h=onchain[s].funding_rate_8h,
        )
    return market


class BaselineRunner:
    """Compute + persist the 3 baseline equity snapshots for a tick (live and backfill)."""

    def __init__(self, experiment_id: str) -> None:
        self._experiment_id = experiment_id

    async def load_prev_states(
        self, session: AsyncSession, before_tick_at: datetime
    ) -> dict[str, dict[str, Any] | None]:
        """Seed each baseline's carry-state from its latest snapshot before ``before_tick_at``."""
        repo = BaselineRepository(session)
        out: dict[str, dict[str, Any] | None] = {}
        for name in BASELINE_NAMES:
            snap = await repo.get_latest_equity_snapshot_before(
                self._experiment_id, name, before_tick_at
            )
            out[name] = snap.raw_state if snap is not None else None
        return out

    async def process_tick(
        self,
        session: AsyncSession,
        bundle: ContextBundle,
        prev_states: dict[str, dict[str, Any] | None],
        *,
        apply: bool,
    ) -> list[BaselineTickPlan]:
        """Plan (and, if ``apply``, persist) the 3 baseline snapshots for one tick.

        Args:
            session: caller-owned session (caller commits).
            bundle: the tick's context bundle (source of prices/EMAs/funding).
            prev_states: per-baseline carry-state from the previous processed tick.
            apply: when True, persist the WRITE rows; when False, compute only (dry-run).

        Returns:
            One BaselineTickPlan per baseline; ``new_state`` is what the caller carries forward.
        """
        repo = BaselineRepository(session)
        market = bundle_to_market(bundle)
        plans: list[BaselineTickPlan] = []
        for name in BASELINE_NAMES:
            config = await repo.get_baseline_config(self._experiment_id, name)
            if config is None:
                plans.append(
                    BaselineTickPlan(name, SKIP_NO_CONFIG, None, None, prev_states.get(name))
                )
                continue
            existing = await repo.get_equity_snapshot(self._experiment_id, name, bundle.tick_id)
            if existing is not None:
                # Idempotent no-op: carry the already-persisted state so the sequence stays exact.
                plans.append(
                    BaselineTickPlan(
                        name,
                        SKIP_EXISTS,
                        existing.equity_usd,
                        existing.pnl_usd_cumulative,
                        existing.raw_state,
                    )
                )
                continue

            result = compute_baseline(name, prev_states.get(name), market)
            equity = result.equity_usd.quantize(_QUANT, rounding=ROUND_HALF_UP)
            pnl = result.pnl_usd_cumulative.quantize(_QUANT, rounding=ROUND_HALF_UP)
            if apply:
                await repo.persist_equity_snapshot(
                    baseline_config_id=str(config.id),
                    tick_id=bundle.tick_id,
                    tick_at=bundle.tick_at,
                    equity_usd=equity,
                    pnl_usd_cumulative=pnl,
                    raw_state=result.raw_state,
                )
            plans.append(BaselineTickPlan(name, WRITE, equity, pnl, result.raw_state))
        return plans

    async def run_live_tick(
        self, session_factory: async_sessionmaker[AsyncSession], bundle: ContextBundle
    ) -> list[BaselineTickPlan]:
        """Live per-tick entrypoint: seed state from DB, compute+persist this tick, commit."""
        tick_at = _parse_dt(bundle.tick_at)
        async with session_factory() as session:
            prev = await self.load_prev_states(session, tick_at)
            plans = await self.process_tick(session, bundle, prev, apply=True)
            await session.commit()
        written = [p.baseline_name for p in plans if p.action == WRITE]
        logger.info(
            "baseline_snapshots_persisted",
            tick_id=bundle.tick_id,
            experiment_id=self._experiment_id,
            written=written,
            skipped=[p.baseline_name for p in plans if p.action != WRITE],
        )
        return plans
