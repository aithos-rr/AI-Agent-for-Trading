"""Tests for orchestration/scheduler.py — APScheduler config (M5-T05, PRD §4.1)."""

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from aiat.config.settings import AgentSettings, ContextOrchestratorSettings
from aiat.orchestration.scheduler import (
    _JOB_DEFAULTS,
    build_scheduler_for_agent,
    build_scheduler_for_orchestrator,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

_BASE = {
    "experiment_id": "exp-sched-test",
    "git_commit_sha": "abc0000000000001",
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
}


@pytest.fixture
def orchestrator_settings() -> ContextOrchestratorSettings:
    # _env_file=None prevents the dev .env (which has AIAT_LLM_GATEWAY etc.)
    # from being loaded — those agent-specific vars trigger extra="forbid".
    return ContextOrchestratorSettings(
        _env_file=None,  # type: ignore[call-arg]
        service_role="context_orchestrator",
        **_BASE,  # type: ignore[arg-type]
    )


@pytest.fixture
def agent_settings() -> AgentSettings:
    return AgentSettings(
        **_BASE,  # type: ignore[arg-type]
        service_role="agent",
        model_id="model-openai-test",
        prompt_template_hash="deadbeef00000000",
        llm_provider="openai",
        model_name_api="gpt-4o",
        openai_api_key="sk-test-key",
        hl_wallet_private_key="0x" + "0" * 64,
        hl_wallet_address="0x" + "0" * 40,
        llm_gateway="direct",
    )


async def _noop() -> None:
    """Dummy no-op job for configuration tests."""


# ── Constant validation ───────────────────────────────────────────────────────


def test_job_defaults_constant() -> None:
    assert _JOB_DEFAULTS["coalesce"] is True
    assert _JOB_DEFAULTS["max_instances"] == 1
    assert _JOB_DEFAULTS["misfire_grace_time"] == 60


# ── Orchestrator ─────────────────────────────────────────────────────────────


async def test_orchestrator_returns_asyncio_scheduler(
    orchestrator_settings: ContextOrchestratorSettings,
) -> None:
    scheduler = await build_scheduler_for_orchestrator(orchestrator_settings, tick_job=_noop)
    assert isinstance(scheduler, AsyncIOScheduler)


async def test_orchestrator_has_exactly_one_job(
    orchestrator_settings: ContextOrchestratorSettings,
) -> None:
    scheduler = await build_scheduler_for_orchestrator(orchestrator_settings, tick_job=_noop)
    assert len(scheduler.get_jobs()) == 1


async def test_orchestrator_job_has_cron_trigger(
    orchestrator_settings: ContextOrchestratorSettings,
) -> None:
    scheduler = await build_scheduler_for_orchestrator(orchestrator_settings, tick_job=_noop)
    assert isinstance(scheduler.get_jobs()[0].trigger, CronTrigger)


async def test_orchestrator_job_defaults_coalesce(
    orchestrator_settings: ContextOrchestratorSettings,
) -> None:
    scheduler = await build_scheduler_for_orchestrator(orchestrator_settings, tick_job=_noop)
    assert scheduler._job_defaults["coalesce"] is True


async def test_orchestrator_job_defaults_max_instances(
    orchestrator_settings: ContextOrchestratorSettings,
) -> None:
    scheduler = await build_scheduler_for_orchestrator(orchestrator_settings, tick_job=_noop)
    assert scheduler._job_defaults["max_instances"] == 1


async def test_orchestrator_job_defaults_misfire_grace_time(
    orchestrator_settings: ContextOrchestratorSettings,
) -> None:
    scheduler = await build_scheduler_for_orchestrator(orchestrator_settings, tick_job=_noop)
    assert scheduler._job_defaults["misfire_grace_time"] == 60


async def test_orchestrator_trigger_fires_at_quarter_hours(
    orchestrator_settings: ContextOrchestratorSettings,
) -> None:
    """CronTrigger must include minutes 0, 15, 30, 45."""
    scheduler = await build_scheduler_for_orchestrator(orchestrator_settings, tick_job=_noop)
    trigger_repr = str(scheduler.get_jobs()[0].trigger)
    assert "minute='0,15,30,45'" in trigger_repr, f"unexpected trigger: {trigger_repr!r}"


async def test_orchestrator_trigger_second_is_zero(
    orchestrator_settings: ContextOrchestratorSettings,
) -> None:
    """Orchestrator fires at second=0 (no start-delay)."""
    scheduler = await build_scheduler_for_orchestrator(orchestrator_settings, tick_job=_noop)
    trigger_repr = str(scheduler.get_jobs()[0].trigger)
    assert "second='0'" in trigger_repr, f"expected second='0' in {trigger_repr!r}"


# ── Agent ─────────────────────────────────────────────────────────────────────


async def test_agent_returns_asyncio_scheduler(
    agent_settings: AgentSettings,
) -> None:
    scheduler = await build_scheduler_for_agent(agent_settings, tick_job=_noop)
    assert isinstance(scheduler, AsyncIOScheduler)


async def test_agent_has_exactly_one_job(agent_settings: AgentSettings) -> None:
    scheduler = await build_scheduler_for_agent(agent_settings, tick_job=_noop)
    assert len(scheduler.get_jobs()) == 1


async def test_agent_job_has_cron_trigger(agent_settings: AgentSettings) -> None:
    scheduler = await build_scheduler_for_agent(agent_settings, tick_job=_noop)
    assert isinstance(scheduler.get_jobs()[0].trigger, CronTrigger)


async def test_agent_job_defaults_coalesce(agent_settings: AgentSettings) -> None:
    scheduler = await build_scheduler_for_agent(agent_settings, tick_job=_noop)
    assert scheduler._job_defaults["coalesce"] is True


async def test_agent_job_defaults_max_instances(agent_settings: AgentSettings) -> None:
    scheduler = await build_scheduler_for_agent(agent_settings, tick_job=_noop)
    assert scheduler._job_defaults["max_instances"] == 1


async def test_agent_job_defaults_misfire_grace_time(agent_settings: AgentSettings) -> None:
    scheduler = await build_scheduler_for_agent(agent_settings, tick_job=_noop)
    assert scheduler._job_defaults["misfire_grace_time"] == 60


async def test_agent_trigger_fires_at_quarter_hours(
    agent_settings: AgentSettings,
) -> None:
    """CronTrigger must include minutes 0, 15, 30, 45."""
    scheduler = await build_scheduler_for_agent(agent_settings, tick_job=_noop)
    trigger_repr = str(scheduler.get_jobs()[0].trigger)
    assert "minute='0,15,30,45'" in trigger_repr, f"unexpected trigger: {trigger_repr!r}"


async def test_agent_trigger_has_30s_start_delay(agent_settings: AgentSettings) -> None:
    """Agent CronTrigger fires at second=agent_start_delay_seconds (default 30)."""
    scheduler = await build_scheduler_for_agent(agent_settings, tick_job=_noop)
    trigger_repr = str(scheduler.get_jobs()[0].trigger)
    assert "second='30'" in trigger_repr, f"expected second='30' in {trigger_repr!r}"


async def test_agent_trigger_respects_custom_delay(
    agent_settings: AgentSettings,
) -> None:
    """Custom agent_start_delay_seconds is used as second offset in trigger."""
    custom = agent_settings.model_copy(update={"agent_start_delay_seconds": 45})
    scheduler = await build_scheduler_for_agent(custom, tick_job=_noop)
    trigger_repr = str(scheduler.get_jobs()[0].trigger)
    assert "second='45'" in trigger_repr, f"expected second='45' in {trigger_repr!r}"


async def test_agent_fires_after_orchestrator(
    agent_settings: AgentSettings,
    orchestrator_settings: ContextOrchestratorSettings,
) -> None:
    """Agent trigger repr differs from orchestrator (30s second offset)."""
    agent_sched = await build_scheduler_for_agent(agent_settings, tick_job=_noop)
    orch_sched = await build_scheduler_for_orchestrator(orchestrator_settings, tick_job=_noop)
    agent_repr = str(agent_sched.get_jobs()[0].trigger)
    orch_repr = str(orch_sched.get_jobs()[0].trigger)
    assert agent_repr != orch_repr
