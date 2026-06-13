"""Pricing loader for LLM cost tracking (PRD §8.4)."""

from decimal import Decimal
from pathlib import Path

import yaml

_YAML_PATH = Path(__file__).parent / "model_pricing.yaml"

_FALLBACK_PRICING: dict[str, Decimal] = {
    "input": Decimal("1.00"),
    "output": Decimal("5.00"),
    "reasoning": Decimal("0.00"),
}


def load_pricing_for_model(model_name: str) -> dict[str, Decimal]:
    """Load USD/1M-token pricing for a model from model_pricing.yaml.

    Falls back to a conservative estimate if the model is not found, so that
    cost tracking remains functional even for models added after the YAML was last
    updated (D1 — final model list deferred to M7).

    Args:
        model_name: Native API model name (e.g. 'openai-gpt-5.1') or OpenRouter
            convention name (e.g. 'openai/gpt-4o'). Look up the exact key in
            model_pricing.yaml; add OpenRouter names as additional keys there.

    Returns:
        dict with 'input', 'output', 'reasoning' keys, all Decimal USD/1M tokens.
    """
    raw: dict[str, object] = yaml.safe_load(_YAML_PATH.read_text())
    models: dict[str, dict[str, object]] = raw.get("models", {})  # type: ignore[assignment]
    entry = models.get(model_name)
    if entry is None:
        return dict(_FALLBACK_PRICING)
    return {
        "input": Decimal(str(entry.get("input", "1.00"))),
        "output": Decimal(str(entry.get("output", "5.00"))),
        "reasoning": Decimal(str(entry.get("reasoning", "0.00"))),
    }
