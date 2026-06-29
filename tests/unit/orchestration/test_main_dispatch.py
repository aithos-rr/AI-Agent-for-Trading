"""Unit tests for __main__.py dispatcher (PRD §11.2, M5-T07)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiat.config.settings import AgentSettings, ContextOrchestratorSettings

# Aligned tick the closures derive via current_tick() (ADR-0019).
_TICK_ID = "2026-06-29T14:30:00+00:00"
_SCHED = datetime(2026, 6, 29, 14, 30, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BASE = {
    "experiment_id": "exp-test",
    "git_commit_sha": "deadbeef",
    "database_url": "postgresql+asyncpg://test:test@localhost/test",
    "network": "testnet",
    "service_role": "agent",
    "model_id": "openai-gpt4o",
    "prompt_template_hash": "abc123",
    "llm_provider": "openai",
    "model_name_api": "gpt-4o",
    "openai_api_key": "sk-test",
    "hl_wallet_private_key": "0x" + "0" * 64,
    "hl_wallet_address": "0x" + "0" * 40,
    "llm_gateway": "direct",
    "hard_timeout_seconds": 180,
}


def _agent_settings(**overrides: object) -> AgentSettings:
    return AgentSettings(**{**_BASE, **overrides})  # type: ignore[arg-type]


def _orchestrator_settings(**overrides: object) -> ContextOrchestratorSettings:
    base = {
        "experiment_id": "exp-test",
        "git_commit_sha": "deadbeef",
        "database_url": "postgresql+asyncpg://test:test@localhost/test",
        "network": "testnet",
        "service_role": "context_orchestrator",
    }
    return ContextOrchestratorSettings(  # type: ignore[call-arg]
        _env_file=None,
        **{**base, **overrides},  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Tests for configure_logging
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    def test_configure_logging_does_not_raise(self) -> None:
        """configure_logging must not raise with valid settings."""
        from aiat.__main__ import configure_logging

        settings = _agent_settings()
        configure_logging(settings)  # should not raise

    def test_configure_logging_calls_structlog_configure(self) -> None:
        """configure_logging must call structlog.configure (via logging_config module)."""
        import aiat.__main__ as main_mod

        settings = _agent_settings()
        with patch("aiat.observability.logging_config.structlog") as mock_structlog:
            mock_structlog.PrintLoggerFactory = MagicMock()
            mock_structlog.configure = MagicMock()
            mock_structlog.contextvars = MagicMock()
            mock_structlog.stdlib = MagicMock()
            mock_structlog.processors = MagicMock()
            mock_structlog.make_filtering_bound_logger = MagicMock()
            main_mod.configure_logging(settings)

        mock_structlog.configure.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for _main dispatch (agent path)
# ---------------------------------------------------------------------------


class TestMainDispatch:
    @pytest.mark.asyncio
    async def test_agent_role_calls_build_scheduler_for_agent(self) -> None:
        """When settings.service_role='agent', build_scheduler_for_agent must be called."""
        settings = _agent_settings()
        mock_scheduler = MagicMock()
        mock_scheduler.start = MagicMock()

        import aiat.__main__ as main_mod

        with (
            patch("aiat.__main__.load_settings", return_value=settings),
            patch("aiat.__main__.configure_logging"),
            patch("aiat.__main__.startup_checks", new_callable=AsyncMock),
            patch(
                "aiat.__main__._build_agent_tick_job",
                new_callable=AsyncMock,
                return_value=AsyncMock(),
            ),
            patch(
                "aiat.__main__.build_scheduler_for_agent",
                new_callable=AsyncMock,
                return_value=mock_scheduler,
            ) as mock_build_agent,
            patch(
                "aiat.__main__.build_scheduler_for_orchestrator",
                new_callable=AsyncMock,
            ) as mock_build_orch,
            patch("aiat.__main__._run_forever", new_callable=AsyncMock),
        ):
            await main_mod._main()

        mock_build_agent.assert_called_once()
        mock_build_orch.assert_not_called()
        mock_scheduler.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_orchestrator_role_calls_build_scheduler_for_orchestrator(self) -> None:
        """When settings.service_role='context_orchestrator', build_scheduler_for_orchestrator."""
        settings = _orchestrator_settings()
        mock_scheduler = MagicMock()
        mock_scheduler.start = MagicMock()

        import aiat.__main__ as main_mod

        with (
            patch("aiat.__main__.load_settings", return_value=settings),
            patch("aiat.__main__.configure_logging"),
            patch("aiat.__main__.startup_checks", new_callable=AsyncMock),
            patch(
                "aiat.__main__._build_orchestrator_tick_job",
                new_callable=AsyncMock,
                return_value=AsyncMock(),
            ),
            patch(
                "aiat.__main__.build_scheduler_for_orchestrator",
                new_callable=AsyncMock,
                return_value=mock_scheduler,
            ) as mock_build_orch,
            patch(
                "aiat.__main__.build_scheduler_for_agent",
                new_callable=AsyncMock,
            ) as mock_build_agent,
            patch("aiat.__main__._run_forever", new_callable=AsyncMock),
        ):
            await main_mod._main()

        mock_build_orch.assert_called_once()
        mock_build_agent.assert_not_called()
        mock_scheduler.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_startup_checks_called_before_scheduler(self) -> None:
        """startup_checks must be awaited before the scheduler is built."""
        settings = _agent_settings()
        call_order: list[str] = []

        async def _fake_startup(s: object) -> None:
            call_order.append("startup_checks")

        async def _fake_build_agent(s: object, tick_job: object = None) -> MagicMock:
            call_order.append("build_scheduler_for_agent")
            m = MagicMock()
            m.start = MagicMock()
            return m

        import aiat.__main__ as main_mod

        with (
            patch("aiat.__main__.load_settings", return_value=settings),
            patch("aiat.__main__.configure_logging"),
            patch("aiat.__main__.startup_checks", side_effect=_fake_startup),
            patch(
                "aiat.__main__._build_agent_tick_job",
                new_callable=AsyncMock,
                return_value=AsyncMock(),
            ),
            patch(
                "aiat.__main__.build_scheduler_for_agent",
                side_effect=_fake_build_agent,
            ),
            patch("aiat.__main__._run_forever", new_callable=AsyncMock),
        ):
            await main_mod._main()

        assert call_order.index("startup_checks") < call_order.index("build_scheduler_for_agent")

    @pytest.mark.asyncio
    async def test_agent_tick_job_passed_to_scheduler_builder(self) -> None:
        """tick_job from _build_agent_tick_job must be forwarded to build_scheduler_for_agent."""
        settings = _agent_settings()
        sentinel_tick_job = AsyncMock(name="tick_job_sentinel")
        captured_tick_job: list[object] = []
        mock_scheduler = MagicMock()
        mock_scheduler.start = MagicMock()

        async def _fake_build_agent(s: object, tick_job: object = None) -> MagicMock:
            captured_tick_job.append(tick_job)
            return mock_scheduler

        import aiat.__main__ as main_mod

        with (
            patch("aiat.__main__.load_settings", return_value=settings),
            patch("aiat.__main__.configure_logging"),
            patch("aiat.__main__.startup_checks", new_callable=AsyncMock),
            patch(
                "aiat.__main__._build_agent_tick_job",
                new_callable=AsyncMock,
                return_value=sentinel_tick_job,
            ),
            patch(
                "aiat.__main__.build_scheduler_for_agent",
                side_effect=_fake_build_agent,
            ),
            patch("aiat.__main__._run_forever", new_callable=AsyncMock),
        ):
            await main_mod._main()

        assert len(captured_tick_job) == 1
        assert captured_tick_job[0] is sentinel_tick_job

    @pytest.mark.asyncio
    async def test_startup_checks_failure_propagates(self) -> None:
        """A RuntimeError from startup_checks must propagate and abort startup."""
        settings = _agent_settings()

        import aiat.__main__ as main_mod

        with (
            patch("aiat.__main__.load_settings", return_value=settings),
            patch("aiat.__main__.configure_logging"),
            patch(
                "aiat.__main__.startup_checks",
                new_callable=AsyncMock,
                side_effect=RuntimeError("testnet check failed"),
            ),
            patch("aiat.__main__.build_scheduler_for_agent") as mock_build,
        ):
            with pytest.raises(RuntimeError, match="testnet check failed"):
                await main_mod._main()

        mock_build.assert_not_called()

    def test_main_runs_asyncio_event_loop(self) -> None:
        """main() must call asyncio.run with the _main coroutine."""
        import aiat.__main__ as main_mod

        with patch("aiat.__main__.asyncio") as mock_asyncio:
            mock_asyncio.run = MagicMock()
            main_mod.main()

        mock_asyncio.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_agent_tick_job_invokes_run_once_with_aligned_tick(self) -> None:
        """_build_agent_tick_job returns a ZERO-ARG closure (APScheduler fires with no
        args) that derives the tick via current_tick() and calls run_once. Regression
        guard for the bug where it returned the bare 2-arg run_once → TypeError per tick.
        """
        settings = _agent_settings()

        import aiat.__main__ as main_mod

        with (
            patch("aiat.__main__.get_db_session", return_value=MagicMock()),
            patch("aiat.__main__.load_llm", return_value=MagicMock()),
            patch("aiat.__main__.build_hl_client", return_value=MagicMock()),
            patch("aiat.__main__.DecisionLoop") as MockLoop,
            patch(
                "aiat.__main__.current_tick",
                return_value=(_TICK_ID, _SCHED),
            ),
        ):
            mock_loop_instance = MagicMock()
            mock_loop_instance.run_once = AsyncMock(return_value="run-id")
            MockLoop.return_value = mock_loop_instance
            tick_job = await main_mod._build_agent_tick_job(settings)
            # zero-arg callable — must be invocable with no args (what APScheduler does)
            await tick_job()

        MockLoop.assert_called_once()
        assert tick_job is not mock_loop_instance.run_once  # closure, not the bare method
        mock_loop_instance.run_once.assert_awaited_once_with(_TICK_ID, _SCHED)

    @pytest.mark.asyncio
    async def test_build_orchestrator_tick_job_invokes_build_tick_context(self) -> None:
        """_build_orchestrator_tick_job returns a zero-arg closure that builds the context
        snapshot for the aligned tick (collectors all on settings.network = testnet)."""
        settings = _orchestrator_settings()

        import aiat.__main__ as main_mod

        with (
            patch("aiat.__main__.get_db_session", return_value=MagicMock()),
            patch("aiat.__main__.httpx"),
            patch("aiat.__main__.TechnicalCollector"),
            patch("aiat.__main__.SentimentCollector"),
            patch("aiat.__main__.NewsCollector"),
            patch("aiat.__main__.HLPublicInfoClient"),
            patch("aiat.__main__.OnchainCollector"),
            patch("aiat.__main__.ContextBuilder"),
            patch("aiat.__main__.ContextOrchestrator") as MockOrch,
            patch(
                "aiat.__main__.current_tick",
                return_value=(_TICK_ID, _SCHED),
            ),
        ):
            mock_orch_instance = MagicMock()
            mock_orch_instance.build_tick_context = AsyncMock()
            MockOrch.return_value = mock_orch_instance
            tick_job = await main_mod._build_orchestrator_tick_job(settings)
            await tick_job()

        MockOrch.assert_called_once()
        mock_orch_instance.build_tick_context.assert_awaited_once_with(
            _TICK_ID, _TICK_ID, settings.experiment_id
        )
