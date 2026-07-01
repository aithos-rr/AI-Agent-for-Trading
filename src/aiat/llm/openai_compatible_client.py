"""OpenAICompatibleClient — OpenAI-compatible client for DeepSeek/Qwen/OpenRouter (PRD §8.1)."""

import time
from decimal import Decimal

from langchain_openai import ChatOpenAI

from aiat.domain.schemas import LLMInvocationResult
from aiat.llm.base import BaseLLMClient
from aiat.llm.stats_handler import StatsCallbackHandler
from aiat.llm.structured import invoke_structured

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenAICompatibleClient(BaseLLMClient):
    """OpenAI-compatible client for providers that expose the OpenAI API interface.

    Used for:
      - DeepSeek (base_url=DEEPSEEK_BASE_URL)
      - Qwen/DashScope (base_url=QWEN_BASE_URL)
      - OpenRouter dev gateway (base_url=OPENROUTER_BASE_URL, ADR-0008)
    """

    provider = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        base_url: str,
        temperature: Decimal,
        pricing: dict[str, Decimal],
        top_p: Decimal | None = None,
        max_tokens: int = 4096,
        seed: int | None = None,
        provider_name: str = "openai_compatible",
    ) -> None:
        self.model_name_api = model_name
        self.provider = provider_name
        self._pricing = pricing
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens
        self._seed = seed
        llm_kwargs: dict[str, object] = {
            "api_key": api_key,
            "model": model_name,
            "base_url": base_url,
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
        """Invoke the OpenAI-compatible endpoint with structured output + fallback."""
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

    async def ping(self, *, timeout_seconds: int = 30) -> None:
        import asyncio

        resp = await asyncio.wait_for(self._llm.ainvoke("ping"), timeout=timeout_seconds)
        content = getattr(resp, "content", None)
        if not content:
            raise RuntimeError(f"{self.provider} ping returned empty response")
