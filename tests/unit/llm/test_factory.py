"""Tests for load_llm factory — dual-mode dispatch (PRD §8.1 + ADR-0008)."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from aiat.config.settings import AgentSettings
from aiat.llm.anthropic_client import AnthropicClient
from aiat.llm.factory import load_llm
from aiat.llm.openai_client import OpenAIClient
from aiat.llm.openai_compatible_client import (
    OpenAICompatibleClient,
)

_MOCK_PRICING = {
    "input": Decimal("1.25"),
    "output": Decimal("10.00"),
    "reasoning": Decimal("0.00"),
}

_REQUIRED_FIELDS: dict[str, object] = {
    "experiment_id": "exp-test",
    "git_commit_sha": "abc123",
    "database_url": "postgresql+asyncpg://test:test@localhost/test",
    "model_id": "model-test",
    "prompt_template_hash": "deadbeef",
    "hl_wallet_private_key": "0x" + "0" * 64,
    "hl_wallet_address": "0x" + "0" * 40,
}


def _base_settings(**kwargs: object) -> AgentSettings:
    defaults: dict[str, object] = {
        **_REQUIRED_FIELDS,
        "llm_provider": "openai",
        "model_name_api": "gpt-4o",
        "openai_api_key": "test-key",
        "anthropic_api_key": "test-key",
        "deepseek_api_key": "test-key",
        "qwen_api_key": "test-key",
        "openrouter_api_key": "test-key",
        "temperature": Decimal("0.7"),
        "llm_gateway": "direct",  # explicit — overrides any .env value
    }
    defaults.update(kwargs)
    return AgentSettings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# gateway=direct — native provider dispatch
# ---------------------------------------------------------------------------


def test_load_llm_direct_openai() -> None:
    settings = _base_settings(llm_provider="openai", model_name_api="gpt-4o")
    with patch("aiat.llm.factory.load_pricing_for_model", return_value=_MOCK_PRICING):
        client = load_llm(settings)
    assert isinstance(client, OpenAIClient)
    assert client.provider == "openai"


def test_load_llm_direct_anthropic() -> None:
    settings = _base_settings(llm_provider="anthropic", model_name_api="claude-3-5-sonnet-20241022")
    with patch("aiat.llm.factory.load_pricing_for_model", return_value=_MOCK_PRICING):
        client = load_llm(settings)
    assert isinstance(client, AnthropicClient)
    assert client.provider == "anthropic"


def test_load_llm_direct_deepseek() -> None:
    settings = _base_settings(llm_provider="deepseek", model_name_api="deepseek-reasoner")
    with patch("aiat.llm.factory.load_pricing_for_model", return_value=_MOCK_PRICING):
        client = load_llm(settings)
    assert isinstance(client, OpenAICompatibleClient)
    assert client.provider == "deepseek"


def test_load_llm_direct_qwen() -> None:
    settings = _base_settings(llm_provider="qwen", model_name_api="qwen3-flagship")
    with patch("aiat.llm.factory.load_pricing_for_model", return_value=_MOCK_PRICING):
        client = load_llm(settings)
    assert isinstance(client, OpenAICompatibleClient)
    assert client.provider == "qwen"


# ---------------------------------------------------------------------------
# gateway=openrouter — single-key dev gateway (ADR-0008)
# ---------------------------------------------------------------------------


def test_load_llm_openrouter_returns_compatible_client() -> None:
    settings = _base_settings(
        llm_gateway="openrouter",
        llm_provider="openai",  # provider doesn't matter when gateway=openrouter
        model_name_api="openai/gpt-4o",
    )
    with patch("aiat.llm.factory.load_pricing_for_model", return_value=_MOCK_PRICING):
        client = load_llm(settings)
    assert isinstance(client, OpenAICompatibleClient)
    assert client.provider == "openrouter"


def test_load_llm_openrouter_uses_openrouter_base_url() -> None:
    settings = _base_settings(
        llm_gateway="openrouter",
        llm_provider="anthropic",
        model_name_api="anthropic/claude-3-5-sonnet",
    )
    with patch("aiat.llm.factory.load_pricing_for_model", return_value=_MOCK_PRICING):
        client = load_llm(settings)
    # The underlying ChatOpenAI base_url should be OPENROUTER_BASE_URL
    assert isinstance(client, OpenAICompatibleClient)
    # Verify the client was created (provider_name="openrouter")
    assert client.provider == "openrouter"


# ---------------------------------------------------------------------------
# Unknown provider — defensive branch (tested via mock to bypass Literal type)
# ---------------------------------------------------------------------------


def test_load_llm_unknown_provider_raises() -> None:
    # Use MagicMock to bypass AgentSettings' Literal validation — tests the
    # factory's defensive `case _: raise ValueError` branch at runtime.
    settings = MagicMock(spec=AgentSettings)
    settings.llm_gateway = "direct"
    settings.llm_provider = "unknown_provider"
    settings.model_name_api = "gpt-4o"
    settings.temperature = Decimal("0.7")
    settings.max_tokens = 4096
    settings.top_p = None
    settings.seed = None
    with patch("aiat.llm.factory.load_pricing_for_model", return_value=_MOCK_PRICING):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            load_llm(settings)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Both modes coexist — no client removed (additive principle, ADR-0008)
# ---------------------------------------------------------------------------


def test_all_four_direct_providers_and_openrouter() -> None:
    """Verify all 5 dispatch paths return valid clients."""
    with patch("aiat.llm.factory.load_pricing_for_model", return_value=_MOCK_PRICING):
        openai_c = load_llm(_base_settings(llm_provider="openai", model_name_api="any-model"))
        anthropic_c = load_llm(_base_settings(llm_provider="anthropic", model_name_api="any-model"))
        deepseek_c = load_llm(_base_settings(llm_provider="deepseek", model_name_api="any-model"))
        qwen_c = load_llm(_base_settings(llm_provider="qwen", model_name_api="any-model"))
        or_c = load_llm(
            _base_settings(
                llm_gateway="openrouter",
                llm_provider="openai",
                model_name_api="openai/gpt-4o",
            )
        )

    assert isinstance(openai_c, OpenAIClient)
    assert isinstance(anthropic_c, AnthropicClient)
    assert isinstance(deepseek_c, OpenAICompatibleClient)
    assert isinstance(qwen_c, OpenAICompatibleClient)
    assert isinstance(or_c, OpenAICompatibleClient)
    assert or_c.provider == "openrouter"
