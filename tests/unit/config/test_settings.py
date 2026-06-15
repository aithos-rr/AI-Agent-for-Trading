"""Unit tests for config/settings.py — PRD §10.3, M5-T03."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from aiat.config.settings import (
    AgentSettings,
    BaseAIATSettings,
    ContextOrchestratorSettings,
    load_settings,
)

# ---------------------------------------------------------------------------
# Minimal valid kwargs helpers
# ---------------------------------------------------------------------------

_BASE_COMMON = {
    "experiment_id": "exp-abc",
    "git_commit_sha": "deadbeef",
    "database_url": "postgresql+asyncpg://test:test@localhost/test",
}

_AGENT_REQUIRED = {
    **_BASE_COMMON,
    "model_id": "model-openai",
    "prompt_template_hash": "abc123",
    "llm_provider": "openai",
    "model_name_api": "gpt-4o",
    "openai_api_key": "sk-test",
    "hl_wallet_private_key": "0x" + "0" * 64,
    "hl_wallet_address": "0x" + "0" * 40,
    "llm_gateway": "direct",  # explicit — overrides any .env value
}

_ORCHESTRATOR_REQUIRED = {
    **_BASE_COMMON,
}


def _agent(**kwargs: object) -> AgentSettings:
    return AgentSettings(**{**_AGENT_REQUIRED, **kwargs})  # type: ignore[arg-type]


def _orchestrator(**kwargs: object) -> ContextOrchestratorSettings:
    # _env_file=None prevents the dev .env (which has AIAT_LLM_GATEWAY etc.) from
    # being loaded — those agent-specific vars would trigger extra="forbid" on the
    # orchestrator class which doesn't define those fields.
    return ContextOrchestratorSettings(
        _env_file=None,  # type: ignore[call-arg]
        **{**_ORCHESTRATOR_REQUIRED, **kwargs},  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# BaseAIATSettings
# ---------------------------------------------------------------------------


def test_base_settings_is_abstract() -> None:
    assert issubclass(AgentSettings, BaseAIATSettings)
    assert issubclass(ContextOrchestratorSettings, BaseAIATSettings)


# ---------------------------------------------------------------------------
# AgentSettings construction
# ---------------------------------------------------------------------------


def test_agent_settings_valid_openai() -> None:
    s = _agent()
    assert s.llm_provider == "openai"
    assert s.service_role == "agent"
    assert s.network == "testnet"
    assert s.max_size_pct == Decimal("0.20")
    assert s.hard_max_leverage == Decimal("10")
    assert s.min_open_confidence == Decimal("0.4")
    assert s.inject_decision_history is False


def test_agent_settings_valid_anthropic() -> None:
    s = _agent(llm_provider="anthropic", anthropic_api_key="sk-ant-test")
    assert s.llm_provider == "anthropic"
    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-test"


def test_agent_settings_secretstr_wraps_api_key() -> None:
    s = _agent(openai_api_key="plaintext-key")
    assert s.openai_api_key is not None
    assert s.openai_api_key.get_secret_value() == "plaintext-key"


def test_agent_settings_secretstr_wraps_wallet_key() -> None:
    key = "0x" + "a" * 64
    s = _agent(hl_wallet_private_key=key)
    assert s.hl_wallet_private_key.get_secret_value() == key


def test_agent_settings_guardrail_defaults_are_decimal() -> None:
    s = _agent()
    assert isinstance(s.max_size_pct, Decimal)
    assert isinstance(s.hard_max_leverage, Decimal)
    assert isinstance(s.min_open_confidence, Decimal)


def test_agent_settings_optional_fields_default_none() -> None:
    s = _agent()
    assert s.temperature is None
    assert s.top_p is None
    assert s.max_tokens is None
    assert s.seed is None


def test_agent_settings_network_must_be_testnet() -> None:
    with pytest.raises(ValidationError, match="testnet"):
        _agent(network="mainnet")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# validate_api_key_matches_provider
# ---------------------------------------------------------------------------


def test_validator_missing_openai_key_raises() -> None:
    with pytest.raises(ValidationError, match="AIAT_OPENAI_API_KEY"):
        _agent(openai_api_key=None)


def test_validator_missing_anthropic_key_raises() -> None:
    with pytest.raises(ValidationError, match="AIAT_ANTHROPIC_API_KEY"):
        _agent(llm_provider="anthropic", anthropic_api_key=None)


def test_validator_missing_deepseek_key_raises() -> None:
    with pytest.raises(ValidationError, match="AIAT_DEEPSEEK_API_KEY"):
        _agent(llm_provider="deepseek", deepseek_api_key=None)


def test_validator_missing_qwen_key_raises() -> None:
    with pytest.raises(ValidationError, match="AIAT_QWEN_API_KEY"):
        _agent(llm_provider="qwen", qwen_api_key=None)


def test_validator_openrouter_skips_provider_key_check() -> None:
    """gateway=openrouter — provider-specific key not required."""
    s = _agent(
        llm_gateway="openrouter",
        openrouter_api_key="or-test-key",
        openai_api_key=None,  # NOT required when gateway=openrouter
    )
    assert s.llm_gateway == "openrouter"


def test_validator_openrouter_missing_or_key_raises() -> None:
    with pytest.raises(ValidationError, match="AIAT_OPENROUTER_API_KEY"):
        _agent(
            llm_gateway="openrouter",
            openrouter_api_key=None,
            openai_api_key=None,
        )


# ---------------------------------------------------------------------------
# ContextOrchestratorSettings — least privilege
# ---------------------------------------------------------------------------


def test_orchestrator_settings_valid() -> None:
    s = _orchestrator()
    assert s.service_role == "context_orchestrator"
    assert s.network == "testnet"
    assert s.hard_timeout_seconds == 30
    assert s.cron_minute_offsets == [0, 15, 30, 45]


def test_orchestrator_has_no_llm_fields() -> None:
    s = _orchestrator()
    assert not hasattr(s, "llm_provider")
    assert not hasattr(s, "openai_api_key")
    assert not hasattr(s, "anthropic_api_key")
    assert not hasattr(s, "hl_wallet_private_key")


def test_orchestrator_rejects_extra_llm_key() -> None:
    """extra='forbid' enforces least privilege — no LLM keys can sneak in."""
    with pytest.raises(ValidationError):
        ContextOrchestratorSettings(
            _env_file=None,  # type: ignore[call-arg]
            **{**_ORCHESTRATOR_REQUIRED, "openai_api_key": "injected"},  # type: ignore[arg-type]
        )


def test_orchestrator_rejects_extra_wallet_key() -> None:
    with pytest.raises(ValidationError):
        ContextOrchestratorSettings(
            _env_file=None,  # type: ignore[call-arg]
            **{  # type: ignore[arg-type]
                **_ORCHESTRATOR_REQUIRED,
                "hl_wallet_private_key": "0x" + "0" * 64,
            },
        )


# ---------------------------------------------------------------------------
# load_settings dispatcher
# ---------------------------------------------------------------------------


def test_load_settings_agent_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_settings dispatches to AgentSettings when role=agent."""
    monkeypatch.setenv("AIAT_SERVICE_ROLE", "agent")
    mock_instance = MagicMock(spec=AgentSettings)
    with patch("aiat.config.settings.AgentSettings", return_value=mock_instance) as mock_cls:
        result = load_settings()
    mock_cls.assert_called_once()
    assert result is mock_instance


def test_load_settings_orchestrator_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_settings dispatches to ContextOrchestratorSettings when role=context_orchestrator."""
    monkeypatch.setenv("AIAT_SERVICE_ROLE", "context_orchestrator")
    mock_instance = MagicMock(spec=ContextOrchestratorSettings)
    with patch(
        "aiat.config.settings.ContextOrchestratorSettings", return_value=mock_instance
    ) as mock_cls:
        result = load_settings()
    mock_cls.assert_called_once()
    assert result is mock_instance


def test_load_settings_invalid_role_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIAT_SERVICE_ROLE", "unknown")
    with pytest.raises(RuntimeError, match="AIAT_SERVICE_ROLE must be"):
        load_settings()


def test_load_settings_missing_role_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIAT_SERVICE_ROLE", raising=False)
    with pytest.raises(RuntimeError, match="AIAT_SERVICE_ROLE must be"):
        load_settings()
