"""Repository for runs + errors lifecycle (§7.6)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.error import Error
from aiat.db.models.run import Run
from aiat.domain.enums import RunStatus


class RunsRepository:
    """Lifecycle management for runs + errors (§7.6).

    No internal commit — caller owns the Unit of Work (AsyncSession).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(
        self,
        *,
        experiment_id: str,
        model_id: str,
        tick_id: str,
        scheduled_for: datetime,
        prompt_template_hash: str,
        rendered_prompt_hash: str,
        context_snapshot_id: str,
        schema_version: str,
        git_commit_sha: str,
        rendered_prompt_text: str | None = None,
    ) -> str:
        """Create a runs row with status='running'. Returns run_id.

        Args:
            experiment_id: UUID string of the owning experiment.
            model_id: Model identifier string.
            tick_id: Tick identifier string.
            scheduled_for: Datetime the cron tick was scheduled for.
            prompt_template_hash: SHA-256 hash of the prompt template.
            rendered_prompt_hash: SHA-256 hash of the rendered prompt for this run.
            context_snapshot_id: UUID string of the context snapshot for this tick.
            schema_version: Schema version tag (e.g. "v2").
            git_commit_sha: Git commit SHA at runtime.
            rendered_prompt_text: Optional full rendered prompt text for audit.

        Returns:
            run_id (str UUID) of the newly created Run.

        Raises:
            IntegrityError: on FK or UNIQUE violation.
        """
        run = Run(
            id=uuid.uuid4(),
            experiment_id=uuid.UUID(experiment_id),
            model_id=model_id,
            tick_id=tick_id,
            scheduled_for=scheduled_for,
            run_started_at=datetime.now(UTC),
            status=RunStatus.RUNNING.value,
            prompt_template_hash=prompt_template_hash,
            rendered_prompt_hash=rendered_prompt_hash,
            rendered_prompt_text=rendered_prompt_text,
            context_snapshot_id=uuid.UUID(context_snapshot_id),
            schema_version=schema_version,
            git_commit_sha=git_commit_sha,
        )
        self._session.add(run)
        await self._session.flush()
        return str(run.id)

    async def update_status(
        self,
        run_id: str,
        status: RunStatus,
        failure_stage: str | None = None,
    ) -> None:
        """Update a run's status and optionally set failure_stage + completed_at.

        Args:
            run_id: UUID string of the run to update.
            status: New RunStatus value.
            failure_stage: Optional stage name where failure occurred.

        Raises:
            ValueError: if run_id does not exist.
        """
        run = await self._session.get(Run, uuid.UUID(run_id))
        if run is None:
            raise ValueError(f"Run {run_id!r} not found")

        run.status = status.value
        if failure_stage is not None:
            run.failure_stage = failure_stage
        if status not in (RunStatus.RUNNING,):
            run.run_completed_at = datetime.now(UTC)
        await self._session.flush()

    async def log_error(
        self,
        *,
        error_kind: str,
        error_message: str,
        run_id: str | None = None,
        experiment_id: str | None = None,
        model_id: str | None = None,
        stack_trace: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Persist an errors row with optional FK associations.

        All FK fields are nullable — errors can be logged before a run exists.

        Args:
            error_kind: Error category string (e.g. "LLMTimeoutError").
            error_message: Human-readable error message.
            run_id: Optional UUID string of the associated run.
            experiment_id: Optional UUID string of the associated experiment.
            model_id: Optional model identifier string.
            stack_trace: Optional Python traceback text.
            context: Optional JSON-serialisable dict for extra context.
        """
        error = Error(
            id=uuid.uuid4(),
            run_id=uuid.UUID(run_id) if run_id is not None else None,
            experiment_id=uuid.UUID(experiment_id) if experiment_id is not None else None,
            model_id=model_id,
            error_kind=error_kind,
            error_message=error_message,
            stack_trace=stack_trace,
            context=context,
            occurred_at=datetime.now(UTC),
        )
        self._session.add(error)
        await self._session.flush()
