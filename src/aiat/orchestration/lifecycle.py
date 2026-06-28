"""Lifecycle startup checks for AIAT service roles (PRD §10.1)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.config.pricing import load_pricing_for_model
from aiat.config.settings import AgentSettings, BaseAIATSettings, ContextOrchestratorSettings
from aiat.db.models import BaselineConfig, Experiment, Model, PromptTemplate
from aiat.db.session import get_db_session

logger = structlog.get_logger(__name__)

EXPECTED_ALEMBIC_VERSION = "002"
EXPECTED_BASELINES: frozenset[str] = frozenset({"buy_and_hold", "cash", "naive_momentum_ema_20_50"})


@asynccontextmanager
async def _db_session(settings: BaseAIATSettings) -> AsyncIterator[AsyncSession]:
    """Provide a single async DB session (one unit-of-work scope)."""
    factory = get_db_session(settings.database_url.get_secret_value())
    async with factory() as session:
        yield session


async def startup_checks(settings: AgentSettings | ContextOrchestratorSettings) -> None:
    """Dispatcher: apply common checks + role-specific checks (PRD §10.1)."""
    await _check_network_testnet(settings)
    await _check_db_connectivity_and_schema(settings)
    await _check_active_experiment(settings)

    if isinstance(settings, AgentSettings):
        await _agent_startup_checks(settings)
    elif isinstance(settings, ContextOrchestratorSettings):
        await _orchestrator_startup_checks(settings)
    else:
        raise RuntimeError(f"Unknown service_role: {settings.service_role}")


async def _check_network_testnet(settings: BaseAIATSettings) -> None:
    """Invariant #9: must run on testnet only."""
    if settings.network != "testnet":
        raise RuntimeError(f"AIAT_NETWORK must be 'testnet', got '{settings.network}'")


async def _check_db_connectivity_and_schema(settings: BaseAIATSettings) -> None:
    """Verify DB is reachable and alembic schema version matches expected."""
    async with _db_session(settings) as session:
        version = await session.scalar(text("SELECT version_num FROM alembic_version"))
        if version != EXPECTED_ALEMBIC_VERSION:
            raise RuntimeError(
                f"DB schema version mismatch: expected {EXPECTED_ALEMBIC_VERSION}, "
                f"got {version}. Run 'alembic upgrade head'."
            )


async def _check_active_experiment(settings: BaseAIATSettings) -> None:
    """Experiment exists in DB and has not ended."""
    async with _db_session(settings) as session:
        experiment = await session.get(Experiment, settings.experiment_id)
        if experiment is None:
            raise RuntimeError(f"Experiment '{settings.experiment_id}' not found in DB")
        if experiment.ended_at is not None:
            raise RuntimeError(f"Experiment ended at {experiment.ended_at}, cannot start")
        if experiment.git_commit_sha != settings.git_commit_sha:
            logger.warning(
                "git_commit_sha_mismatch",
                experiment_sha=experiment.git_commit_sha,
                runtime_sha=settings.git_commit_sha,
            )


async def _agent_startup_checks(settings: AgentSettings) -> None:
    """Checks A1-A10 for the agent service (PRD §10.1)."""
    # [A1] Model registered; [A2] provider match; [A3] wallet match
    async with _db_session(settings) as session:
        model = await session.get(Model, settings.model_id)
        if model is None:
            raise RuntimeError(
                f"Model '{settings.model_id}' not registered. "
                "Run 'python scripts/seed_experiment.py' first."
            )
        if model.provider != settings.llm_provider:
            raise RuntimeError(
                f"Provider mismatch: settings={settings.llm_provider}, "
                f"models.provider={model.provider}"
            )
        if model.wallet_address != settings.hl_wallet_address:
            raise RuntimeError(
                f"Wallet mismatch for '{settings.model_id}': "
                f"models={model.wallet_address}, "
                f"settings={settings.hl_wallet_address}"
            )

    # [A4] Pricing config in YAML (fallback exists; explicit entry preferred)
    pricing = load_pricing_for_model(settings.model_id)
    if pricing is None:
        raise RuntimeError(f"No pricing config for '{settings.model_id}' in model_pricing.yaml")

    # [A5] Prompt template registered
    async with _db_session(settings) as session:
        template = await session.get(PromptTemplate, settings.prompt_template_hash)
        if template is None:
            raise RuntimeError(
                f"Prompt template '{settings.prompt_template_hash}' not registered. "
                "Run 'python scripts/register_prompt_template.py' first."
            )

    # [A6] HL testnet reachability + funded wallet (external; mock in unit tests)
    await _check_hl_reachability(settings)

    # [A7] LLM credentials valid — smoke call (external; mock in unit tests)
    await _check_llm_credentials(settings)

    # [A8] Guardrail configuration validity (invariant #8)
    if not (Decimal("0") < settings.max_size_pct <= Decimal("1")):
        raise RuntimeError("AIAT_MAX_SIZE_PCT must be in (0, 1]")
    if settings.hard_max_leverage < Decimal("1"):
        raise RuntimeError("AIAT_HARD_MAX_LEVERAGE must be >= 1")
    if not (Decimal("0") <= settings.min_open_confidence <= Decimal("1")):
        raise RuntimeError("AIAT_MIN_OPEN_CONFIDENCE must be in [0, 1]")

    # [A9] Memory off — invariant #5
    if settings.inject_decision_history is not False:
        raise RuntimeError(
            "AIAT_INJECT_DECISION_HISTORY must be False (invariant #5, thesis design)"
        )

    # [A10] Baseline configs registered for this experiment (fatal, fix B.14)
    async with _db_session(settings) as session:
        rows = await session.scalars(
            select(BaselineConfig.baseline_name).where(
                BaselineConfig.experiment_id == settings.experiment_id
            )
        )
        registered = set(rows)
        missing = EXPECTED_BASELINES - registered
        if missing:
            raise RuntimeError(
                f"Missing baselines for '{settings.experiment_id}': "
                f"{sorted(missing)}. Run 'python scripts/seed_experiment.py' first."
            )


async def _check_hl_reachability(settings: AgentSettings) -> None:
    """[A6] HL testnet reachable and wallet has positive equity.

    Resolves the configured client (mock or real testnet SDK) via
    ``build_hl_client``; with ``AIAT_HL_CLIENT_IMPL=real`` this genuinely probes the
    funded testnet wallet. In unit tests, patch this function via
    ``unittest.mock.patch``.
    """
    from aiat.execution.hyperliquid_client import build_hl_client

    hl = build_hl_client(settings)
    state = await hl.fetch_portfolio_state()
    if state.equity_usd <= 0:
        raise RuntimeError(f"Wallet equity=0 for '{settings.model_id}'. Fund testnet wallet first.")


async def _check_llm_credentials(settings: AgentSettings) -> None:
    """[A7] LLM credentials valid — smoke call.

    In unit tests, patch this function via ``unittest.mock.patch``.
    """
    from aiat.llm.factory import load_llm

    llm = load_llm(settings)
    try:
        await asyncio.wait_for(
            llm.invoke("Reply with exactly: pong"),
            timeout=15.0,
        )
    except Exception as exc:
        raise RuntimeError(f"LLM credentials invalid or unreachable: {exc!r}") from exc


async def _orchestrator_startup_checks(
    settings: ContextOrchestratorSettings,
) -> None:
    """Checks O1-O4 for the context-orchestrator service (PRD §10.1)."""
    # [O1] No LLM credentials leaked (least privilege, fix B.13/B.14)
    suspicious_envs = [
        "AIAT_OPENAI_API_KEY",
        "AIAT_ANTHROPIC_API_KEY",
        "AIAT_DEEPSEEK_API_KEY",
        "AIAT_QWEN_API_KEY",
        "AIAT_OPENROUTER_API_KEY",
        "AIAT_HL_WALLET_PRIVATE_KEY",
        "AIAT_MODEL_ID",
        "AIAT_LLM_PROVIDER",
    ]
    leaked = [v for v in suspicious_envs if os.environ.get(v)]
    if leaked:
        raise RuntimeError(
            f"context-orchestrator has unexpected env vars: {leaked}. Least privilege violation."
        )

    # [O2-O4] External sources reachable (mock in unit tests)
    await _check_orchestrator_sources(settings)


async def _check_orchestrator_sources(
    settings: ContextOrchestratorSettings,
) -> None:
    """[O2/O3/O4] Verify external data sources are reachable.

    In unit tests, patch this function via ``unittest.mock.patch``.
    """
    import httpx

    from aiat.context.collectors.news import NewsCollector
    from aiat.context.collectors.onchain import HLPublicInfoClient
    from aiat.context.collectors.sentiment import SentimentCollector

    # [O2] HL info endpoint
    hl_info = HLPublicInfoClient(network=settings.network)
    try:
        meta = await asyncio.wait_for(hl_info.fetch_meta(), timeout=10.0)
        if not meta or "universe" not in meta:
            raise RuntimeError("HL info endpoint returned unexpected response")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"HL info endpoint unreachable: {exc!r}") from exc

    # [O3] RSS sources (at least one reachable)
    async with httpx.AsyncClient() as http_client:
        news = NewsCollector(client=http_client, timeout_seconds=10)
        reachable = await news.check_sources_reachability()
    if not any(reachable.values()):
        raise RuntimeError("No RSS news source reachable")

    # [O4] Fear & Greed API
    async with httpx.AsyncClient() as http_client:
        sent = SentimentCollector(client=http_client, timeout_seconds=5)
        try:
            await sent.collect()
        except Exception as exc:
            raise RuntimeError(f"Fear&Greed API unreachable: {exc!r}") from exc
