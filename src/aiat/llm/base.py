"""BaseLLMClient ABC — uniform interface for all 4 LLM providers (PRD §7.3)."""

from abc import ABC, abstractmethod

from aiat.domain.schemas import LLMInvocationResult


class BaseLLMClient(ABC):
    """Uniform interface for OpenAI, Anthropic, OpenAICompatible (DeepSeek/Qwen).

    Implements:
      1. Primary: with_structured_output(TradeDecision) via langchain
      2. Fallback: freetext + balanced-JSON extraction
      3. Cost tracking: returns CostEventData (persisted AFTER by caller, inv #4)
      4. Nuisance snapshot: provider/model/temperature/top_p/seed in result
    """

    provider: str
    model_name_api: str

    @abstractmethod
    async def invoke(
        self,
        prompt: str,
        *,
        timeout_seconds: int = 90,
    ) -> LLMInvocationResult:
        """Invoke the LLM and return a validated TradeDecision with cost data.

        Returns:
            LLMInvocationResult with Pydantic-validated decision + cost + nuisance.

        Raises:
            LLMTimeoutError: timeout_seconds exceeded.
            LLMUnrecoverableError: both primary and freetext fallback failed parsing.
        """
        ...
