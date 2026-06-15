"""Minimal metrics stubs (PRD §11.3).

Placeholder for future Prometheus/OpenTelemetry integration. All functions
are safe no-ops so callers can be instrumented now without blocking on infra.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def record_tick_duration_ms(
    service_role: str,
    model_id: str | None,
    duration_ms: float,
    status: str,
) -> None:
    """Record tick execution duration.

    Args:
        service_role: "agent" or "context_orchestrator".
        model_id: Model identifier for agent runs; None for orchestrator.
        duration_ms: Wall-clock duration in milliseconds.
        status: "success", "failed", "timeout", or "missed".
    """
    logger.info(
        "tick_duration",
        service_role=service_role,
        model_id=model_id,
        duration_ms=duration_ms,
        status=status,
    )


def record_llm_cost(
    model_id: str,
    cost_usd: float,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Record LLM call cost for observability.

    Args:
        model_id: Model identifier.
        cost_usd: Total cost in USD for this invocation.
        input_tokens: Number of input tokens consumed.
        output_tokens: Number of output tokens generated.
    """
    logger.info(
        "llm_cost",
        model_id=model_id,
        cost_usd=cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
