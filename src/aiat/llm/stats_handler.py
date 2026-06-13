"""StatsCallbackHandler — LangChain callback for LLM token usage tracking (PRD §8.3)."""

from decimal import Decimal
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

from aiat.domain.schemas import CostEventData


class StatsCallbackHandler(AsyncCallbackHandler):
    """LangChain callback that captures token usage uniformly across all providers.

    Returns CostEventData which the caller persists AFTER the decision
    (invariant #4 — NO direct DB writes here).

    Fix B.8 review-r2: aggregates tokens across MULTIPLE attempts (primary + optional
    freetext fallback). `n_attempts` tracks how many LLM calls were made; `cost_usd`
    final = sum of all. This ensures the cost ledger reflects the REAL cost incurred
    to produce a decision, not just the cost of the last attempt.
    """

    def __init__(self, pricing: dict[str, Decimal]) -> None:
        super().__init__()
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.reasoning_tokens: int = 0
        self.n_attempts: int = 0
        self._pricing = pricing

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        usage = self._extract_usage(response)
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.reasoning_tokens += usage.get("reasoning_tokens", 0)
        self.n_attempts += 1

    def build_cost_event(self) -> CostEventData:
        """Build the aggregated CostEventData for all LLM calls made so far."""
        cost_usd = (
            Decimal(self.input_tokens) * self._pricing["input"] / Decimal("1000000")
            + Decimal(self.output_tokens) * self._pricing["output"] / Decimal("1000000")
            + Decimal(self.reasoning_tokens) * self._pricing["reasoning"] / Decimal("1000000")
        )
        return CostEventData(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
            cost_usd=cost_usd,
            pricing_snapshot=self._pricing,
            n_attempts=max(1, self.n_attempts),
        )

    def _extract_usage(self, response: LLMResult) -> dict[str, int]:
        """Extract token counts from a LangChain LLMResult.

        Provider-specific paths (native formats per ADR-0008):
          OpenAI: llm_output['token_usage']['prompt_tokens'/'completion_tokens']
                  + completion_tokens_details.reasoning_tokens for o-series models
          Anthropic: llm_output['usage']['input_tokens'/'output_tokens']
          DeepSeek-R1 (OpenAI-compatible): llm_output['usage'] with reasoning_tokens
          Qwen (OpenAI-compatible): llm_output['usage'], reasoning_tokens=0 by default
        """
        llm_output: dict[str, Any] = response.llm_output or {}

        # --- OpenAI native: token_usage key ---
        token_usage: dict[str, Any] = llm_output.get("token_usage") or {}
        if token_usage:
            ct_details: dict[str, Any] = token_usage.get("completion_tokens_details") or {}
            return {
                "input_tokens": int(token_usage.get("prompt_tokens", 0)),
                "output_tokens": int(token_usage.get("completion_tokens", 0)),
                "reasoning_tokens": int(ct_details.get("reasoning_tokens", 0)),
            }

        # --- Anthropic native / DeepSeek/Qwen via usage key ---
        usage: dict[str, Any] = llm_output.get("usage") or {}
        if usage:
            return {
                "input_tokens": int(usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)),
                "output_tokens": int(
                    usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
                ),
                "reasoning_tokens": int(usage.get("reasoning_tokens", 0)),
            }

        # --- Fallback: check generation response_metadata (newer LangChain) ---
        if response.generations:
            first_gen_list = response.generations[0] if response.generations else []
            if first_gen_list:
                gen = first_gen_list[0]
                msg = getattr(gen, "message", None)
                if msg is not None:
                    meta: dict[str, Any] = getattr(msg, "response_metadata", None) or {}
                    tu = meta.get("token_usage") or {}
                    if tu:
                        ct_d: dict[str, Any] = tu.get("completion_tokens_details") or {}
                        return {
                            "input_tokens": int(tu.get("prompt_tokens", 0)),
                            "output_tokens": int(tu.get("completion_tokens", 0)),
                            "reasoning_tokens": int(ct_d.get("reasoning_tokens", 0)),
                        }
                    u2: dict[str, Any] = meta.get("usage") or {}
                    if u2:
                        return {
                            "input_tokens": int(u2.get("input_tokens", 0)),
                            "output_tokens": int(u2.get("output_tokens", 0)),
                            "reasoning_tokens": int(u2.get("reasoning_tokens", 0)),
                        }

        return {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
