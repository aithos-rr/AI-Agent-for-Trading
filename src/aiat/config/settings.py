"""Settings hierarchy for AIAT services (PRD §10.3, fix B.13).

Three classes discriminated by service_role:
  BaseAIATSettings  — common fields (all roles)
  AgentSettings     — agent-specific: LLM credentials + HL wallet
  ContextOrchestratorSettings — least privilege: no LLM keys, no wallet

load_settings() dispatches on AIAT_SERVICE_ROLE env var.
"""

import os
from decimal import Decimal
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseAIATSettings(BaseSettings):
    """Common fields shared by all service roles."""

    model_config = SettingsConfigDict(
        env_prefix="AIAT_",
        env_file=".env",
        case_sensitive=False,
        extra="forbid",
    )

    experiment_id: str
    git_commit_sha: str

    database_url: SecretStr

    network: Literal["testnet"] = "testnet"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    service_role: Literal["agent", "context_orchestrator"]


class AgentSettings(BaseAIATSettings):
    """Settings for the 4 agent services (one per LLM model).

    Holds LLM credentials and the HL wallet private key specific to this model.
    The context-orchestrator must never hold these secrets (least privilege).
    """

    service_role: Literal["agent"] = "agent"

    model_id: str
    prompt_template_hash: str
    schema_version: Literal["v1"] = "v1"

    llm_provider: Literal["openai", "anthropic", "deepseek", "qwen"]
    model_name_api: str
    temperature: Decimal | None = None
    top_p: Decimal | None = None
    max_tokens: int | None = None
    seed: int | None = None

    # LLM API keys — only the one matching llm_provider is required
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    qwen_api_key: SecretStr | None = None

    # OpenRouter dev gateway (ADR-0008)
    llm_gateway: str = "direct"
    openrouter_api_key: SecretStr | None = None

    # Hyperliquid wallet — one per model
    hl_wallet_private_key: SecretStr
    hl_wallet_address: str

    # Hyperliquid client implementation selector (M4-T08 wiring).
    # Defaults to 'mock' so existing tests and un-provisioned deploys stay green;
    # set AIAT_HL_CLIENT_IMPL=real to trade on testnet via the live SDK client.
    hl_client_impl: Literal["mock", "real"] = "mock"

    # Guardrails (Strategia C+, PRE_PRD §13.3 — always active, invariant #8)
    max_size_pct: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    hard_max_leverage: Decimal = Field(default=Decimal("10"), ge=1)
    min_open_confidence: Decimal = Field(default=Decimal("0.4"), ge=0, le=1)

    # Context (invariant #5)
    inject_decision_history: bool = False

    # Scheduling
    agent_start_delay_seconds: int = 30
    hard_timeout_seconds: int = 180

    @model_validator(mode="after")
    def validate_api_key_matches_provider(self) -> "AgentSettings":
        """Ensure the provided API key matches the chosen LLM provider."""
        if self.llm_gateway == "openrouter":
            if self.openrouter_api_key is None:
                raise ValueError("gateway='openrouter' requires AIAT_OPENROUTER_API_KEY")
            return self
        mapping: dict[str, SecretStr | None] = {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "deepseek": self.deepseek_api_key,
            "qwen": self.qwen_api_key,
        }
        if mapping[self.llm_provider] is None:
            raise ValueError(
                f"llm_provider='{self.llm_provider}' requires "
                f"AIAT_{self.llm_provider.upper()}_API_KEY"
            )
        return self


class ContextOrchestratorSettings(BaseAIATSettings):
    """Settings for the context-orchestrator service.

    Least privilege: no LLM credentials, no HL wallet private key.
    Only accesses public/free sources (HL info endpoint, RSS, F&G).
    """

    service_role: Literal["context_orchestrator"] = "context_orchestrator"

    cron_minute_offsets: list[int] = [0, 15, 30, 45]
    hard_timeout_seconds: int = 30

    newsfeed_api_key: SecretStr | None = None

    # Tax simulation (ADR-0033). Rate is an EXPLICIT config override written on every
    # tax_sim_periods row — the schema server_default (0.26) is left untouched (no migration).
    # 0.33 reflects the Italian regime applied to leveraged crypto derivatives for this study.
    # period='quarter' for the real experiment; 'daily' for the M6.2 smoke (faster feedback).
    tax_rate_pct: Decimal = Field(default=Decimal("0.33"), ge=0, le=1)
    tax_period: Literal["daily", "quarter"] = "quarter"


def load_settings() -> AgentSettings | ContextOrchestratorSettings:
    """Dispatch on AIAT_SERVICE_ROLE, return the appropriate Settings subclass."""
    role = os.environ.get("AIAT_SERVICE_ROLE")
    if role == "agent":
        return AgentSettings()  # type: ignore[call-arg]
    elif role == "context_orchestrator":
        return ContextOrchestratorSettings()  # type: ignore[call-arg]
    else:
        raise RuntimeError(
            f"AIAT_SERVICE_ROLE must be 'agent' or 'context_orchestrator', got {role!r}"
        )
