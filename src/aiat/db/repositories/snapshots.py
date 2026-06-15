"""Repository for account_snapshots + context_snapshots (§7.6)."""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.account_snapshot import AccountSnapshot
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.run import Run
from aiat.domain.schemas import PortfolioState


class SnapshotsRepository:
    """account_snapshots (with portfolio_state_hash) + context_snapshots (§7.6).

    No internal commit — caller owns the Unit of Work (AsyncSession).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def persist_account_snapshot(
        self,
        run_id: str,
        portfolio_state: PortfolioState,
    ) -> str:
        """Persist an account_snapshots row derived from a PortfolioState.

        Fetches the Run to populate experiment_id, model_id, and snapshot_at.
        Computes portfolio_state_hash as SHA-256 of the canonical JSON of the portfolio.
        Computes total_position_value_usd as sum of (current_price × size_units).

        Args:
            run_id: UUID string of the owning run.
            portfolio_state: PortfolioState DTO from the agent wallet snapshot.

        Returns:
            account_snapshot_id (str UUID) of the newly persisted row.

        Raises:
            ValueError: if run_id does not exist in the database.
            IntegrityError: on FK or CHECK violation.
        """
        run_uuid = uuid.UUID(run_id)
        run = await self._session.get(Run, run_uuid)
        if run is None:
            raise ValueError(f"Run {run_id!r} not found")

        state_hash = hashlib.sha256(portfolio_state.model_dump_json().encode()).hexdigest()

        total_position_value_usd = sum(
            p.current_price * p.size_units for p in portfolio_state.open_positions
        )
        raw: dict[str, Any] = portfolio_state.model_dump(mode="json")

        snapshot = AccountSnapshot(
            id=uuid.uuid4(),
            run_id=run_uuid,
            experiment_id=run.experiment_id,
            model_id=run.model_id,
            snapshot_at=datetime.now(UTC),
            equity_usd=portfolio_state.equity_usd,
            available_usd=portfolio_state.available_usd,
            margin_used_usd=portfolio_state.margin_used_usd,
            n_open_positions=portfolio_state.n_open_positions,
            total_position_value_usd=total_position_value_usd,
            unrealized_pnl_usd=portfolio_state.unrealized_pnl_usd,
            portfolio_state_hash=state_hash,
            raw_account_state=raw,
        )
        self._session.add(snapshot)
        await self._session.flush()
        return str(snapshot.id)

    async def get_context_snapshot(
        self,
        experiment_id: str,
        tick_id: str,
    ) -> ContextSnapshot | None:
        """Return the ContextSnapshot for (experiment_id, tick_id), or None if absent.

        Args:
            experiment_id: UUID string of the experiment.
            tick_id: Tick identifier string (e.g. ISO timestamp).

        Returns:
            ContextSnapshot ORM instance or None.
        """
        result = await self._session.execute(
            select(ContextSnapshot).where(
                ContextSnapshot.experiment_id == uuid.UUID(experiment_id),
                ContextSnapshot.tick_id == tick_id,
            )
        )
        return result.scalar_one_or_none()
