"""Entrypoint dispatcher — reads AIAT_SERVICE_ROLE and starts the correct service (PRD §11.2)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from aiat.config.settings import AgentSettings, ContextOrchestratorSettings, load_settings
from aiat.context.builder import ContextBuilder
from aiat.context.collectors.news import NewsCollector
from aiat.context.collectors.onchain import _HL_TESTNET_URL, HLPublicInfoClient, OnchainCollector
from aiat.context.collectors.sentiment import SentimentCollector
from aiat.context.collectors.technical import TechnicalCollector
from aiat.db.session import get_db_session
from aiat.execution.hyperliquid_client import build_hl_client
from aiat.llm.factory import load_llm
from aiat.observability.logging_config import configure_logging as _configure_logging_impl
from aiat.orchestration.context_orchestrator import ContextOrchestrator
from aiat.orchestration.decision_loop import DecisionLoop
from aiat.orchestration.funding_reconciler import FundingReconciler
from aiat.orchestration.lifecycle import startup_checks
from aiat.orchestration.scheduler import (
    build_scheduler_for_agent,
    build_scheduler_for_orchestrator,
    current_tick,
)

logger = structlog.get_logger(__name__)


def configure_logging(settings: AgentSettings | ContextOrchestratorSettings) -> None:
    """Configure structlog JSON renderer for the service role (delegates to logging_config)."""
    _configure_logging_impl(settings.log_level)


async def _build_agent_tick_job(
    settings: AgentSettings,
) -> Callable[..., Any]:
    """Build the per-tick callable for an agent service.

    Returns:
        A zero-argument coroutine for APScheduler. APScheduler fires jobs with no
        args, so the closure derives (tick_id, scheduled_for) from ``current_tick``
        — the same 15-min boundary the orchestrator uses (inv #13) — and invokes
        ``DecisionLoop.run_once``.
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

    async def _agent_tick() -> None:
        tick_id, scheduled_for = current_tick()
        await loop.run_once(tick_id, scheduled_for)

    return _agent_tick


async def _build_orchestrator_tick_job(
    settings: ContextOrchestratorSettings,
) -> Callable[..., Any]:
    """Build the per-tick callable for the context-orchestrator service.

    Assembles the 6 collectors (all on ``settings.network`` = testnet, ADR-0019),
    the ContextBuilder and the ContextOrchestrator, and returns a zero-argument
    coroutine for APScheduler that materialises one context_snapshot per tick.
    """
    session_factory = get_db_session(settings.database_url.get_secret_value())
    http_client = httpx.AsyncClient()

    # ADR-0019: every market collector reads from settings.network (testnet, inv #9).
    # TechnicalCollector's default base_url is mainnet — the production wiring MUST
    # pass the testnet URL explicitly so technical and on-chain share one network.
    builder = ContextBuilder(
        technical_btc=TechnicalCollector("BTC", http_client, base_url=_HL_TESTNET_URL),
        technical_eth=TechnicalCollector("ETH", http_client, base_url=_HL_TESTNET_URL),
        technical_sol=TechnicalCollector("SOL", http_client, base_url=_HL_TESTNET_URL),
        sentiment=SentimentCollector(http_client),
        news=NewsCollector(http_client),
        onchain=OnchainCollector(HLPublicInfoClient(network=settings.network)),
    )
    orchestrator = ContextOrchestrator(
        builder,
        session_factory,
        hard_timeout_seconds=float(settings.hard_timeout_seconds),
    )

    async def _orchestrator_tick() -> None:
        tick_id, scheduled_for = current_tick()
        await orchestrator.build_tick_context(
            tick_id, scheduled_for.isoformat(), settings.experiment_id
        )

    return _orchestrator_tick


async def _build_funding_job(
    settings: ContextOrchestratorSettings,
) -> Callable[..., Any]:
    """Build the 8-hourly funding-ledger reconcile job (finding B / ADR-0031).

    Reads each open-position wallet's realized funding from the public HL ``userFunding``
    endpoint (read-only, no private key) and writes missing ``funding_events`` rows.
    """
    session_factory = get_db_session(settings.database_url.get_secret_value())
    reconciler = FundingReconciler(
        session_factory=session_factory,
        funding_source=HLPublicInfoClient(network=settings.network),
        experiment_id=settings.experiment_id,
    )

    async def _funding_tick() -> None:
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        await reconciler.reconcile(now_ms)

    return _funding_tick


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
        orchestrator_job = await _build_orchestrator_tick_job(settings)
        funding_job = await _build_funding_job(settings)
        scheduler = await build_scheduler_for_orchestrator(
            settings, tick_job=orchestrator_job, funding_job=funding_job
        )

    scheduler.start()
    log.info("scheduler_started", service_role=settings.service_role)
    await _run_forever()


def main() -> None:
    """Entrypoint: blocks until process exits."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()
