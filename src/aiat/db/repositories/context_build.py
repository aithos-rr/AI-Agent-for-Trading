"""Repository for context_snapshots + context_build_runs (§7.6 fix B.5)."""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.context_build_run import ContextBuildRun
from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.domain.schemas import ContextBundle


class ContextBuildRepository:
    """Bounded context: context_snapshots + context_build_runs.

    Used exclusively by the context-orchestrator. Receives an external AsyncSession;
    no internal commit/rollback — the orchestrator owns the Unit of Work.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start_build(
        self,
        experiment_id: str,
        tick_id: str,
        tick_at: str,
    ) -> str:
        """Create a context_build_runs row with status='running'. Returns build_run_id."""
        tick_at_dt = datetime.fromisoformat(tick_at)
        if tick_at_dt.tzinfo is None:
            tick_at_dt = tick_at_dt.replace(tzinfo=UTC)

        build_run = ContextBuildRun(
            id=uuid.uuid4(),
            experiment_id=uuid.UUID(experiment_id),
            tick_id=tick_id,
            tick_at=tick_at_dt,
            started_at=datetime.now(UTC),
            status="running",
        )
        self._session.add(build_run)
        await self._session.flush()
        return str(build_run.id)

    async def complete_build(
        self,
        build_run_id: str,
        status: Literal["success", "partial"],
        context_bundle: ContextBundle,
        build_duration_ms: int,
    ) -> str:
        """Persist context_snapshot + update context_build_run. Returns context_snapshot_id.

        Both writes happen in the caller's transaction (no internal commit).
        """
        build_run = await self._session.get(ContextBuildRun, uuid.UUID(build_run_id))
        if build_run is None:
            raise ValueError(f"ContextBuildRun {build_run_id!r} not found")

        bundle_json: dict[str, Any] = context_bundle.model_dump(mode="json")
        context_hash = hashlib.sha256(context_bundle.model_dump_json().encode()).hexdigest()

        tick_at_dt = datetime.fromisoformat(context_bundle.tick_at)
        if tick_at_dt.tzinfo is None:
            tick_at_dt = tick_at_dt.replace(tzinfo=UTC)

        snapshot = ContextSnapshot(
            id=uuid.uuid4(),
            experiment_id=build_run.experiment_id,
            tick_id=context_bundle.tick_id,
            tick_at=tick_at_dt,
            context_hash=context_hash,
            context_json=bundle_json,
            source_timestamps=context_bundle.source_timestamps,
            build_duration_ms=build_duration_ms,
        )
        self._session.add(snapshot)
        await self._session.flush()

        build_run.context_snapshot_id = snapshot.id
        build_run.status = status
        build_run.completed_at = datetime.now(UTC)
        await self._session.flush()

        return str(snapshot.id)

    async def fail_build(
        self,
        build_run_id: str,
        failure_stage: str,
        error_context: dict[str, Any],
        status: Literal["failed", "timeout"] = "failed",
    ) -> None:
        """Update context_build_run to a terminal failure status without creating a snapshot."""
        build_run = await self._session.get(ContextBuildRun, uuid.UUID(build_run_id))
        if build_run is None:
            raise ValueError(f"ContextBuildRun {build_run_id!r} not found")

        build_run.status = status
        build_run.failure_stage = failure_stage
        build_run.error_context = error_context
        build_run.completed_at = datetime.now(UTC)
        await self._session.flush()

    async def get_snapshot_for_tick(
        self,
        experiment_id: str,
        tick_id: str,
    ) -> ContextSnapshot | None:
        """Read the context snapshot for a given tick. Used by agents (read-only)."""
        result = await self._session.execute(
            select(ContextSnapshot).where(
                ContextSnapshot.experiment_id == uuid.UUID(experiment_id),
                ContextSnapshot.tick_id == tick_id,
            )
        )
        return result.scalar_one_or_none()
