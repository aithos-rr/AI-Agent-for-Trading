"""RepositorySpy for invariant #1: cross-model isolation (PRD §9.5, §5 inv #1).

Wraps an AsyncSession and intercepts flush/commit events to detect rows that
have a model_id field inconsistent with the expected model_id.  Raises
LeakDetected on the first detected violation.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession


class LeakDetected(Exception):
    """Raised when a cross-model data access is detected by RepositorySpy."""


class RepositorySpy:
    """Context manager that instruments a session to detect cross-model row access.

    Usage::

        spy = RepositorySpy(session, expected_model_id="openai-gpt4o")
        with spy:
            await repo.some_query()
        # raises LeakDetected if any row returned or flushed has wrong model_id
    """

    def __init__(self, session: AsyncSession, expected_model_id: str) -> None:
        self._session = session
        self._expected_model_id = expected_model_id
        self._violations: list[str] = []

    def __enter__(self) -> RepositorySpy:
        self._install_listeners()
        return self

    def __exit__(self, *args: object) -> None:
        self._remove_listeners()
        if self._violations:
            raise LeakDetected(
                f"model_id isolation violation: expected '{self._expected_model_id}', "
                f"got {self._violations}"
            )

    def _install_listeners(self) -> None:
        sync_session = self._session.sync_session
        event.listen(sync_session, "after_bulk_delete", self._on_bulk_delete)
        event.listen(sync_session, "before_flush", self._on_before_flush)
        event.listen(sync_session, "after_flush", self._on_after_flush)
        # READ-path teeth (PRD §9.5 lines 2481-2483): every ORM instance loaded
        # from a SELECT result is checked. A missing `WHERE model_id` filter on an
        # agent read would surface a foreign-model row here, raising LeakDetected.
        event.listen(sync_session, "loaded_as_persistent", self._on_loaded)

    def _remove_listeners(self) -> None:
        sync_session = self._session.sync_session
        if event.contains(sync_session, "after_bulk_delete", self._on_bulk_delete):
            event.remove(sync_session, "after_bulk_delete", self._on_bulk_delete)
        if event.contains(sync_session, "before_flush", self._on_before_flush):
            event.remove(sync_session, "before_flush", self._on_before_flush)
        if event.contains(sync_session, "after_flush", self._on_after_flush):
            event.remove(sync_session, "after_flush", self._on_after_flush)
        if event.contains(sync_session, "loaded_as_persistent", self._on_loaded):
            event.remove(sync_session, "loaded_as_persistent", self._on_loaded)

    def _check_instance(self, obj: object) -> None:
        model_id = getattr(obj, "model_id", None)
        if model_id is None:
            return
        if isinstance(model_id, str) and model_id != self._expected_model_id:
            self._violations.append(model_id)

    def _on_before_flush(self, session: object, flush_context: object, instances: object) -> None:
        sync = session  # type: ignore[assignment]
        for obj in list(getattr(sync, "new", [])) + list(getattr(sync, "dirty", [])):  # type: ignore[attr-defined]
            self._check_instance(obj)

    def _on_after_flush(self, session: object, flush_context: object) -> None:
        pass  # violations already caught in before_flush

    def _on_loaded(self, session: object, instance: object) -> None:
        # Fires once per ORM instance materialized from a SELECT result.
        self._check_instance(instance)

    def _on_bulk_delete(self, delete_context: object) -> None:
        pass  # bulk deletes don't carry model_id directly; skip
