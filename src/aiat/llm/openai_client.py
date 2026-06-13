"""OpenAIClient — LLM client for OpenAI models via langchain-openai (PRD §8.1)."""

import time
from decimal import Decimal

from langchain_openai import ChatOpenAI

from aiat.domain.schemas import LLMInvocationResult
from aiat.llm.base import BaseLLMClient
from aiat.llm.stats_handler import StatsCallbackHandler
from aiat.llm.structured import invoke_structured


class OpenAIClient(BaseLLMClient):
    """OpenAI client using langchain-openai ChatOpenAI."""

    provider = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        temperature: Decimal,
        pricing: dict[str, Decimal],
        top_p: Decimal | None = None,
        max_tokens: int = 4096,
        seed: int | None = None,
    ) -> None:
        self.model_name_api = model_name
        self._pricing = pricing
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens
        self._seed = seed
        llm_kwargs: dict[str, object] = {
            "api_key": api_key,
            "model": model_name,
            "temperature": float(temperature),
            "max_tokens": max_tokens,
        }
        if top_p is not None:
            llm_kwargs["model_kwargs"] = {"top_p": float(top_p)}
        if seed is not None:
            llm_kwargs["seed"] = seed
        self._llm = ChatOpenAI(**llm_kwargs)  # type: ignore[arg-type]

    async def invoke(
        self,
        prompt: str,
        *,
        timeout_seconds: int = 90,
    ) -> LLMInvocationResult:
        """Invoke OpenAI with structured output + fallback."""
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
            top_p=self._top_p,
            max_tokens=self._max_tokens,
            seed=self._seed,
        )
