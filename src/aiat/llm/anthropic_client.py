"""AnthropicClient — LLM client for Anthropic models via langchain-anthropic (PRD §8.1)."""

import time
from decimal import Decimal

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
        temperature: Decimal,
        pricing: dict[str, Decimal],
        max_tokens: int = 4096,
    ) -> None:
        self.model_name_api = model_name
        self._pricing = pricing
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._llm = ChatAnthropic(  # type: ignore[call-arg]
            api_key=api_key,  # type: ignore[arg-type]
            model=model_name,
            temperature=float(temperature),
            max_tokens=max_tokens,
        )

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
