"""Additional tests to reach 95% coverage on llm/ (M2-T13)."""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.exceptions import OutputParserException

from aiat.llm.exceptions import LLMError, LLMTimeoutError, LLMUnrecoverableError
from aiat.llm.structured import (
    _extract_json_balanced,
    _is_auth_error,
    _is_rate_limit_error,
    invoke_structured,
)

_PRICING: dict[str, Decimal] = {
    "input": Decimal("1.25"),
    "output": Decimal("10.00"),
    "reasoning": Decimal("0.00"),
}


def _make_stats_handler() -> Any:
    from aiat.llm.stats_handler import StatsCallbackHandler

    return StatsCallbackHandler(pricing=_PRICING)


# ---------------------------------------------------------------------------
# structured.py line 76: unexpected primary error (not parsing, rate, auth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_primary_error_raises_llm_error() -> None:
    """An unexpected exception (not timeout/rate/auth/parsing) raises LLMError."""
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.with_config.return_value.ainvoke = AsyncMock(
        side_effect=RuntimeError("unexpected internal error")
    )
    handler = _make_stats_handler()

    with pytest.raises(LLMError, match="unexpected primary error"):
        await invoke_structured(mock_llm, "prompt", timeout_seconds=10, stats_handler=handler)


# ---------------------------------------------------------------------------
# structured.py line 89: non-string content in fallback response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_non_string_content_raises_unrecoverable() -> None:
    """If fallback response has non-string content, LLMUnrecoverableError is raised."""
    mock_llm = MagicMock()
    # Primary: parsing error
    mock_llm.with_structured_output.return_value.with_config.return_value.ainvoke = AsyncMock(
        side_effect=OutputParserException("bad parse")
    )
    # Fallback: content is a list (not a string)
    msg = MagicMock()
    msg.content = [{"type": "text", "text": "..."}]
    mock_llm.with_config.return_value.ainvoke = AsyncMock(return_value=msg)
    handler = _make_stats_handler()

    with pytest.raises(LLMUnrecoverableError):
        await invoke_structured(mock_llm, "prompt", timeout_seconds=10, stats_handler=handler)


# ---------------------------------------------------------------------------
# structured.py line 93-96: fallback timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_timeout_raises_llm_timeout() -> None:
    """Timeout in fallback path raises LLMTimeoutError."""
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.with_config.return_value.ainvoke = AsyncMock(
        side_effect=OutputParserException("bad parse")
    )
    mock_llm.with_config.return_value.ainvoke = AsyncMock(side_effect=TimeoutError())
    handler = _make_stats_handler()

    with pytest.raises(LLMTimeoutError, match="fallback attempt"):
        await invoke_structured(mock_llm, "prompt", timeout_seconds=10, stats_handler=handler)


# ---------------------------------------------------------------------------
# _extract_json_balanced: IN_STRING_ESCAPE state (backslash in string)
# ---------------------------------------------------------------------------


def test_extract_json_balanced_escape_backslash_in_string() -> None:
    """Backslash-escape sequences inside JSON strings are handled correctly."""
    text = '{"path": "C:\\\\Users\\\\name"}'
    result = _extract_json_balanced(text)
    assert '"path"' in result


def test_extract_json_balanced_in_string_escape_state() -> None:
    """Coverage for IN_STRING_ESCAPE state transition."""
    text = '{"a": "value with \\\\ backslash and \\" quote"}'
    result = _extract_json_balanced(text)
    assert result.startswith("{")


# ---------------------------------------------------------------------------
# _is_rate_limit_error / _is_auth_error: string fallback branches
# ---------------------------------------------------------------------------


def test_is_rate_limit_error_quota_exceeded() -> None:
    assert _is_rate_limit_error(Exception("quota exceeded"))


def test_is_auth_error_403_forbidden() -> None:
    assert _is_auth_error(Exception("403 forbidden access"))


def test_is_auth_error_authentication_string() -> None:
    assert _is_auth_error(Exception("authentication failed"))


# ---------------------------------------------------------------------------
# stats_handler.py lines 91-107: fallback to generation response_metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_handler_generation_response_metadata_openai() -> None:
    """Coverage for the generation response_metadata fallback path (newer LangChain)."""
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    from aiat.llm.stats_handler import StatsCallbackHandler

    handler = StatsCallbackHandler(pricing=_PRICING)
    msg = AIMessage(
        content="decision",
        response_metadata={
            "token_usage": {
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "completion_tokens_details": {"reasoning_tokens": 10},
            }
        },
    )
    gen = ChatGeneration(message=msg, text="decision")
    result = LLMResult(generations=[[gen]])
    await handler.on_llm_end(result)
    assert handler.input_tokens == 50
    assert handler.output_tokens == 20
    assert handler.reasoning_tokens == 10


@pytest.mark.asyncio
async def test_stats_handler_generation_response_metadata_anthropic() -> None:
    """Coverage for Anthropic-style usage in generation response_metadata."""
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    from aiat.llm.stats_handler import StatsCallbackHandler

    handler = StatsCallbackHandler(pricing=_PRICING)
    msg = AIMessage(
        content="decision",
        response_metadata={"usage": {"input_tokens": 80, "output_tokens": 40}},
    )
    gen = ChatGeneration(message=msg, text="decision")
    result = LLMResult(generations=[[gen]])
    await handler.on_llm_end(result)
    assert handler.input_tokens == 80
    assert handler.output_tokens == 40


# ---------------------------------------------------------------------------
# openai_compatible_client.py: top_p and seed branches
# ---------------------------------------------------------------------------


def test_compatible_client_with_top_p() -> None:
    """Coverage for top_p branch (line 55-56)."""
    from aiat.llm.openai_compatible_client import (
        DEEPSEEK_BASE_URL,
        OpenAICompatibleClient,
    )

    client = OpenAICompatibleClient(
        api_key="key",
        model_name="some-model",
        base_url=DEEPSEEK_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_PRICING,
        top_p=Decimal("0.95"),
        provider_name="deepseek",
    )
    assert client._top_p == Decimal("0.95")


def test_compatible_client_with_seed() -> None:
    """Coverage for seed branch (line 57-58)."""
    from aiat.llm.openai_compatible_client import (
        DEEPSEEK_BASE_URL,
        OpenAICompatibleClient,
    )

    client = OpenAICompatibleClient(
        api_key="key",
        model_name="some-model",
        base_url=DEEPSEEK_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_PRICING,
        seed=42,
        provider_name="deepseek",
    )
    assert client._seed == 42
