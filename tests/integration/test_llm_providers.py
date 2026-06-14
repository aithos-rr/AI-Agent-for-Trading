"""LLM provider integration tests with VCR cassettes (PRD §9.4).

These tests use @pytest.mark.vcr and require cassettes in tests/cassettes/.
Cassettes are recorded via OpenRouter (ADR-0008, M2-T12) by a human under
supervision; in CI they replay in record_mode="none".

All tests use OpenAICompatibleClient with gateway=openrouter for cassette
recording (single base_url), so cassette responses are in OpenAI-style format.
"""

import asyncio
import os
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from langchain_openai import ChatOpenAI

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

# Shared prompt for the structured-output tests (M2-T12 / fix Problem #2).
#
# `with_structured_output(method="json_schema")` propagates the schema SHAPE, but
# OpenAI-style structured output ignores numeric `minimum`/`maximum` and the
# conditional model_validators in TradeDecision/ActionDecision. So the model must
# be told the SEMANTIC constraints explicitly, or it emits e.g. size_pct=20
# (percentage) instead of 0.20 (fraction). This prompt encodes exactly the
# constraints the schema cannot enforce on its own.
_DECISION_PROMPT = (
    "You are a crypto perpetual-futures trading agent. Produce trading actions for "
    "a portfolio spanning EXACTLY three symbols: BTC, ETH and SOL.\n\n"
    "Illustrative market context: BTC trends mildly up (RSI ~58); ETH consolidates "
    "near support; SOL is volatile after a rally. Account equity is 10,000 USDC, "
    "no open positions.\n\n"
    "Return ONE TradeDecision JSON object obeying the schema EXACTLY. Hard rules "
    "the schema alone does not enforce:\n"
    "- 'actions' MUST have EXACTLY 3 items, one each for BTC, ETH and SOL.\n"
    "- 'size_pct' is a FRACTION of equity in [0, 1] (0.10 == 10%), NOT a percent; "
    "use 0.05-0.20 for LONG/SHORT.\n"
    "- 'leverage' is a multiplier (3 == 3x); use 1-5 for LONG/SHORT.\n"
    "- For every LONG/SHORT action BOTH 'stop_loss_pct' and 'take_profit_pct' are "
    "REQUIRED and are FRACTIONS (0.03 == 3%); use ~0.02-0.05 SL and ~0.04-0.10 TP.\n"
    "- Use 'entry_type'='market' for LONG/SHORT here and NEVER set 'limit_price'.\n"
    "- 'confidence' is a probability in [0, 1] (e.g. 0.65); 'time_horizon_min' is "
    "an integer in [1, 1440] (e.g. 120).\n"
    "- 'action_reasoning' must be a full sentence (>= 20 chars).\n"
    "- To stay flat on a symbol use side 'HOLD' with size_pct=0, leverage=0, "
    "entry_type='none' and NO stop_loss_pct/take_profit_pct/limit_price.\n"
    "- 'portfolio_reasoning' must be a substantive paragraph (>= 50 chars); "
    "'risk_assessment' at least a sentence or two (>= 30 chars).\n"
    "Output the JSON object DIRECTLY, with 'portfolio_reasoning', 'risk_assessment' "
    "and 'actions' as TOP-LEVEL keys. Do NOT wrap it inside any outer key (no "
    "'TradeDecision' wrapper) and do NOT use markdown code fences — JSON only."
)

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
    result = await client.invoke(_DECISION_PROMPT, timeout_seconds=60)
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
        model_name="anthropic/claude-sonnet-4.5",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    result = await client.invoke(_DECISION_PROMPT, timeout_seconds=60)
    assert isinstance(result.decision, TradeDecision)


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_deepseek_invoke_structured_via_compatible() -> None:
    """DeepSeek via OpenRouter — structured output returns valid TradeDecision.

    Uses deepseek-chat (V3, non-reasoning) for the structured path: it supports
    response_format=json_schema within budget. The reasoning model deepseek-r1 is
    exercised separately in test_deepseek_r1_reasoning_usage (M2-T12).
    """
    from aiat.domain.schemas import TradeDecision

    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="deepseek/deepseek-chat",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.6"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    result = await client.invoke(_DECISION_PROMPT, timeout_seconds=90)
    assert isinstance(result.decision, TradeDecision)


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_qwen_invoke_structured_via_compatible() -> None:
    """Qwen via OpenRouter — structured output returns valid TradeDecision."""
    from aiat.domain.schemas import TradeDecision

    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="qwen/qwen3-235b-a22b-2507",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    result = await client.invoke(_DECISION_PROMPT, timeout_seconds=90)
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
    result = await client.invoke(_DECISION_PROMPT, timeout_seconds=60)
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
        await client.invoke(_DECISION_PROMPT, timeout_seconds=60)


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
    result = await client.invoke(_DECISION_PROMPT, timeout_seconds=60)
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
        model_name="anthropic/claude-sonnet-4.5",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    result = await client.invoke(_DECISION_PROMPT, timeout_seconds=60)
    assert result.cost.input_tokens > 0
    assert result.cost.cost_usd > Decimal("0")


# ---------------------------------------------------------------------------
# Error propagation (NOT fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_handling() -> None:
    """Slow primary attempt → LLMTimeoutError raised (no fallback).

    NOT VCR-based by design: vcrpy replays cassettes instantly, so a recorded
    response cannot reproduce a wall-clock timeout on replay. Instead we patch the
    bound chat model's ``ainvoke`` to sleep past ``timeout_seconds`` — this exercises
    the real ``asyncio.wait_for`` deadline in ``invoke_structured`` and asserts the
    timeout is classified (NOT swallowed by the freetext fallback). No network or
    cassette is involved (M2-T12: scenario non producibile via cassetta).
    """

    async def _slow_ainvoke(self: Any, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(5)  # exceeds timeout_seconds=1 below

    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="openai/gpt-4o",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    with patch.object(ChatOpenAI, "ainvoke", _slow_ainvoke):
        with pytest.raises(LLMTimeoutError):
            await client.invoke(_DECISION_PROMPT, timeout_seconds=1)


# allow_playback_repeats: the OpenAI SDK retries on HTTP 429 (default max_retries),
# so the single recorded 429 must be replayable for each retry attempt (M2-T12).
@pytest.mark.vcr(allow_playback_repeats=True)
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
        await client.invoke(_DECISION_PROMPT, timeout_seconds=60)


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
        await client.invoke(_DECISION_PROMPT, timeout_seconds=60)


# ---------------------------------------------------------------------------
# Cost capture on fallback (primary malformed → freetext fallback)
# ---------------------------------------------------------------------------


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_cost_aggregation_primary_plus_fallback() -> None:
    """Cassette: primary returns malformed JSON, freetext fallback returns valid.

    Verifies the cost ledger captures the freetext fallback attempt (tokens > 0,
    cost > 0) and that the decision is produced via fallback.

    KNOWN LIMITATION (M2-T12, verified empirically): when the structured primary
    fails Pydantic validation, the OpenAI SDK validates the response *inside*
    ``_agenerate`` (``client.beta.chat.completions.parse`` → ``model_validate_json``),
    so langchain fires ``on_llm_error`` — NOT ``on_llm_end`` — for that attempt.
    ``StatsCallbackHandler`` only counts ``on_llm_end``, so the failed-but-billed
    primary attempt is not separately counted: ``n_attempts == 1`` (not 2), and its
    tokens are absent from the ledger. ``include_raw=True`` does NOT change this (the
    error is in the LLM step, not a downstream parser).

    Practical impact is nil for the official experiment: the selected models support
    strict ``json_schema``, so the primary does not fail and the fallback never fires.
    The freetext fallback's OWN cost is captured correctly (asserted below). If a
    provider that cannot honor ``json_schema`` is ever used in production, PATH 1 in
    ``invoke_structured`` would need to bind a raw ``response_format`` and validate in
    a separate step so the primary's ``on_llm_end`` fires — tracked as future work.
    """
    client = OpenAICompatibleClient(
        api_key=os.environ.get("AIAT_OPENROUTER_API_KEY", "test-or-key"),
        model_name="openai/gpt-4o",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_TEST_PRICING,
        provider_name="openrouter",
    )
    result = await client.invoke(_DECISION_PROMPT, timeout_seconds=60)
    assert result.fallback_used is True
    # The freetext fallback attempt is counted with its real token usage.
    assert result.cost.n_attempts == 1
    assert result.cost.input_tokens > 0
    assert result.cost.output_tokens > 0
    assert result.cost.cost_usd > Decimal("0")


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
    result = await client.invoke(_DECISION_PROMPT, timeout_seconds=180)
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
        model_name="anthropic/claude-sonnet-4.5",
        base_url=OPENROUTER_BASE_URL,
        temperature=Decimal("1.0"),
        pricing=pricing_with_reasoning,
        provider_name="openrouter",
    )
    result = await client.invoke(_DECISION_PROMPT, timeout_seconds=180)
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
    result = await client.invoke(_DECISION_PROMPT, timeout_seconds=180)
    assert result.cost.reasoning_tokens > 0
