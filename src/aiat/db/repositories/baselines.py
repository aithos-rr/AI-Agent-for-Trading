"""Repository for baseline_configs and baseline_equity_snapshots (§7.6)."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.baseline_config import BaselineConfig
from aiat.db.models.baseline_equity_snapshot import BaselineEquitySnapshot


class BaselineRepository:
    """Bounded context: baseline_configs + baseline_equity_snapshots (§7.6).

    Configs are pre-registered at seed time. Equity snapshots are written LIVE each tick by the
    context-orchestrator (ADR-0036, via aiat.baselines.runner.BaselineRunner) and can be
    backfilled/caught-up from historical context snapshots by scripts/compute_baselines.py.

    No internal commit — caller owns the Unit of Work (AsyncSession).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def register_baseline_config(
        self,
        experiment_id: str,
        baseline_name: str,
        config_json: dict[str, Any],
    ) -> str:
        """Insert a baseline_configs row with a canonical SHA-256 config_hash.

        Args:
            experiment_id: UUID string of the experiment.
            baseline_name: One of 'buy_and_hold', 'cash', 'naive_momentum_ema_20_50'.
            config_json: Arbitrary JSON-serialisable config dict.

        Returns:
            baseline_config_id (str UUID) of the newly created row.

        Raises:
            IntegrityError: on UNIQUE (experiment_id, baseline_name) or invalid name.
        """
        canonical = json.dumps(config_json, sort_keys=True, ensure_ascii=True)
        config_hash = hashlib.sha256(canonical.encode()).hexdigest()

        config = BaselineConfig(
            id=uuid.uuid4(),
            experiment_id=uuid.UUID(experiment_id),
            baseline_name=baseline_name,
            config_json=config_json,
            config_hash=config_hash,
        )
        self._session.add(config)
        await self._session.flush()
        return str(config.id)

    async def get_baseline_config(
        self,
        experiment_id: str,
        baseline_name: str,
    ) -> BaselineConfig | None:
        """Return the BaselineConfig for (experiment_id, baseline_name), or None.

        Args:
            experiment_id: UUID string of the experiment.
            baseline_name: Baseline strategy name.

        Returns:
            BaselineConfig row, or None if not found.
        """
        result = await self._session.execute(
            select(BaselineConfig).where(
                BaselineConfig.experiment_id == uuid.UUID(experiment_id),
                BaselineConfig.baseline_name == baseline_name,
            )
        )
        return result.scalar_one_or_none()

    async def persist_equity_snapshot(
        self,
        baseline_config_id: str,
        tick_id: str,
        tick_at: str,
        equity_usd: Decimal,
        pnl_usd_cumulative: Decimal,
        raw_state: dict[str, Any],
    ) -> str:
        """Insert a baseline_equity_snapshots row.

        Looks up the BaselineConfig to derive experiment_id and baseline_name.

        Args:
            baseline_config_id: UUID string of the parent BaselineConfig.
            tick_id: Tick identifier (shared with context_snapshots.tick_id).
            tick_at: ISO 8601 tick timestamp string.
            equity_usd: Current equity value in USD (≥ 0).
            pnl_usd_cumulative: Cumulative signed PnL in USD.
            raw_state: Arbitrary JSON audit state.

        Returns:
            snapshot_id (str UUID) of the newly created row.

        Raises:
            ValueError: if the baseline_config_id is not found in the session.
            IntegrityError: on UNIQUE (experiment_id, baseline_name, tick_id) violation.
        """
        bc_result = await self._session.execute(
            select(BaselineConfig).where(BaselineConfig.id == uuid.UUID(baseline_config_id))
        )
        bc = bc_result.scalar_one_or_none()
        if bc is None:
            raise ValueError(f"BaselineConfig {baseline_config_id!r} not found")

        tick_dt = datetime.fromisoformat(tick_at)
        if tick_dt.tzinfo is None:
            tick_dt = tick_dt.replace(tzinfo=UTC)

        snap = BaselineEquitySnapshot(
            id=uuid.uuid4(),
            experiment_id=bc.experiment_id,
            baseline_config_id=uuid.UUID(baseline_config_id),
            baseline_name=bc.baseline_name,
            tick_id=tick_id,
            tick_at=tick_dt,
            equity_usd=equity_usd,
            pnl_usd_cumulative=pnl_usd_cumulative,
            raw_state=raw_state,
        )
        self._session.add(snap)
        await self._session.flush()
        return str(snap.id)

    async def list_equity_history(
        self,
        experiment_id: str,
        baseline_name: str,
    ) -> list[BaselineEquitySnapshot]:
        """Return equity snapshots for a baseline ordered by tick_at ascending.

        Args:
            experiment_id: UUID string of the experiment.
            baseline_name: Baseline strategy name.

        Returns:
            List of BaselineEquitySnapshot ordered by tick_at ascending.
        """
        result = await self._session.execute(
            select(BaselineEquitySnapshot)
            .where(
                BaselineEquitySnapshot.experiment_id == uuid.UUID(experiment_id),
                BaselineEquitySnapshot.baseline_name == baseline_name,
            )
            .order_by(BaselineEquitySnapshot.tick_at.asc())
        )
        return list(result.scalars().all())

    async def get_equity_snapshot(
        self,
        experiment_id: str,
        baseline_name: str,
        tick_id: str,
    ) -> BaselineEquitySnapshot | None:
        """Return the snapshot for (experiment, baseline, tick_id), or None (idempotency guard)."""
        result = await self._session.execute(
            select(BaselineEquitySnapshot).where(
                BaselineEquitySnapshot.experiment_id == uuid.UUID(experiment_id),
                BaselineEquitySnapshot.baseline_name == baseline_name,
                BaselineEquitySnapshot.tick_id == tick_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_latest_equity_snapshot_before(
        self,
        experiment_id: str,
        baseline_name: str,
        before_tick_at: datetime,
    ) -> BaselineEquitySnapshot | None:
        """Return the most recent snapshot strictly before ``before_tick_at`` (state to carry)."""
        result = await self._session.execute(
            select(BaselineEquitySnapshot)
            .where(
                BaselineEquitySnapshot.experiment_id == uuid.UUID(experiment_id),
                BaselineEquitySnapshot.baseline_name == baseline_name,
                BaselineEquitySnapshot.tick_at < before_tick_at,
            )
            .order_by(BaselineEquitySnapshot.tick_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
