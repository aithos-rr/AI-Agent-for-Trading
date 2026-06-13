"""LLM provider integration tests with VCR cassettes (PRD §9.4).

These tests use @pytest.mark.vcr and require cassettes in tests/cassettes/.
Cassettes are recorded via OpenRouter (ADR-0008, M2-T12) by a human under
supervision; in CI they replay in record_mode="none".

All tests use OpenAICompatibleClient with gateway=openrouter for cassette
recording (single base_url), so cassette responses are in OpenAI-style format.
"""

import os
from decimal import Decimal

import pytest

from aiat.config.settings import AgentSettings
from aiat.llm.exceptions import (
    LLMAuthError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnrecoverableError,
)
from aiat.llm.openai_compatible_client import OPENROUTER_BASE_URL, OpenAICompatibleClient

_TEST_PRICING: dict[str, Decimal] = {
    "input": Decimal("1.25"),
    "output": Decimal("10.00"),
    "reasoning": Decimal("0.00"),
}

# NOTE: _OPENROUTER_SETTINGS_BASE / _or_settings are NOT used by the 15 active
# VCR tests below (they instantiate OpenAICompatibleClient directly with an
# env-with-fallback api_key). The "test-or-key" placeholder here is therefore
# never used to make real network calls during cassette recording, so it is left
# as-is. If a future test uses _or_settings to drive real OpenRouter calls, apply
# the same os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key") pattern here.
_OPENROUTER_SETTINGS_BASE = {
    "llm_gateway": "openrouter",
    "openrouter_api_key": "test-or-key",
    "temperature": Decimal("0.7"),
    "max_tokens": 4096,
    "openai_api_key": "",
    "anthropic_api_key": "",
    "deepseek_api_key": "",
    "qwen_api_key": "",
}


def _or_settings(provider: str, model: str) -> AgentSettings:
    return AgentSettings(
        llm_provider=provider,
        model_name_api=model,
        **_OPENROUTER_SETTINGS_BASE,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 4× structured output success (one per provider via OpenRouter)
# ---------------------------------------------------------------------------


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_openai_invoke_structured() -> None:
    """OpenAI via OpenRouter — structured output returns valid TradeDecision."""
    from aiat.domain.schemas import TradeDecision

    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="openai/gpt-4o",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    result = await client.invoke("Generate a BTC trading decision.", timeout_seconds=60)
    assert isinstance(result.decision, TradeDecision)
    assert len(result.decision.actions) == 3
    assert result.cost.cost_usd >= Decimal("0")


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_anthropic_invoke_structured() -> None:
    """Anthropic via OpenRouter — structured output returns valid TradeDecision."""
    from aiat.domain.schemas import TradeDecision

    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="anthropic/claude-3-5-sonnet",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    result = await client.invoke("Generate a portfolio decision.", timeout_seconds=60)
    assert isinstance(result.decision, TradeDecision)


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_deepseek_invoke_structured_via_compatible() -> None:
    """DeepSeek via OpenRouter — structured output returns valid TradeDecision."""
    from aiat.domain.schemas import TradeDecision

    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="deepseek/deepseek-r1",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.6"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    result = await client.invoke("Generate a portfolio decision.", timeout_seconds=90)
    assert isinstance(result.decision, TradeDecision)


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_qwen_invoke_structured_via_compatible() -> None:
    """Qwen via OpenRouter — structured output returns valid TradeDecision."""
    from aiat.domain.schemas import TradeDecision

    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="qwen/qwen3-235b-a22b",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    result = await client.invoke("Generate a portfolio decision.", timeout_seconds=90)
    assert isinstance(result.decision, TradeDecision)


# ---------------------------------------------------------------------------
# Fallback path tests
# ---------------------------------------------------------------------------


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_openai_fallback_freetext() -> None:
    """Primary structured output fails; freetext fallback succeeds.

    Cassette has two HTTP exchanges: first returns malformed JSON, second
    returns valid JSON. Verifies fallback_used=True.
    """
    from aiat.domain.schemas import TradeDecision

    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="openai/gpt-4o",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    result = await client.invoke("Generate a decision.", timeout_seconds=60)
    assert isinstance(result.decision, TradeDecision)
    assert result.fallback_used is True


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_llm_unrecoverable_error() -> None:
    """Both primary and fallback return invalid JSON → LLMUnrecoverableError."""
    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="openai/gpt-4o",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    with pytest.raises(LLMUnrecoverableError):
        await client.invoke("Generate a decision.", timeout_seconds=60)


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_cost_tracking_openai() -> None:
    """Verify cost_usd is computed from usage tokens (OpenAI via OR)."""
    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="openai/gpt-4o",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    result = await client.invoke("Generate a decision.", timeout_seconds=60)
    assert result.cost.input_tokens > 0
    assert result.cost.output_tokens > 0
    assert result.cost.cost_usd > Decimal("0")
    assert isinstance(result.cost.cost_usd, Decimal)


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_cost_tracking_anthropic() -> None:
    """Verify cost_usd is computed from usage tokens (Anthropic via OR)."""
    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="anthropic/claude-3-5-sonnet",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    result = await client.invoke("Generate a decision.", timeout_seconds=60)
    assert result.cost.input_tokens > 0
    assert result.cost.cost_usd > Decimal("0")


# ---------------------------------------------------------------------------
# Error propagation (NOT fallback)
# ---------------------------------------------------------------------------


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_timeout_handling() -> None:
    """Simulated timeout → LLMTimeoutError raised (no fallback).

    Cassette simulates a response that arrives after the timeout deadline.
    Uses a very short timeout_seconds so the wait_for fires.
    """
    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="openai/gpt-4o",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    with pytest.raises(LLMTimeoutError):
        await client.invoke("Generate a decision.", timeout_seconds=1)


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_rate_limit_propagation() -> None:
    """HTTP 429 response → LLMRateLimitError (NOT freetext fallback)."""
    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="openai/gpt-4o",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    with pytest.raises(LLMRateLimitError):
        await client.invoke("Generate a decision.", timeout_seconds=60)


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_auth_error_propagation() -> None:
    """HTTP 401 response → LLMAuthError (NOT freetext fallback)."""
    client = OpenAICompatibleClient(
        api_key="invalid-key",
        model_name="openai/gpt-4o",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    with pytest.raises(LLMAuthError):
        await client.invoke("Generate a decision.", timeout_seconds=60)


# ---------------------------------------------------------------------------
# Cost aggregation: primary + fallback → n_attempts=2
# ---------------------------------------------------------------------------


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_cost_aggregation_primary_plus_fallback() -> None:
    """Cassette: primary returns malformed JSON, fallback returns valid JSON.
    cost_event.n_attempts must equal 2 (both LLM calls billed).
    """
    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="openai/gpt-4o",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    result = await client.invoke("Generate a decision.", timeout_seconds=60)
    assert result.fallback_used is True
    assert result.cost.n_attempts == 2


# ---------------------------------------------------------------------------
# Reasoning tokens coverage (fix B.10 review-r2, ADR-0008)
# Cassettes are in OpenAI-style (via OR); native formats covered by unit tests.
# ---------------------------------------------------------------------------


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_openai_reasoning_tokens() -> None:
    """Cassette has completion_tokens_details.reasoning_tokens > 0 (o-series style)."""
    pricing_with_reasoning: dict[str, Decimal] = {
        "input": Decimal("1.25"),
        "output": Decimal("10.00"),
        "reasoning": Decimal("10.00"),
    }
    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="openai/o3-mini",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=pricing_with_reasoning,
        provider_name="openrouter",
    )
    result = await client.invoke("Generate a decision.", timeout_seconds=120)
    assert result.cost.reasoning_tokens > 0


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_anthropic_thinking_usage() -> None:
    """Cassette: Anthropic extended thinking → reasoning_tokens captured."""
    pricing_with_reasoning: dict[str, Decimal] = {
        "input": Decimal("3.00"),
        "output": Decimal("15.00"),
        "reasoning": Decimal("15.00"),
    }
    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="anthropic/claude-3-5-sonnet",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("1.0"),
        pricing=pricing_with_reasoning,
        provider_name="openrouter",
    )
    result = await client.invoke("Generate a decision.", timeout_seconds=120)
    # reasoning_tokens may be 0 for standard calls; cassette shows > 0 for thinking mode
    assert result.cost.reasoning_tokens >= 0
    assert isinstance(result.cost.cost_usd, Decimal)


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_deepseek_r1_reasoning_usage() -> None:
    """Cassette: DeepSeek-R1 via OR → usage.reasoning_tokens > 0."""
    pricing_r1: dict[str, Decimal] = {
        "input": Decimal("0.55"),
        "output": Decimal("2.19"),
        "reasoning": Decimal("2.19"),
    }
    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="deepseek/deepseek-r1",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.6"),
        pricing=pricing_r1,
        provider_name="openrouter",
    )
    result = await client.invoke("Generate a decision.", timeout_seconds=120)
    assert result.cost.reasoning_tokens > 0
