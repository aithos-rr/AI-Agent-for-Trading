"""Tests for invoke_structured + _extract_json_balanced (PRD §8.2, §9.2)."""

import json
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from aiat.llm.exceptions import (
    LLMAuthError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnrecoverableError,
)
from aiat.llm.structured import (
    _extract_json_balanced,
    _is_auth_error,
    _is_parsing_error,
    _is_rate_limit_error,
    invoke_structured,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRICING: dict[str, Decimal] = {
    "input": Decimal("1.25"),
    "output": Decimal("10.00"),
    "reasoning": Decimal("10.00"),
}

_VALID_DECISION_DICT: dict[str, Any] = {
    "portfolio_reasoning": "A" * 60,  # min 50 chars
    "risk_assessment": "B" * 40,  # min 30 chars
    "actions": [
        {
            "symbol": "BTC",
            "side": "LONG",
            "size_pct": "0.10",
            "leverage": "2",
            "entry_type": "market",
            "stop_loss_pct": "0.05",
            "take_profit_pct": "0.10",
            "confidence": "0.8",
            "time_horizon_min": 60,
            "action_reasoning": "Strong uptrend observed with volume confirmation",
        },
        {
            "symbol": "ETH",
            "side": "HOLD",
            "size_pct": "0.00",
            "leverage": "0",
            "entry_type": "none",
            "confidence": "0.5",
            "time_horizon_min": 60,
            "action_reasoning": "Neutral signals, maintaining current position",
        },
        {
            "symbol": "SOL",
            "side": "HOLD",
            "size_pct": "0.00",
            "leverage": "0",
            "entry_type": "none",
            "confidence": "0.5",
            "time_horizon_min": 60,
            "action_reasoning": "Neutral signals, maintaining current position",
        },
    ],
}


def _make_stats_handler() -> Any:
    from aiat.llm.stats_handler import StatsCallbackHandler

    return StatsCallbackHandler(pricing=_PRICING)


def _make_structured_success_mock(return_value: Any) -> MagicMock:
    mock_llm = MagicMock()
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=return_value)
    mock_llm.with_structured_output.return_value.with_config.return_value = chain
    return mock_llm


def _make_primary_fail_fallback_ok_mock(fallback_content: str) -> MagicMock:
    """Primary raises OutputParserException; fallback returns valid JSON string."""
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.with_config.return_value.ainvoke = AsyncMock(
        side_effect=OutputParserException("failed to parse")
    )
    msg = MagicMock()
    msg.content = fallback_content
    mock_llm.with_config.return_value.ainvoke = AsyncMock(return_value=msg)
    return mock_llm


def _make_both_fail_mock(fallback_content: str = "not json at all") -> MagicMock:
    """Both primary and fallback fail."""
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.with_config.return_value.ainvoke = AsyncMock(
        side_effect=OutputParserException("primary parse error")
    )
    bad_msg = MagicMock()
    bad_msg.content = fallback_content
    mock_llm.with_config.return_value.ainvoke = AsyncMock(return_value=bad_msg)
    return mock_llm


# ---------------------------------------------------------------------------
# _extract_json_balanced tests
# ---------------------------------------------------------------------------


def test_extract_json_balanced_simple() -> None:
    result = _extract_json_balanced('{"key": "value"}')
    assert json.loads(result) == {"key": "value"}


def test_extract_json_balanced_nested() -> None:
    result = _extract_json_balanced('{"outer": {"inner": 1}}')
    assert json.loads(result) == {"outer": {"inner": 1}}


def test_extract_json_balanced_with_prose_surrounding() -> None:
    text = 'Here is the JSON: {"key": "value"} and some trailing text.'
    result = _extract_json_balanced(text)
    assert json.loads(result) == {"key": "value"}


def test_extract_json_balanced_brace_in_string() -> None:
    text = '{"note": "contains {braces} inside"}'
    result = _extract_json_balanced(text)
    assert json.loads(result) == {"note": "contains {braces} inside"}


def test_extract_json_balanced_escaped_quote_in_string() -> None:
    text = '{"msg": "say \\"hello\\""}'
    result = _extract_json_balanced(text)
    assert json.loads(result) == {"msg": 'say "hello"'}


def test_extract_json_balanced_no_json_raises() -> None:
    with pytest.raises(ValueError, match="No balanced JSON object found"):
        _extract_json_balanced("no json here at all")


def test_extract_json_balanced_unbalanced_raises() -> None:
    with pytest.raises(ValueError):
        _extract_json_balanced('{"unclosed"')


# ---------------------------------------------------------------------------
# _is_parsing_error / _is_rate_limit_error / _is_auth_error
# ---------------------------------------------------------------------------


def test_is_parsing_error_output_parser_exception() -> None:
    e = OutputParserException("bad output")
    assert _is_parsing_error(e)


def test_is_parsing_error_pydantic_validation_error() -> None:
    try:
        from pydantic import BaseModel

        class M(BaseModel):
            x: int

        M(x="not_an_int")  # type: ignore[arg-type]
    except ValidationError as e:
        assert _is_parsing_error(e)


def test_is_parsing_error_json_decode() -> None:
    try:
        json.loads("{bad}")
    except json.JSONDecodeError as e:
        assert _is_parsing_error(e)


def test_is_parsing_error_plain_exception_is_false() -> None:
    assert not _is_parsing_error(RuntimeError("generic"))


def test_is_rate_limit_error_string_match() -> None:
    assert _is_rate_limit_error(Exception("rate limit exceeded"))
    assert _is_rate_limit_error(Exception("HTTP 429 too many requests"))
    assert not _is_rate_limit_error(Exception("some other error"))


def test_is_auth_error_string_match() -> None:
    assert _is_auth_error(Exception("401 unauthorized"))
    assert _is_auth_error(Exception("invalid api key"))
    assert not _is_auth_error(Exception("network error"))


# ---------------------------------------------------------------------------
# invoke_structured — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_structured_success_no_fallback() -> None:
    from aiat.domain.schemas import TradeDecision

    decision = TradeDecision.model_validate(_VALID_DECISION_DICT)
    mock_llm = _make_structured_success_mock(decision)
    handler = _make_stats_handler()

    result, fallback_used = await invoke_structured(
        mock_llm, "prompt", timeout_seconds=10, stats_handler=handler
    )
    assert isinstance(result, TradeDecision)
    assert fallback_used is False


# ---------------------------------------------------------------------------
# invoke_structured — fallback after parsing failure (PATH 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_structured_fallback_after_primary_failure() -> None:
    from aiat.domain.schemas import TradeDecision

    valid_json = json.dumps(_VALID_DECISION_DICT)
    mock_llm = _make_primary_fail_fallback_ok_mock(f"Here is the result:\n{valid_json}\n")
    handler = _make_stats_handler()

    result, fallback_used = await invoke_structured(
        mock_llm, "prompt", timeout_seconds=10, stats_handler=handler
    )
    assert isinstance(result, TradeDecision)
    assert fallback_used is True


# ---------------------------------------------------------------------------
# invoke_structured — unrecoverable (both paths fail)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_structured_unrecoverable_when_both_fail() -> None:
    mock_llm = _make_both_fail_mock("not json at all")
    handler = _make_stats_handler()

    with pytest.raises(LLMUnrecoverableError):
        await invoke_structured(mock_llm, "prompt", timeout_seconds=10, stats_handler=handler)


# ---------------------------------------------------------------------------
# invoke_structured — timeout propagated (not fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_structured_timeout_raises_timeout_error() -> None:

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.with_config.return_value.ainvoke = AsyncMock(
        side_effect=TimeoutError()
    )
    handler = _make_stats_handler()

    with pytest.raises(LLMTimeoutError):
        await invoke_structured(mock_llm, "prompt", timeout_seconds=10, stats_handler=handler)


# ---------------------------------------------------------------------------
# invoke_structured — rate limit / auth are NOT fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_structured_rate_limit_not_fallback() -> None:
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.with_config.return_value.ainvoke = AsyncMock(
        side_effect=Exception("rate limit exceeded")
    )
    handler = _make_stats_handler()

    with pytest.raises(LLMRateLimitError):
        await invoke_structured(mock_llm, "prompt", timeout_seconds=10, stats_handler=handler)


@pytest.mark.asyncio
async def test_invoke_structured_auth_error_not_fallback() -> None:
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.with_config.return_value.ainvoke = AsyncMock(
        side_effect=Exception("401 unauthorized")
    )
    handler = _make_stats_handler()

    with pytest.raises(LLMAuthError):
        await invoke_structured(mock_llm, "prompt", timeout_seconds=10, stats_handler=handler)
