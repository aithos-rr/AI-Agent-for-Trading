"""Tests for OpenAIClient (PRD §8.1)."""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiat.domain.schemas import LLMInvocationResult

_PRICING: dict[str, Decimal] = {
    "input": Decimal("1.25"),
    "output": Decimal("10.00"),
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
    from aiat.llm.openai_client import OpenAIClient

    return OpenAIClient(
        api_key="test-key",
        model_name="gpt-4o",
        temperature=Decimal("0.7"),
        pricing=_PRICING,
        max_tokens=4096,
    )


@pytest.mark.asyncio
async def test_openai_client_invoke_returns_result() -> None:
    from aiat.domain.schemas import TradeDecision

    client = _make_client()
    decision = TradeDecision.model_validate(_VALID_DECISION)

    with patch("aiat.llm.openai_client.invoke_structured", new_callable=AsyncMock) as mock_inv:
        mock_inv.return_value = (decision, False)
        result = await client.invoke("test prompt", timeout_seconds=10)

    assert isinstance(result, LLMInvocationResult)
    assert result.provider_snapshot == "openai"
    assert result.model_name_api_snapshot == "gpt-4o"
    assert result.fallback_used is False
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_openai_client_invoke_fallback_used() -> None:
    from aiat.domain.schemas import TradeDecision

    client = _make_client()
    decision = TradeDecision.model_validate(_VALID_DECISION)

    with patch("aiat.llm.openai_client.invoke_structured", new_callable=AsyncMock) as mock_inv:
        mock_inv.return_value = (decision, True)
        result = await client.invoke("test prompt")

    assert result.fallback_used is True


@pytest.mark.asyncio
async def test_openai_client_implements_abc() -> None:
    from aiat.llm.base import BaseLLMClient

    client = _make_client()
    assert isinstance(client, BaseLLMClient)
    assert client.provider == "openai"


def test_openai_client_stores_nuisance_params() -> None:
    from aiat.llm.openai_client import OpenAIClient

    client = OpenAIClient(
        api_key="key",
        model_name="gpt-4o-mini",
        temperature=Decimal("0.5"),
        pricing=_PRICING,
        top_p=Decimal("0.9"),
        seed=42,
    )
    assert client._temperature == Decimal("0.5")
    assert client._top_p == Decimal("0.9")
    assert client._seed == 42


# ── ADR-0026: A7 lightweight credential probe — raw ainvoke, NO structured output ──


@pytest.mark.asyncio
async def test_openai_ping_ok_with_nonempty_response() -> None:
    """ping() completes without raising when _llm.ainvoke returns non-empty content."""
    client = _make_client()
    client._llm = MagicMock()
    client._llm.ainvoke = AsyncMock(return_value=MagicMock(content="pong"))

    await client.ping(timeout_seconds=10)

    client._llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_openai_ping_raises_on_empty_response() -> None:
    """ping() raises RuntimeError('empty response') when content is empty."""
    client = _make_client()
    client._llm = MagicMock()
    client._llm.ainvoke = AsyncMock(return_value=MagicMock(content=""))

    with pytest.raises(RuntimeError, match="empty response"):
        await client.ping(timeout_seconds=10)


@pytest.mark.asyncio
async def test_openai_ping_propagates_timeout() -> None:
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
