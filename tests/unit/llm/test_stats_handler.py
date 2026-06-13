"""Tests for StatsCallbackHandler (PRD §8.3, §9.2)."""

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.outputs import LLMResult

from aiat.llm.stats_handler import StatsCallbackHandler

_PRICING: dict[str, Decimal] = {
    "input": Decimal("1.25"),
    "output": Decimal("10.00"),
    "reasoning": Decimal("10.00"),
}


def _make_result_with_llm_output(llm_output: dict[str, Any]) -> LLMResult:
    return LLMResult(generations=[], llm_output=llm_output)


def _make_result_with_response_meta(meta: dict[str, Any]) -> LLMResult:
    """Simulate newer LangChain where usage is in generation response_metadata."""
    msg = MagicMock()
    msg.response_metadata = meta
    gen = MagicMock()
    gen.message = msg
    return LLMResult(generations=[[gen]])


# ---------------------------------------------------------------------------
# OpenAI native token_usage format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_openai_token_usage() -> None:
    handler = StatsCallbackHandler(pricing=_PRICING)
    result = _make_result_with_llm_output(
        {
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            }
        }
    )
    await handler.on_llm_end(result)
    assert handler.input_tokens == 100
    assert handler.output_tokens == 50
    assert handler.reasoning_tokens == 0
    assert handler.n_attempts == 1


@pytest.mark.asyncio
async def test_extract_openai_reasoning_tokens() -> None:
    """OpenAI o-series models expose reasoning_tokens in completion_tokens_details."""
    handler = StatsCallbackHandler(pricing=_PRICING)
    result = _make_result_with_llm_output(
        {
            "token_usage": {
                "prompt_tokens": 200,
                "completion_tokens": 80,
                "completion_tokens_details": {"reasoning_tokens": 40},
            }
        }
    )
    await handler.on_llm_end(result)
    assert handler.input_tokens == 200
    assert handler.output_tokens == 80
    assert handler.reasoning_tokens == 40


# ---------------------------------------------------------------------------
# Anthropic native format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_anthropic_usage() -> None:
    handler = StatsCallbackHandler(pricing=_PRICING)
    result = _make_result_with_llm_output(
        {
            "usage": {
                "input_tokens": 300,
                "output_tokens": 120,
            }
        }
    )
    await handler.on_llm_end(result)
    assert handler.input_tokens == 300
    assert handler.output_tokens == 120
    assert handler.reasoning_tokens == 0  # Anthropic has no separate reasoning billing


# ---------------------------------------------------------------------------
# DeepSeek-R1 via OpenAI-compatible: includes reasoning_tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_deepseek_r1_reasoning_tokens() -> None:
    handler = StatsCallbackHandler(pricing=_PRICING)
    result = _make_result_with_llm_output(
        {
            "usage": {
                "prompt_tokens": 150,
                "completion_tokens": 60,
                "reasoning_tokens": 30,
            }
        }
    )
    await handler.on_llm_end(result)
    assert handler.input_tokens == 150
    assert handler.output_tokens == 60
    assert handler.reasoning_tokens == 30


# ---------------------------------------------------------------------------
# Qwen via OpenAI-compatible: standard format, no reasoning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_qwen_usage_no_reasoning() -> None:
    handler = StatsCallbackHandler(pricing=_PRICING)
    result = _make_result_with_llm_output(
        {
            "usage": {
                "prompt_tokens": 80,
                "completion_tokens": 40,
            }
        }
    )
    await handler.on_llm_end(result)
    assert handler.input_tokens == 80
    assert handler.output_tokens == 40
    assert handler.reasoning_tokens == 0


# ---------------------------------------------------------------------------
# Multi-attempt aggregation (primary + fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_attempt_aggregation() -> None:
    handler = StatsCallbackHandler(pricing=_PRICING)
    usage1 = {"token_usage": {"prompt_tokens": 100, "completion_tokens": 50}}
    usage2 = {"token_usage": {"prompt_tokens": 110, "completion_tokens": 55}}
    r1 = _make_result_with_llm_output(usage1)
    r2 = _make_result_with_llm_output(usage2)
    await handler.on_llm_end(r1)
    await handler.on_llm_end(r2)
    assert handler.n_attempts == 2
    assert handler.input_tokens == 210
    assert handler.output_tokens == 105


# ---------------------------------------------------------------------------
# cost_usd calculation with Decimal precision (no float)
# ---------------------------------------------------------------------------


def test_cost_usd_decimal_precision() -> None:
    pricing: dict[str, Decimal] = {
        "input": Decimal("1.25"),
        "output": Decimal("10.00"),
        "reasoning": Decimal("10.00"),
    }
    handler = StatsCallbackHandler(pricing=pricing)
    handler.input_tokens = 1000
    handler.output_tokens = 500
    handler.reasoning_tokens = 0
    handler.n_attempts = 1

    event = handler.build_cost_event()
    expected_cost = Decimal("1000") * Decimal("1.25") / Decimal("1000000") + Decimal(
        "500"
    ) * Decimal("10.00") / Decimal("1000000")
    assert event.cost_usd == expected_cost
    assert isinstance(event.cost_usd, Decimal)
    # Verify no float arithmetic crept in
    assert event.input_tokens == 1000
    assert event.output_tokens == 500
    assert event.n_attempts == 1


def test_build_cost_event_n_attempts_min_1() -> None:
    handler = StatsCallbackHandler(pricing=_PRICING)
    # No on_llm_end called → n_attempts is 0 internally
    event = handler.build_cost_event()
    assert event.n_attempts >= 1  # invariant: always at least 1


def test_cost_usd_with_reasoning() -> None:
    pricing: dict[str, Decimal] = {
        "input": Decimal("0.55"),
        "output": Decimal("2.19"),
        "reasoning": Decimal("2.19"),
    }
    handler = StatsCallbackHandler(pricing=pricing)
    handler.input_tokens = 200
    handler.output_tokens = 100
    handler.reasoning_tokens = 50
    handler.n_attempts = 1

    event = handler.build_cost_event()
    expected = (
        Decimal("200") * Decimal("0.55") / Decimal("1000000")
        + Decimal("100") * Decimal("2.19") / Decimal("1000000")
        + Decimal("50") * Decimal("2.19") / Decimal("1000000")
    )
    assert event.cost_usd == expected


# ---------------------------------------------------------------------------
# Fallback: empty llm_output falls back gracefully to zeros
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_llm_output_returns_zeros() -> None:
    handler = StatsCallbackHandler(pricing=_PRICING)
    result = _make_result_with_llm_output({})
    await handler.on_llm_end(result)
    assert handler.input_tokens == 0
    assert handler.output_tokens == 0
    assert handler.reasoning_tokens == 0
    assert handler.n_attempts == 1
