"""4 guardrail Strategia C+ (PRE_PRD §13.3, §7.4 — never disableable, invariant #8).

Guardrails apply in order per action:
  1. SL/TP mandatory (Figma F1): LONG/SHORT without SL or TP → force HOLD
  2. size_pct clamp: size_pct > max_size_pct → clamp to max_size_pct
  3. leverage clamp: leverage > min(1 + confidence×9, hard_max_leverage) → clamp
  4. confidence gate: confidence < min_open_confidence → force HOLD
"""

from decimal import ROUND_DOWN, Decimal
from typing import Protocol

from aiat.domain.enums import EntryType, Side
from aiat.domain.schemas import ActionDecision, GuardrailReport, TradeDecision

_LEVERAGE_SCALE = Decimal("9")
_TWO_DP = Decimal("0.01")
_OPEN_SIDES = frozenset({Side.LONG, Side.SHORT})


class GuardrailStrategy(Protocol):
    """Protocol for composable guardrail strategies (§7.4)."""

    def apply(
        self,
        decision: TradeDecision,
        *,
        max_size_pct: Decimal,
        hard_max_leverage: Decimal,
        min_open_confidence: Decimal,
    ) -> tuple[TradeDecision, list[GuardrailReport]]:
        """Apply guardrails to all actions in the decision.

        Returns:
            Tuple of (post-clamp TradeDecision, per-action reports).
        """
        ...


def _force_hold(source: ActionDecision) -> ActionDecision:
    """Create a valid HOLD action, preserving informational metadata from source."""
    return ActionDecision.model_validate(
        {
            "symbol": source.symbol,
            "side": Side.HOLD,
            "leverage": Decimal("0"),
            "size_pct": Decimal("0"),
            "stop_loss_pct": None,
            "take_profit_pct": None,
            "entry_type": EntryType.NONE,
            "limit_price": None,
            "confidence": source.confidence,
            "time_horizon_min": source.time_horizon_min,
            "action_reasoning": source.action_reasoning,
            "action_key_signals": list(source.action_key_signals),
        }
    )


class Guardrails:
    """4 guardrail Strategia C+ — invariant #8: always active, never disableable."""

    def _apply_to_action(
        self,
        action: ActionDecision,
        *,
        max_size_pct: Decimal,
        hard_max_leverage: Decimal,
        min_open_confidence: Decimal,
    ) -> tuple[ActionDecision, GuardrailReport]:
        original_side = action.side
        leverage_clamped = False
        size_pct_clamped = False
        forced_hold = False
        current = action

        # Guardrail 1: SL/TP mandatory for LONG/SHORT (Figma F1)
        if current.side in _OPEN_SIDES:
            if current.stop_loss_pct is None or current.take_profit_pct is None:
                current = _force_hold(current)
                forced_hold = True

        # Guardrail 2: size_pct clamp
        if current.side in _OPEN_SIDES and current.size_pct > max_size_pct:
            current = current.model_copy(update={"size_pct": max_size_pct})
            size_pct_clamped = True

        # Guardrail 3: leverage clamp (dynamic cap = 1 + confidence×9, bounded by hard max)
        if current.side in _OPEN_SIDES:
            cap = min(
                (Decimal("1") + current.confidence * _LEVERAGE_SCALE).quantize(
                    _TWO_DP, rounding=ROUND_DOWN
                ),
                hard_max_leverage,
            )
            if current.leverage > cap:
                current = current.model_copy(update={"leverage": cap})
                leverage_clamped = True

        # Guardrail 4: confidence gate
        if current.side in _OPEN_SIDES and current.confidence < min_open_confidence:
            current = _force_hold(current)
            forced_hold = True

        return current, GuardrailReport(
            symbol=action.symbol,
            original_side=original_side,
            leverage_clamped=leverage_clamped,
            size_pct_clamped=size_pct_clamped,
            forced_hold=forced_hold,
            final_action=current,
        )

    def apply(
        self,
        decision: TradeDecision,
        *,
        max_size_pct: Decimal,
        hard_max_leverage: Decimal,
        min_open_confidence: Decimal,
    ) -> tuple[TradeDecision, list[GuardrailReport]]:
        """Apply all 4 guardrails to every action in the decision, in order.

        Args:
            decision: Raw TradeDecision from the LLM (pre-guardrail).
            max_size_pct: Maximum allowed size_pct (e.g. 0.20 from AIAT_MAX_SIZE_PCT).
            hard_max_leverage: Hard leverage ceiling (e.g. 10 from AIAT_HARD_MAX_LEVERAGE).
            min_open_confidence: Minimum confidence to open (from AIAT_MIN_OPEN_CONFIDENCE).

        Returns:
            Tuple of (post-guardrail TradeDecision, one GuardrailReport per action).
        """
        new_actions: list[ActionDecision] = []
        reports: list[GuardrailReport] = []

        for action in decision.actions:
            processed, report = self._apply_to_action(
                action,
                max_size_pct=max_size_pct,
                hard_max_leverage=hard_max_leverage,
                min_open_confidence=min_open_confidence,
            )
            new_actions.append(processed)
            reports.append(report)

        return decision.model_copy(update={"actions": new_actions}), reports
