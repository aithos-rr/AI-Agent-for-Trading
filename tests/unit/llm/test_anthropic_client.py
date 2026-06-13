"""Tests for AnthropicClient (PRD §8.1)."""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

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
