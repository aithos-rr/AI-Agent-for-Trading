"""Tests for OpenAICompatibleClient (PRD §8.1)."""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from aiat.domain.schemas import LLMInvocationResult
from aiat.llm.openai_compatible_client import (
    DEEPSEEK_BASE_URL,
    OPENROUTER_BASE_URL,
    QWEN_BASE_URL,
)

_PRICING: dict[str, Decimal] = {
    "input": Decimal("0.55"),
    "output": Decimal("2.19"),
    "reasoning": Decimal("2.19"),
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


def _make_deepseek_client() -> "Any":
    from aiat.llm.openai_compatible_client import OpenAICompatibleClient

    return OpenAICompatibleClient(
        api_key="test-key",
        model_name="deepseek-reasoner",
        base_url=DEEPSEEK_BASE_URL,
        temperature=Decimal("0.6"),
        pricing=_PRICING,
        provider_name="deepseek",
    )


def _make_qwen_client() -> "Any":
    from aiat.llm.openai_compatible_client import OpenAICompatibleClient

    return OpenAICompatibleClient(
        api_key="test-key",
        model_name="qwen3-flagship",
        base_url=QWEN_BASE_URL,
        temperature=Decimal("0.7"),
        pricing=_PRICING,
        provider_name="qwen",
    )


@pytest.mark.asyncio
async def test_deepseek_client_invoke_returns_result() -> None:
    from aiat.domain.schemas import TradeDecision

    client = _make_deepseek_client()
    decision = TradeDecision.model_validate(_VALID_DECISION)

    with patch(
        "aiat.llm.openai_compatible_client.invoke_structured", new_callable=AsyncMock
    ) as mock_inv:
        mock_inv.return_value = (decision, False)
        result = await client.invoke("test prompt", timeout_seconds=10)

    assert isinstance(result, LLMInvocationResult)
    assert result.provider_snapshot == "deepseek"
    assert result.fallback_used is False


@pytest.mark.asyncio
async def test_qwen_client_invoke_returns_result() -> None:
    from aiat.domain.schemas import TradeDecision

    client = _make_qwen_client()
    decision = TradeDecision.model_validate(_VALID_DECISION)

    with patch(
        "aiat.llm.openai_compatible_client.invoke_structured", new_callable=AsyncMock
    ) as mock_inv:
        mock_inv.return_value = (decision, False)
        result = await client.invoke("test prompt", timeout_seconds=10)

    assert isinstance(result, LLMInvocationResult)
    assert result.provider_snapshot == "qwen"


def test_compatible_client_implements_abc() -> None:
    from aiat.llm.base import BaseLLMClient

    client = _make_deepseek_client()
    assert isinstance(client, BaseLLMClient)


def test_base_urls_are_correct() -> None:
    assert "deepseek.com" in DEEPSEEK_BASE_URL
    assert "dashscope" in QWEN_BASE_URL
    assert "openrouter.ai" in OPENROUTER_BASE_URL


def test_compatible_client_uses_base_url_override() -> None:
    """The client stores the base_url and uses it when creating ChatOpenAI."""
    from aiat.llm.openai_compatible_client import OpenAICompatibleClient

    client = OpenAICompatibleClient(
        api_key="key",
        model_name="custom-model",
        base_url="https://custom.provider/v1",
        temperature=Decimal("0.5"),
        pricing=_PRICING,
        provider_name="custom",
    )
    # The underlying ChatOpenAI should have used the base_url
    assert client.provider == "custom"
    assert client.model_name_api == "custom-model"
