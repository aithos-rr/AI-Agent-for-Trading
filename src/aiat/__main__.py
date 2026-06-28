"""Entrypoint dispatcher — reads AIAT_SERVICE_ROLE and starts the correct service (PRD §11.2)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import structlog

from aiat.config.settings import AgentSettings, ContextOrchestratorSettings, load_settings
from aiat.db.session import get_db_session
from aiat.execution.hyperliquid_client import build_hl_client
from aiat.llm.factory import load_llm
from aiat.observability.logging_config import configure_logging as _configure_logging_impl
from aiat.orchestration.decision_loop import DecisionLoop
from aiat.orchestration.lifecycle import startup_checks
from aiat.orchestration.scheduler import build_scheduler_for_agent, build_scheduler_for_orchestrator

logger = structlog.get_logger(__name__)


def configure_logging(settings: AgentSettings | ContextOrchestratorSettings) -> None:
    """Configure structlog JSON renderer for the service role (delegates to logging_config)."""
    _configure_logging_impl(settings.log_level)


async def _build_agent_tick_job(
    settings: AgentSettings,
) -> Callable[..., Any]:
    """Build the per-tick callable for an agent service.

    Returns:
        The bound `DecisionLoop.run_once` method ready for APScheduler.
    """
    session_factory = get_db_session(settings.database_url.get_secret_value())
    llm_client = load_llm(settings)
    hl_client = build_hl_client(settings)
    loop = DecisionLoop(
        settings=settings,
        llm_client=llm_client,
        hl_client=hl_client,
        session_factory=session_factory,
    )
    return loop.run_once


async def _run_forever() -> None:
    """Block until the process is killed (SIGTERM/SIGINT handled by asyncio)."""
    await asyncio.Event().wait()


async def _main() -> None:
    """Full startup sequence: settings → logging → checks → scheduler → run."""
    settings = load_settings()
    configure_logging(settings)
    log = structlog.get_logger()
    log.info("startup", service_role=settings.service_role)

    await startup_checks(settings)

    if isinstance(settings, AgentSettings):
        tick_job = await _build_agent_tick_job(settings)
        scheduler = await build_scheduler_for_agent(settings, tick_job=tick_job)
    else:
        scheduler = await build_scheduler_for_orchestrator(settings)

    scheduler.start()
    log.info("scheduler_started", service_role=settings.service_role)
    await _run_forever()


def main() -> None:
    """Entrypoint: blocks until process exits."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()
