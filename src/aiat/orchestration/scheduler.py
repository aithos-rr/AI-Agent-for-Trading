"""APScheduler configuration for AIAT services (PRD §4.1).

Two factory functions build the AsyncIOScheduler for each service role:
  build_scheduler_for_orchestrator — CronTrigger at minutes 0/15/30/45, second=0
  build_scheduler_for_agent        — CronTrigger at minutes 0/15/30/45 + start_delay (second offset)

Job defaults are invariant (PRD §4.1, fix 12):
  coalesce=True, max_instances=1, misfire_grace_time=60
"""

from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from aiat.config.settings import AgentSettings, ContextOrchestratorSettings

_CRON_MINUTE = "0,15,30,45"

_JOB_DEFAULTS: dict[str, Any] = {
    "coalesce": True,
    "max_instances": 1,
    "misfire_grace_time": 60,
}


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
