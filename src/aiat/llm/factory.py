"""LLM factory — dual-mode dispatch (PRD §8.1 + ADR-0008)."""

from aiat.config.pricing import load_pricing_for_model
from aiat.config.settings import AgentSettings
from aiat.llm.anthropic_client import AnthropicClient
from aiat.llm.base import BaseLLMClient
from aiat.llm.openai_client import OpenAIClient
from aiat.llm.openai_compatible_client import (
    DEEPSEEK_BASE_URL,
    OPENROUTER_BASE_URL,
    QWEN_BASE_URL,
    OpenAICompatibleClient,
)


def load_llm(settings: AgentSettings) -> BaseLLMClient:
    """Instantiate the correct LLM client based on settings.

    Type contract (fix B.17 review-r2-v2): accepts ONLY AgentSettings, NOT
    BaseAIATSettings or ContextOrchestratorSettings. The context-orchestrator
    must never call load_llm() — it does not own LLM credentials (least privilege).

    Dual-mode (ADR-0008):
      gateway="openrouter" → OpenAICompatibleClient(base_url=OPENROUTER_BASE_URL)
          Uses AIAT_OPENROUTER_API_KEY. Single-key dev gateway; cassette recording.
      gateway="direct" (default) → native provider dispatch:
          provider="openai"     → OpenAIClient
          provider="anthropic"  → AnthropicClient
          provider="deepseek"   → OpenAICompatibleClient(base_url=DEEPSEEK_BASE_URL)
          provider="qwen"       → OpenAICompatibleClient(base_url=QWEN_BASE_URL)

    Both branches coexist; no client is removed (additive principle, ADR-0008).
    """
    pricing = load_pricing_for_model(settings.model_name_api)

    if settings.llm_gateway == "openrouter":
        return OpenAICompatibleClient(
            api_key=settings.openrouter_api_key,
            model_name=settings.model_name_api,
            base_url=OPENROUTER_BASE_URL,
            temperature=settings.temperature,
            pricing=pricing,
            top_p=settings.top_p,
            max_tokens=settings.max_tokens,
            seed=settings.seed,
            provider_name="openrouter",
        )

    # gateway="direct" — native provider dispatch
    match settings.llm_provider:
        case "openai":
            return OpenAIClient(
                api_key=settings.openai_api_key,
                model_name=settings.model_name_api,
                temperature=settings.temperature,
                pricing=pricing,
                top_p=settings.top_p,
                max_tokens=settings.max_tokens,
                seed=settings.seed,
            )
        case "anthropic":
            return AnthropicClient(
                api_key=settings.anthropic_api_key,
                model_name=settings.model_name_api,
                temperature=settings.temperature,
                pricing=pricing,
                max_tokens=settings.max_tokens,
            )
        case "deepseek":
            return OpenAICompatibleClient(
                api_key=settings.deepseek_api_key,
                model_name=settings.model_name_api,
                base_url=DEEPSEEK_BASE_URL,
                temperature=settings.temperature,
                pricing=pricing,
                top_p=settings.top_p,
                max_tokens=settings.max_tokens,
                seed=settings.seed,
                provider_name="deepseek",
            )
        case "qwen":
            return OpenAICompatibleClient(
                api_key=settings.qwen_api_key,
                model_name=settings.model_name_api,
                base_url=QWEN_BASE_URL,
                temperature=settings.temperature,
                pricing=pricing,
                top_p=settings.top_p,
                max_tokens=settings.max_tokens,
                seed=settings.seed,
                provider_name="qwen",
            )
        case _:
            raise ValueError(
                f"Unknown LLM provider: {settings.llm_provider!r}. "
                "Expected one of: openai, anthropic, deepseek, qwen."
            )
