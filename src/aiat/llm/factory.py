"""LLM factory — dual-mode dispatch (PRD §8.1 + ADR-0008)."""

from decimal import Decimal

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

_DEFAULT_TEMPERATURE = Decimal("0.7")
_DEFAULT_MAX_TOKENS = 4096


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
    temperature = settings.temperature if settings.temperature is not None else _DEFAULT_TEMPERATURE
    max_tokens = settings.max_tokens if settings.max_tokens is not None else _DEFAULT_MAX_TOKENS

    if settings.llm_gateway == "openrouter":
        # validate_api_key_matches_provider guarantees openrouter_api_key is not None
        assert settings.openrouter_api_key is not None
        return OpenAICompatibleClient(
            api_key=settings.openrouter_api_key.get_secret_value(),
            model_name=settings.model_name_api,
            base_url=OPENROUTER_BASE_URL,
            temperature=temperature,
            pricing=pricing,
            top_p=settings.top_p,
            max_tokens=max_tokens,
            seed=settings.seed,
            provider_name="openrouter",
        )

    # gateway="direct" — native provider dispatch
    match settings.llm_provider:
        case "openai":
            # validate_api_key_matches_provider guarantees key is not None
            assert settings.openai_api_key is not None
            return OpenAIClient(
                api_key=settings.openai_api_key.get_secret_value(),
                model_name=settings.model_name_api,
                temperature=temperature,
                pricing=pricing,
                top_p=settings.top_p,
                max_tokens=max_tokens,
                seed=settings.seed,
            )
        case "anthropic":
            assert settings.anthropic_api_key is not None
            return AnthropicClient(
                api_key=settings.anthropic_api_key.get_secret_value(),
                model_name=settings.model_name_api,
                temperature=temperature,
                pricing=pricing,
                max_tokens=max_tokens,
            )
        case "deepseek":
            assert settings.deepseek_api_key is not None
            return OpenAICompatibleClient(
                api_key=settings.deepseek_api_key.get_secret_value(),
                model_name=settings.model_name_api,
                base_url=DEEPSEEK_BASE_URL,
                temperature=temperature,
                pricing=pricing,
                top_p=settings.top_p,
                max_tokens=max_tokens,
                seed=settings.seed,
                provider_name="deepseek",
            )
        case "qwen":
            assert settings.qwen_api_key is not None
            return OpenAICompatibleClient(
                api_key=settings.qwen_api_key.get_secret_value(),
                model_name=settings.model_name_api,
                base_url=QWEN_BASE_URL,
                temperature=temperature,
                pricing=pricing,
                top_p=settings.top_p,
                max_tokens=max_tokens,
                seed=settings.seed,
                provider_name="qwen",
            )
        case _:
            raise ValueError(
                f"Unknown LLM provider: {settings.llm_provider!r}. "
                "Expected one of: openai, anthropic, deepseek, qwen."
            )
