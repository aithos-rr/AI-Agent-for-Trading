"""AnthropicClient — LLM client for Anthropic models via langchain-anthropic (PRD §8.1)."""

import time
from decimal import Decimal
from typing import Any

from langchain_anthropic import ChatAnthropic

from aiat.domain.schemas import LLMInvocationResult
from aiat.llm.base import BaseLLMClient
from aiat.llm.stats_handler import StatsCallbackHandler
from aiat.llm.structured import invoke_structured


class AnthropicClient(BaseLLMClient):
    """Anthropic client using langchain-anthropic ChatAnthropic."""

    provider = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        temperature: Decimal | None = None,
        pricing: dict[str, Decimal],
        max_tokens: int = 4096,
    ) -> None:
        self.model_name_api = model_name
        self._pricing = pricing
        self._temperature = temperature
        self._max_tokens = max_tokens
        # Pass `temperature` ONLY when set: current Anthropic models (Opus 4.8+) are
        # thinking-only and reject the parameter with HTTP 400 (discovered M5-T14). When
        # omitted, langchain-anthropic does not inject it into the payload. See ADR-0023.
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "model": model_name,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            kwargs["temperature"] = float(temperature)
        self._llm = ChatAnthropic(**kwargs)

    async def invoke(
        self,
        prompt: str,
        *,
        timeout_seconds: int = 90,
    ) -> LLMInvocationResult:
        """Invoke Anthropic with structured output + fallback."""
        handler = StatsCallbackHandler(pricing=self._pricing)
        t0 = time.monotonic()
        decision, fallback_used = await invoke_structured(
            self._llm,
            prompt,
            timeout_seconds=timeout_seconds,
            stats_handler=handler,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        cost = handler.build_cost_event()
        return LLMInvocationResult(
            decision=decision,
            cost=cost,
            latency_ms=latency_ms,
            raw_response_id=None,
            raw_payload={},
            fallback_used=fallback_used,
            provider_snapshot=self.provider,
            model_name_api_snapshot=self.model_name_api,
            temperature=self._temperature,
            top_p=None,
            max_tokens=self._max_tokens,
            seed=None,
        )

    async def ping(self, *, timeout_seconds: int = 30) -> None:
        import asyncio

        resp = await asyncio.wait_for(self._llm.ainvoke("ping"), timeout=timeout_seconds)
        content = getattr(resp, "content", None)
        if not content:
            raise RuntimeError(f"{self.provider} ping returned empty response")
