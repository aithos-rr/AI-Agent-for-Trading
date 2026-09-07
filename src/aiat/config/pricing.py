"""Pricing loader for LLM cost tracking (PRD §8.4)."""

from decimal import Decimal
from pathlib import Path

import yaml

_YAML_PATH = Path(__file__).parent / "model_pricing.yaml"


class UnknownModelPricingError(LookupError):
    """Raised when a model id has no entry in model_pricing.yaml.

    Deliberately fatal. The previous behaviour — returning a conservative fallback —
    made a key mismatch invisible: ``load_llm`` looked the price up by
    ``model_name_api`` while the YAML is keyed by ``model_id`` (ADR-0020), so every
    ``cost_events`` row of the M6.1 and M6.2-r2 datasets was written with the fallback
    price instead of the real one. Cost is a dependent variable of RQ1, so a silent
    wrong number is worse than a loud failure at startup.
    """


def load_pricing_for_model(model_id: str) -> dict[str, Decimal]:
    """Load USD/1M-token pricing for a model from model_pricing.yaml.

    Args:
        model_id: The **stable D1 model id** (``usa-premium``, ``usa-cheap``,
            ``cn-premium``, ``cn-cheap``) — the key convention of
            ``model_pricing.yaml``, per ADR-0020. NOT the concrete
            ``model_name_api`` (e.g. ``gpt-4.1-mini``), which is an attribute of the
            model and is frozen at the seed.

    Returns:
        dict with 'input', 'output', 'reasoning' keys, all Decimal USD/1M tokens.

    Raises:
        UnknownModelPricingError: if ``model_id`` has no entry in the YAML. There is
            no fallback by design — see the exception's docstring.
    """
    raw: dict[str, object] = yaml.safe_load(_YAML_PATH.read_text())
    models: dict[str, dict[str, object]] = raw.get("models", {})  # type: ignore[assignment]
    entry = models.get(model_id)
    if entry is None:
        raise UnknownModelPricingError(
            f"No pricing entry for model_id {model_id!r} in model_pricing.yaml "
            f"(known ids: {sorted(models)}). The YAML is keyed by the stable D1 "
            f"model id (ADR-0020), not by model_name_api."
        )
    return {
        "input": Decimal(str(entry.get("input", "1.00"))),
        "output": Decimal(str(entry.get("output", "5.00"))),
        "reasoning": Decimal(str(entry.get("reasoning", "0.00"))),
    }
