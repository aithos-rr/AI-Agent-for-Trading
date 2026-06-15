"""Unit tests for orchestration/lifecycle.py — PRD §10.1, M5-T04."""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiat.config.settings import AgentSettings, ContextOrchestratorSettings
from aiat.orchestration.lifecycle import (
    EXPECTED_ALEMBIC_VERSION,
    EXPECTED_BASELINES,
    _agent_startup_checks,
    _check_active_experiment,
    _check_db_connectivity_and_schema,
    _check_network_testnet,
    _orchestrator_startup_checks,
    startup_checks,
)

# ---------------------------------------------------------------------------
# Minimal valid kwarg helpers (mirrors tests/unit/config/test_settings.py)
# ---------------------------------------------------------------------------

_BASE_COMMON = {
    "experiment_id": "exp-abc",
    "git_commit_sha": "deadbeef",
    "database_url": "postgresql+asyncpg://test:test@localhost/test",
}

_AGENT_REQUIRED = {
    **_BASE_COMMON,
    "model_id": "model-openai",
    "prompt_template_hash": "abc123hash",
    "llm_provider": "openai",
    "model_name_api": "gpt-4o",
    "openai_api_key": "sk-test",
    "hl_wallet_private_key": "0x" + "0" * 64,
    "hl_wallet_address": "0x" + "0" * 40,
    "llm_gateway": "direct",
}

_ORCHESTRATOR_REQUIRED = {**_BASE_COMMON}


def _agent(**kwargs: object) -> AgentSettings:
    return AgentSettings(**{**_AGENT_REQUIRED, **kwargs})  # type: ignore[arg-type]


def _orchestrator(**kwargs: object) -> ContextOrchestratorSettings:
    return ContextOrchestratorSettings(
        _env_file=None,  # type: ignore[call-arg]
        **{**_ORCHESTRATOR_REQUIRED, **kwargs},  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# DB session mock helpers
# ---------------------------------------------------------------------------


def _session_cycle(*sessions: AsyncMock) -> object:
    """Return a _db_session replacement that yields each session in turn."""
    idx = [0]

    @asynccontextmanager  # type: ignore[misc]
    async def _cm(_settings: object) -> object:
        s = sessions[min(idx[0], len(sessions) - 1)]
        idx[0] += 1
        yield s

    return _cm


def _make_model(provider: str = "openai", wallet: str = "0x" + "0" * 40) -> MagicMock:
    m = MagicMock()
    m.provider = provider
    m.wallet_address = wallet
    return m


def _model_session(model: object) -> AsyncMock:
    s = AsyncMock()
    s.get.return_value = model
    return s


def _template_session(template: object = None) -> AsyncMock:
    s = AsyncMock()
    s.get.return_value = template if template is not None else MagicMock()
    return s


def _baselines_session(names: list[str]) -> AsyncMock:
    s = AsyncMock()
    s.scalars.return_value = names
    return s


# ---------------------------------------------------------------------------
# _check_network_testnet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_network_testnet_ok() -> None:
    settings = MagicMock()
    settings.network = "testnet"
    await _check_network_testnet(settings)  # must not raise


@pytest.mark.asyncio
async def test_check_network_testnet_rejects_mainnet() -> None:
    settings = MagicMock()
    settings.network = "mainnet"
    with pytest.raises(RuntimeError, match="AIAT_NETWORK must be 'testnet'"):
        await _check_network_testnet(settings)


@pytest.mark.asyncio
async def test_check_network_testnet_rejects_arbitrary_value() -> None:
    settings = MagicMock()
    settings.network = "production"
    with pytest.raises(RuntimeError, match="got 'production'"):
        await _check_network_testnet(settings)


# ---------------------------------------------------------------------------
# _check_db_connectivity_and_schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_db_schema_ok() -> None:
    session = AsyncMock()
    session.scalar.return_value = EXPECTED_ALEMBIC_VERSION
    settings = MagicMock()
    with patch("aiat.orchestration.lifecycle._db_session", _session_cycle(session)):
        await _check_db_connectivity_and_schema(settings)  # no error


@pytest.mark.asyncio
async def test_check_db_schema_mismatch_raises() -> None:
    session = AsyncMock()
    session.scalar.return_value = "000"
    settings = MagicMock()
    with patch("aiat.orchestration.lifecycle._db_session", _session_cycle(session)):
        with pytest.raises(RuntimeError, match="DB schema version mismatch"):
            await _check_db_connectivity_and_schema(settings)


# ---------------------------------------------------------------------------
# _check_active_experiment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_active_experiment_ok() -> None:
    exp = MagicMock()
    exp.ended_at = None
    exp.git_commit_sha = "deadbeef"
    session = AsyncMock()
    session.get.return_value = exp
    settings = MagicMock()
    settings.experiment_id = "exp-abc"
    settings.git_commit_sha = "deadbeef"
    with patch("aiat.orchestration.lifecycle._db_session", _session_cycle(session)):
        await _check_active_experiment(settings)  # no error


@pytest.mark.asyncio
async def test_check_active_experiment_not_found_raises() -> None:
    session = AsyncMock()
    session.get.return_value = None
    settings = MagicMock()
    settings.experiment_id = "missing"
    with patch("aiat.orchestration.lifecycle._db_session", _session_cycle(session)):
        with pytest.raises(RuntimeError, match="not found in DB"):
            await _check_active_experiment(settings)


@pytest.mark.asyncio
async def test_check_active_experiment_ended_raises() -> None:
    exp = MagicMock()
    exp.ended_at = "2026-01-01"
    session = AsyncMock()
    session.get.return_value = exp
    settings = MagicMock()
    settings.experiment_id = "exp-abc"
    with patch("aiat.orchestration.lifecycle._db_session", _session_cycle(session)):
        with pytest.raises(RuntimeError, match="Experiment ended"):
            await _check_active_experiment(settings)


@pytest.mark.asyncio
async def test_check_active_experiment_sha_mismatch_warns_not_raises() -> None:
    """SHA mismatch is a warning only — not fatal (allows patch deploys)."""
    exp = MagicMock()
    exp.ended_at = None
    exp.git_commit_sha = "old-sha"
    session = AsyncMock()
    session.get.return_value = exp
    settings = MagicMock()
    settings.experiment_id = "exp-abc"
    settings.git_commit_sha = "new-sha"
    with patch("aiat.orchestration.lifecycle._db_session", _session_cycle(session)):
        await _check_active_experiment(settings)  # must not raise


# ---------------------------------------------------------------------------
# _agent_startup_checks — A2: provider mismatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_a2_provider_mismatch_raises() -> None:
    settings = _agent(llm_provider="openai")
    model = _make_model(provider="anthropic", wallet=settings.hl_wallet_address)
    db = _session_cycle(_model_session(model))
    with patch("aiat.orchestration.lifecycle._db_session", db):
        with pytest.raises(RuntimeError, match="Provider mismatch"):
            await _agent_startup_checks(settings)


# ---------------------------------------------------------------------------
# _agent_startup_checks — A3: wallet mismatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_a3_wallet_mismatch_raises() -> None:
    settings = _agent()
    model = _make_model(provider="openai", wallet="0xDIFFERENT")
    db = _session_cycle(_model_session(model))
    with patch("aiat.orchestration.lifecycle._db_session", db):
        with pytest.raises(RuntimeError, match="Wallet mismatch"):
            await _agent_startup_checks(settings)


# ---------------------------------------------------------------------------
# _agent_startup_checks — A5: template not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_a5_template_not_found_raises() -> None:
    settings = _agent()
    model = _make_model(provider="openai", wallet=settings.hl_wallet_address)
    template_session = AsyncMock()
    template_session.get.return_value = None
    db = _session_cycle(_model_session(model), template_session)
    with patch("aiat.orchestration.lifecycle._db_session", db):
        with (
            patch("aiat.orchestration.lifecycle._check_hl_reachability", AsyncMock()),
            patch("aiat.orchestration.lifecycle._check_llm_credentials", AsyncMock()),
        ):
            with pytest.raises(RuntimeError, match="Prompt template.*not registered"):
                await _agent_startup_checks(settings)


# ---------------------------------------------------------------------------
# _agent_startup_checks — A8: guardrail config validity
# ---------------------------------------------------------------------------


def _full_db_mock(settings: AgentSettings) -> object:
    """DB mock that succeeds for A1/A2/A3, A5, and A10 (full baseline set)."""
    model = _make_model(provider=settings.llm_provider, wallet=settings.hl_wallet_address)
    return _session_cycle(
        _model_session(model),
        _template_session(),
        _baselines_session(list(EXPECTED_BASELINES)),
    )


@pytest.mark.asyncio
async def test_agent_a8_max_size_pct_zero_raises() -> None:
    settings = _agent(max_size_pct=Decimal("0"))
    db = _full_db_mock(settings)
    with patch("aiat.orchestration.lifecycle._db_session", db):
        with (
            patch("aiat.orchestration.lifecycle._check_hl_reachability", AsyncMock()),
            patch("aiat.orchestration.lifecycle._check_llm_credentials", AsyncMock()),
        ):
            with pytest.raises(RuntimeError, match="AIAT_MAX_SIZE_PCT"):
                await _agent_startup_checks(settings)


@pytest.mark.asyncio
async def test_agent_a8_hard_max_leverage_zero_raises() -> None:
    # Field ge=1 in settings, but we use MagicMock to bypass pydantic
    settings = _agent()
    object.__setattr__(settings, "hard_max_leverage", Decimal("0"))
    db = _full_db_mock(settings)
    with patch("aiat.orchestration.lifecycle._db_session", db):
        with (
            patch("aiat.orchestration.lifecycle._check_hl_reachability", AsyncMock()),
            patch("aiat.orchestration.lifecycle._check_llm_credentials", AsyncMock()),
        ):
            with pytest.raises(RuntimeError, match="AIAT_HARD_MAX_LEVERAGE"):
                await _agent_startup_checks(settings)


@pytest.mark.asyncio
async def test_agent_a8_valid_passes() -> None:
    settings = _agent()  # defaults: max_size_pct=0.20, hard_max_leverage=10, etc.
    db = _full_db_mock(settings)
    with patch("aiat.orchestration.lifecycle._db_session", db):
        with (
            patch("aiat.orchestration.lifecycle._check_hl_reachability", AsyncMock()),
            patch("aiat.orchestration.lifecycle._check_llm_credentials", AsyncMock()),
        ):
            await _agent_startup_checks(settings)  # must not raise


# ---------------------------------------------------------------------------
# _agent_startup_checks — A9: memory off (invariant #5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_a9_memory_on_raises() -> None:
    settings = _agent(inject_decision_history=True)
    db = _full_db_mock(settings)
    with patch("aiat.orchestration.lifecycle._db_session", db):
        with (
            patch("aiat.orchestration.lifecycle._check_hl_reachability", AsyncMock()),
            patch("aiat.orchestration.lifecycle._check_llm_credentials", AsyncMock()),
        ):
            with pytest.raises(RuntimeError, match="AIAT_INJECT_DECISION_HISTORY"):
                await _agent_startup_checks(settings)


@pytest.mark.asyncio
async def test_agent_a9_memory_off_ok() -> None:
    settings = _agent(inject_decision_history=False)
    db = _full_db_mock(settings)
    with patch("aiat.orchestration.lifecycle._db_session", db):
        with (
            patch("aiat.orchestration.lifecycle._check_hl_reachability", AsyncMock()),
            patch("aiat.orchestration.lifecycle._check_llm_credentials", AsyncMock()),
        ):
            await _agent_startup_checks(settings)  # must not raise


# ---------------------------------------------------------------------------
# _agent_startup_checks — A10: baseline configs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_a10_baseline_missing_raises() -> None:
    settings = _agent()
    model = _make_model(provider=settings.llm_provider, wallet=settings.hl_wallet_address)
    db = _session_cycle(
        _model_session(model),
        _template_session(),
        _baselines_session(["buy_and_hold"]),  # missing cash + naive_momentum
    )
    with patch("aiat.orchestration.lifecycle._db_session", db):
        with (
            patch("aiat.orchestration.lifecycle._check_hl_reachability", AsyncMock()),
            patch("aiat.orchestration.lifecycle._check_llm_credentials", AsyncMock()),
        ):
            with pytest.raises(RuntimeError, match="Missing baselines"):
                await _agent_startup_checks(settings)


@pytest.mark.asyncio
async def test_agent_a10_all_baselines_present_ok() -> None:
    settings = _agent()
    db = _full_db_mock(settings)
    with patch("aiat.orchestration.lifecycle._db_session", db):
        with (
            patch("aiat.orchestration.lifecycle._check_hl_reachability", AsyncMock()),
            patch("aiat.orchestration.lifecycle._check_llm_credentials", AsyncMock()),
        ):
            await _agent_startup_checks(settings)  # must not raise


# ---------------------------------------------------------------------------
# _orchestrator_startup_checks — O1: env-var leak detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_o1_leaked_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIAT_MODEL_ID", "model-openai")
    settings = _orchestrator()
    with pytest.raises(RuntimeError, match="AIAT_MODEL_ID"):
        await _orchestrator_startup_checks(settings)


@pytest.mark.asyncio
async def test_orchestrator_o1_leaked_wallet_key_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIAT_HL_WALLET_PRIVATE_KEY", "0xsecret")
    settings = _orchestrator()
    with pytest.raises(RuntimeError, match="Least privilege violation"):
        await _orchestrator_startup_checks(settings)


@pytest.mark.asyncio
async def test_orchestrator_o1_clean_env_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in [
        "AIAT_OPENAI_API_KEY",
        "AIAT_ANTHROPIC_API_KEY",
        "AIAT_DEEPSEEK_API_KEY",
        "AIAT_QWEN_API_KEY",
        "AIAT_HL_WALLET_PRIVATE_KEY",
        "AIAT_MODEL_ID",
    ]:
        monkeypatch.delenv(v, raising=False)
    settings = _orchestrator()
    with patch("aiat.orchestration.lifecycle._check_orchestrator_sources", AsyncMock()):
        await _orchestrator_startup_checks(settings)  # must not raise


# ---------------------------------------------------------------------------
# startup_checks — dispatcher (role dispatch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_checks_dispatches_to_agent() -> None:
    settings = _agent()
    with (
        patch("aiat.orchestration.lifecycle._check_network_testnet", AsyncMock()) as mock_net,
        patch("aiat.orchestration.lifecycle._check_db_connectivity_and_schema", AsyncMock()),
        patch("aiat.orchestration.lifecycle._check_active_experiment", AsyncMock()),
        patch("aiat.orchestration.lifecycle._agent_startup_checks", AsyncMock()) as mock_agent,
        patch(
            "aiat.orchestration.lifecycle._orchestrator_startup_checks", AsyncMock()
        ) as mock_orch,
    ):
        await startup_checks(settings)
        mock_agent.assert_called_once_with(settings)
        mock_orch.assert_not_called()
        mock_net.assert_called_once()


@pytest.mark.asyncio
async def test_startup_checks_dispatches_to_orchestrator() -> None:
    settings = _orchestrator()
    with (
        patch("aiat.orchestration.lifecycle._check_network_testnet", AsyncMock()),
        patch("aiat.orchestration.lifecycle._check_db_connectivity_and_schema", AsyncMock()),
        patch("aiat.orchestration.lifecycle._check_active_experiment", AsyncMock()),
        patch("aiat.orchestration.lifecycle._agent_startup_checks", AsyncMock()) as mock_agent,
        patch(
            "aiat.orchestration.lifecycle._orchestrator_startup_checks", AsyncMock()
        ) as mock_orch,
    ):
        await startup_checks(settings)
        mock_orch.assert_called_once_with(settings)
        mock_agent.assert_not_called()
