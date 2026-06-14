"""ContextOrchestrator — 5th Railway service entrypoint (PRD §7.1)."""

from __future__ import annotations

import asyncio
import time

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiat.context.builder import ContextBuilder
from aiat.db.repositories.context_build import ContextBuildRepository
from aiat.domain.exceptions import ContextBuildError
from aiat.domain.schemas import ContextBundle

logger = structlog.get_logger(__name__)


class ContextOrchestrator:
    """Materialises ONE context_snapshot per 15-minute tick (PRD §7.1).

    Owns the Unit of Work for context_snapshots + context_build_runs.
    Always commits a context_build_runs row regardless of outcome.
    """

    def __init__(
        self,
        builder: ContextBuilder,
        session_factory: async_sessionmaker[AsyncSession],
        hard_timeout_seconds: float = 30.0,
    ) -> None:
        self._builder = builder
        self._session_factory = session_factory
        self._hard_timeout_seconds = hard_timeout_seconds

    async def build_tick_context(
        self,
        tick_id: str,
        tick_at: str,
        experiment_id: str,
    ) -> ContextBundle:
        """Fetch all context sources and persist one snapshot atomically.

        Args:
            tick_id: Unique tick identifier (ISO timestamp string).
            tick_at: ISO 8601 timestamp with timezone, e.g. "2026-06-14T14:30:00+00:00".
            experiment_id: UUID string of the running experiment.

        Returns:
            The assembled ContextBundle (market context byte-identical cross-model, inv #13).

        Raises:
            ContextBuildError: timeout, source unavailable, or persist failure.
        """
        build_start = time.monotonic()

        async with self._session_factory() as session:
            repo = ContextBuildRepository(session)
            build_run_id = await repo.start_build(experiment_id, tick_id, tick_at)

            bundle: ContextBundle
            try:
                bundle = await asyncio.wait_for(
                    self._builder.build(tick_id, tick_at),
                    timeout=self._hard_timeout_seconds,
                )
            except TimeoutError:
                await repo.fail_build(
                    build_run_id,
                    failure_stage="builder",
                    error_context={"reason": "hard_timeout"},
                    status="timeout",
                )
                await session.commit()
                raise ContextBuildError(
                    f"build_tick_context timed out after {self._hard_timeout_seconds}s"
                ) from None
            except ContextBuildError as exc:
                await repo.fail_build(
                    build_run_id,
                    failure_stage="builder",
                    error_context={"error": str(exc)},
                    status="failed",
                )
                await session.commit()
                raise
            except Exception as exc:
                await repo.fail_build(
                    build_run_id,
                    failure_stage="builder",
                    error_context={"error": str(exc)},
                    status="failed",
                )
                await session.commit()
                raise ContextBuildError(str(exc)) from exc

            build_duration_ms = int((time.monotonic() - build_start) * 1000)

            await repo.complete_build(
                build_run_id,
                status="success",
                context_bundle=bundle,
                build_duration_ms=build_duration_ms,
            )
            await session.commit()

        logger.info(
            "context_snapshot_persisted",
            tick_id=tick_id,
            experiment_id=experiment_id,
            build_duration_ms=build_duration_ms,
        )
        return bundle
