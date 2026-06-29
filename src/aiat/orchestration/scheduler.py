"""APScheduler configuration for AIAT services (PRD §4.1).

Two factory functions build the AsyncIOScheduler for each service role:
  build_scheduler_for_orchestrator — CronTrigger at minutes 0/15/30/45, second=0
  build_scheduler_for_agent        — CronTrigger at minutes 0/15/30/45 + start_delay (second offset)

Job defaults are invariant (PRD §4.1, fix 12):
  coalesce=True, max_instances=1, misfire_grace_time=60
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from aiat.config.settings import AgentSettings, ContextOrchestratorSettings

_CRON_MINUTE = "0,15,30,45"
_TICK_MINUTES = 15

_JOB_DEFAULTS: dict[str, Any] = {
    "coalesce": True,
    "max_instances": 1,
    "misfire_grace_time": 60,
}


def current_tick() -> tuple[str, datetime]:
    """Return ``(tick_id, scheduled_for)`` for the current 15-minute tick boundary.

    APScheduler fires the tick jobs with no arguments, so each job derives its own
    tick from wall-clock time. Both the context-orchestrator (fires at second 0) and
    the agents (fire at second ``agent_start_delay_seconds``, default 30, of the SAME
    minute) floor ``now()`` to the same 15-minute boundary, so they agree on
    ``tick_id`` — the precondition for invariant #13 (agents read the snapshot the
    orchestrator wrote for that tick). ``tick_id`` is the boundary's ISO timestamp;
    ``scheduled_for`` is the same instant as a ``datetime``.
    """
    now = datetime.now(UTC)
    floored = now.replace(
        minute=(now.minute // _TICK_MINUTES) * _TICK_MINUTES,
        second=0,
        microsecond=0,
    )
    return floored.isoformat(), floored


async def _unbound_orchestrator_tick() -> None:
    raise RuntimeError("tick_job not bound — pass a callable to build_scheduler_for_orchestrator")


async def _unbound_agent_tick() -> None:
    raise RuntimeError("tick_job not bound — pass a callable to build_scheduler_for_agent")


async def build_scheduler_for_orchestrator(
    settings: ContextOrchestratorSettings,
    tick_job: Callable[..., Any] | None = None,
) -> AsyncIOScheduler:
    """Build the AsyncIOScheduler for the context-orchestrator service.

    Fires tick_job at minutes 0/15/30/45 UTC (second=0).

    Args:
        settings: ContextOrchestratorSettings for this service.
        tick_job: Callable invoked each tick. Defaults to an unbound placeholder
            (must be replaced by __main__.py when starting the real service).
    """
    actual_job: Callable[..., Any] = (
        tick_job if tick_job is not None else _unbound_orchestrator_tick
    )
    scheduler = AsyncIOScheduler(job_defaults=_JOB_DEFAULTS)
    scheduler.add_job(
        actual_job,
        trigger=CronTrigger(minute=_CRON_MINUTE, second=0),
        id="orchestrator_tick",
    )
    return scheduler


async def build_scheduler_for_agent(
    settings: AgentSettings,
    tick_job: Callable[..., Any] | None = None,
) -> AsyncIOScheduler:
    """Build the AsyncIOScheduler for an agent service.

    Fires tick_job at minutes 0/15/30/45 UTC plus agent_start_delay_seconds
    (second offset, default 30s) so agents trigger after the context-orchestrator
    has materialised the context snapshot.

    Args:
        settings: AgentSettings for this agent service.
        tick_job: Callable invoked each tick. Defaults to an unbound placeholder.
    """
    actual_job: Callable[..., Any] = tick_job if tick_job is not None else _unbound_agent_tick
    scheduler = AsyncIOScheduler(job_defaults=_JOB_DEFAULTS)
    scheduler.add_job(
        actual_job,
        trigger=CronTrigger(minute=_CRON_MINUTE, second=settings.agent_start_delay_seconds),
        id="agent_tick",
    )
    return scheduler
