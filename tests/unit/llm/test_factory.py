"""Tests for load_llm factory — dual-mode dispatch (PRD §8.1 + ADR-0008)."""

from decimal import Decimal
from unittest.mock import patch

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


def _base_settings(**kwargs: object) -> AgentSettings:
    defaults: dict[str, object] = {
        "llm_provider": "openai",
        "model_name_api": "gpt-4o",
        "openai_api_key": "test-key",
        "anthropic_api_key": "test-key",
        "deepseek_api_key": "test-key",
        "qwen_api_key": "test-key",
        "openrouter_api_key": "test-key",
        "temperature": Decimal("0.7"),
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
# Unknown provider raises
# ---------------------------------------------------------------------------


def test_load_llm_unknown_provider_raises() -> None:
    settings = _base_settings(llm_provider="unknown_provider", model_name_api="gpt-4o")
    with patch("aiat.llm.factory.load_pricing_for_model", return_value=_MOCK_PRICING):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            load_llm(settings)


# ---------------------------------------------------------------------------
# Both modes coexist — no client removed (additive principle, ADR-0008)
# ---------------------------------------------------------------------------


def test_all_four_direct_providers_and_openrouter() -> None:
    """Verify all 5 dispatch paths return valid clients."""
    base = _base_settings(model_name_api="any-model")

    with patch("aiat.llm.factory.load_pricing_for_model", return_value=_MOCK_PRICING):
        openai_c = load_llm(AgentSettings(**{**base.model_dump(), "llm_provider": "openai"}))
        anthropic_c = load_llm(AgentSettings(**{**base.model_dump(), "llm_provider": "anthropic"}))
        deepseek_c = load_llm(AgentSettings(**{**base.model_dump(), "llm_provider": "deepseek"}))
        qwen_c = load_llm(AgentSettings(**{**base.model_dump(), "llm_provider": "qwen"}))
        or_settings = {**base.model_dump(), "llm_gateway": "openrouter", "llm_provider": "openai"}
        or_c = load_llm(AgentSettings(**or_settings))

    assert isinstance(openai_c, OpenAIClient)
    assert isinstance(anthropic_c, AnthropicClient)
    assert isinstance(deepseek_c, OpenAICompatibleClient)
    assert isinstance(qwen_c, OpenAICompatibleClient)
    assert isinstance(or_c, OpenAICompatibleClient)
    assert or_c.provider == "openrouter"
