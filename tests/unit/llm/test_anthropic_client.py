"""Tests for AnthropicClient (PRD §8.1)."""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiat.domain.schemas import LLMInvocationResult

_PRICING: dict[str, Decimal] = {
    "input": Decimal("3.00"),
    "output": Decimal("15.00"),
    "reasoning": Decimal("0.00"),
}

_VALID_DECISION: dict[str, Any] = {
    "portfolio_reasoning": "A" * 60,
    "risk_assessment": "B" * 40,
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


def _make_client() -> "Any":
    from aiat.llm.anthropic_client import AnthropicClient

    return AnthropicClient(
        api_key="test-key",
        model_name="claude-3-5-sonnet-20241022",
        temperature=Decimal("0.7"),
        pricing=_PRICING,
        max_tokens=4096,
    )


@pytest.mark.asyncio
async def test_anthropic_client_invoke_returns_result() -> None:
    from aiat.domain.schemas import TradeDecision

    client = _make_client()
    decision = TradeDecision.model_validate(_VALID_DECISION)

    with patch("aiat.llm.anthropic_client.invoke_structured", new_callable=AsyncMock) as mock_inv:
        mock_inv.return_value = (decision, False)
        result = await client.invoke("test prompt", timeout_seconds=10)

    assert isinstance(result, LLMInvocationResult)
    assert result.provider_snapshot == "anthropic"
    assert result.fallback_used is False


@pytest.mark.asyncio
async def test_anthropic_client_implements_abc() -> None:
    from aiat.llm.base import BaseLLMClient

    client = _make_client()
    assert isinstance(client, BaseLLMClient)
    assert client.provider == "anthropic"


def test_anthropic_no_top_p_no_seed() -> None:
    """Anthropic client does not expose top_p or seed (not supported by API)."""
    client = _make_client()
    # AnthropicClient does not store top_p or seed
    assert not hasattr(client, "_top_p") or client._top_p is None  # type: ignore[attr-defined]


# ── ADR-0023: provider-aware sampling — Opus 4.8+ is thinking-only, rejects temperature ──


def test_anthropic_omits_temperature_when_none() -> None:
    """temperature=None (default) must NOT be passed to ChatAnthropic (else HTTP 400, M5-T14)."""
    from aiat.llm.anthropic_client import AnthropicClient

    with patch("aiat.llm.anthropic_client.ChatAnthropic") as mock_chat:
        client = AnthropicClient(
            api_key="test-key",
            model_name="claude-opus-4-8",
            pricing=_PRICING,
            max_tokens=4096,
        )
    kwargs = mock_chat.call_args.kwargs
    assert "temperature" not in kwargs, "temperature must be omitted when None"
    assert kwargs["model"] == "claude-opus-4-8"
    assert client._temperature is None  # type: ignore[attr-defined]


def test_anthropic_passes_temperature_when_explicit() -> None:
    """A provider that DOES accept temperature still gets it (backward compatible)."""
    from aiat.llm.anthropic_client import AnthropicClient

    with patch("aiat.llm.anthropic_client.ChatAnthropic") as mock_chat:
        AnthropicClient(
            api_key="test-key",
            model_name="claude-3-5-sonnet-20241022",
            temperature=Decimal("0"),
            pricing=_PRICING,
            max_tokens=4096,
        )
    kwargs = mock_chat.call_args.kwargs
    assert kwargs["temperature"] == 0.0


@pytest.mark.asyncio
async def test_anthropic_invocation_result_temperature_none_when_omitted() -> None:
    """The audited LLMInvocationResult.temperature is None (the truth), not a fake 0."""
    from aiat.domain.schemas import TradeDecision
    from aiat.llm.anthropic_client import AnthropicClient

    decision = TradeDecision.model_validate(_VALID_DECISION)
    with patch("aiat.llm.anthropic_client.ChatAnthropic"):
        client = AnthropicClient(
            api_key="test-key",
            model_name="claude-opus-4-8",
            pricing=_PRICING,
        )
    with patch("aiat.llm.anthropic_client.invoke_structured", new_callable=AsyncMock) as mock_inv:
        mock_inv.return_value = (decision, False)
        result = await client.invoke("test prompt", timeout_seconds=10)
    assert result.temperature is None


# ── ADR-0026: A7 lightweight credential probe — raw ainvoke, NO structured output ──


@pytest.mark.asyncio
async def test_anthropic_ping_ok_with_nonempty_response() -> None:
    """ping() completes without raising when _llm.ainvoke returns non-empty content."""
    client = _make_client()
    client._llm = MagicMock()
    client._llm.ainvoke = AsyncMock(return_value=MagicMock(content="pong"))

    await client.ping(timeout_seconds=10)

    client._llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_anthropic_ping_raises_on_empty_response() -> None:
    """ping() raises RuntimeError('empty response') when content is empty."""
    client = _make_client()
    client._llm = MagicMock()
    client._llm.ainvoke = AsyncMock(return_value=MagicMock(content=None))

    with pytest.raises(RuntimeError, match="empty response"):
        await client.ping(timeout_seconds=10)


@pytest.mark.asyncio
async def test_anthropic_ping_propagates_timeout() -> None:
    """ping(timeout_seconds=N) passes N to asyncio.wait_for."""
    client = _make_client()
    client._llm = MagicMock()
    client._llm.ainvoke = AsyncMock(return_value=MagicMock(content="pong"))
    captured: dict[str, float] = {}

    async def _fake_wait_for(coro: Any, timeout: float) -> Any:
        captured["timeout"] = timeout
        return await coro

    with patch("asyncio.wait_for", side_effect=_fake_wait_for):
        await client.ping(timeout_seconds=77)

    assert captured["timeout"] == 77
