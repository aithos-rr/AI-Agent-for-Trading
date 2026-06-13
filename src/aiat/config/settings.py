"""Settings stub for M2. Full implementation in M5 (PRD §11.4).

This module is expanded in M5 to include all AIAT_* environment variables,
Pydantic-settings validation, and the BaseAIATSettings / AgentSettings /
ContextOrchestratorSettings hierarchy.
"""

from decimal import Decimal

from pydantic import BaseModel


class AgentSettings(BaseModel):
    """Minimal stub of agent settings needed by the LLM factory (PRD §8.1).

    Full implementation with pydantic-settings env-var loading is in M5.
    """

    llm_provider: str
    """One of: openai | anthropic | deepseek | qwen."""

    llm_gateway: str = "direct"
    """Routing mode: direct (default, native providers) | openrouter (dev single-key)."""

    model_name_api: str
    """Model name passed to the provider API (e.g. 'gpt-4o', 'claude-3-5-sonnet-20241022')."""

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    qwen_api_key: str = ""
    openrouter_api_key: str = ""

    temperature: Decimal = Decimal("0.7")
    top_p: Decimal | None = None
    max_tokens: int = 4096
    seed: int | None = None
