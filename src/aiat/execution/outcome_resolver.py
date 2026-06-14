"""Outcome resolver for closed positions and HOLD/FLAT decisions (§4.2, closes D2).

D2 Rule — HOLD/FLAT labeling (ADR-0014):
  A HOLD/FLAT decision is labeled was_profitable_net=True when the absolute
  price change over time_horizon_min does not exceed fee_roundtrip_pct.
  This means no directional position would have overcome the round-trip fee drag.

  For HOLD/FLAT outcomes:
    - All PnL fields are Decimal("0") (no position opened).
    - holding_duration_min = decision_action_time_horizon_min (passive hold for full horizon).
    - horizon_met = True (passive choice maintained through the horizon by definition).
    - pnl_net_fee_funding_tax_sim_usd = Decimal("0") for all outcomes
      (populated later by scripts/compute_tax_sim.py, never by the resolver).
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class PositionOutcomeInput:
    """Inputs for resolving a closed LONG/SHORT position outcome."""

    opening_action_id: UUID
    opening_run_id: UUID
    closing_run_id: UUID
    experiment_id: UUID
    model_id: str
    symbol: str
    decision_action_confidence: Decimal
    decision_action_time_horizon_min: int
    realized_pnl_gross_usd: Decimal
    sum_fees_usd: Decimal
    sum_funding_usd: Decimal
    holding_duration_min: int


@dataclass(frozen=True)
class HoldFlatOutcomeInput:
    """Inputs for resolving a HOLD/FLAT decision outcome (D2 counterfactual rule)."""

    opening_action_id: UUID
    opening_run_id: UUID
    closing_run_id: UUID
    experiment_id: UUID
    model_id: str
    symbol: str
    decision_action_confidence: Decimal
    decision_action_time_horizon_min: int
    price_at_decision: Decimal
    price_at_horizon: Decimal
    fee_roundtrip_pct: Decimal


@dataclass(frozen=True)
class OutcomeResult:
    """Computed outcome data ready for insertion by OutcomesRepository."""

    opening_action_id: UUID
    opening_run_id: UUID
    closing_run_id: UUID
    experiment_id: UUID
    model_id: str
    symbol: str
    realized_pnl_gross_usd: Decimal
    sum_fees_usd: Decimal
    sum_funding_usd: Decimal
    pnl_net_fee_usd: Decimal
    pnl_net_fee_funding_usd: Decimal
    pnl_net_fee_funding_tax_sim_usd: Decimal
    was_profitable_net: bool
    holding_duration_min: int
    decision_action_confidence: Decimal
    decision_action_time_horizon_min: int
    horizon_met: bool


_ZERO = Decimal("0")


class OutcomeResolver:
    """Pure domain service — resolves outcome data from pre-fetched inputs.

    No DB access. Callers (e.g. OutcomesRepository) are responsible for
    fetching the required inputs and persisting the returned OutcomeResult.
    """

    def resolve_position(self, inp: PositionOutcomeInput) -> OutcomeResult:
        """Resolve outcome for a closed LONG/SHORT position.

        Args:
            inp: Pre-fetched data for the closed position.

        Returns:
            OutcomeResult with all PnL fields computed.
        """
        pnl_net_fee = inp.realized_pnl_gross_usd - inp.sum_fees_usd
        pnl_net_fee_funding = pnl_net_fee + inp.sum_funding_usd
        return OutcomeResult(
            opening_action_id=inp.opening_action_id,
            opening_run_id=inp.opening_run_id,
            closing_run_id=inp.closing_run_id,
            experiment_id=inp.experiment_id,
            model_id=inp.model_id,
            symbol=inp.symbol,
            realized_pnl_gross_usd=inp.realized_pnl_gross_usd,
            sum_fees_usd=inp.sum_fees_usd,
            sum_funding_usd=inp.sum_funding_usd,
            pnl_net_fee_usd=pnl_net_fee,
            pnl_net_fee_funding_usd=pnl_net_fee_funding,
            pnl_net_fee_funding_tax_sim_usd=_ZERO,
            was_profitable_net=pnl_net_fee_funding > _ZERO,
            holding_duration_min=inp.holding_duration_min,
            decision_action_confidence=inp.decision_action_confidence,
            decision_action_time_horizon_min=inp.decision_action_time_horizon_min,
            horizon_met=inp.holding_duration_min <= inp.decision_action_time_horizon_min,
        )

    def resolve_hold_flat(self, inp: HoldFlatOutcomeInput) -> OutcomeResult:
        """Resolve outcome for a HOLD/FLAT decision (D2 fee-hurdle counterfactual).

        D2 rule: was_profitable_net=True when |price_change_pct| ≤ fee_roundtrip_pct,
        meaning the market did not move enough for any directional position to beat fees.

        Args:
            inp: Pre-fetched decision data and price points at decision time
                 and at time_horizon.

        Returns:
            OutcomeResult with all PnL fields zero and was_profitable_net from
            the fee-hurdle counterfactual.
        """
        abs_price_change_pct = abs(
            (inp.price_at_horizon - inp.price_at_decision) / inp.price_at_decision
        )
        was_profitable = abs_price_change_pct <= inp.fee_roundtrip_pct
        return OutcomeResult(
            opening_action_id=inp.opening_action_id,
            opening_run_id=inp.opening_run_id,
            closing_run_id=inp.closing_run_id,
            experiment_id=inp.experiment_id,
            model_id=inp.model_id,
            symbol=inp.symbol,
            realized_pnl_gross_usd=_ZERO,
            sum_fees_usd=_ZERO,
            sum_funding_usd=_ZERO,
            pnl_net_fee_usd=_ZERO,
            pnl_net_fee_funding_usd=_ZERO,
            pnl_net_fee_funding_tax_sim_usd=_ZERO,
            was_profitable_net=was_profitable,
            holding_duration_min=inp.decision_action_time_horizon_min,
            decision_action_confidence=inp.decision_action_confidence,
            decision_action_time_horizon_min=inp.decision_action_time_horizon_min,
            horizon_met=True,
        )
