"""Unit tests for OutcomeResolver (§4.2, D2 labeling rule — ADR-0014)."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from aiat.execution.outcome_resolver import (
    HoldFlatOutcomeInput,
    OutcomeResolver,
    PositionOutcomeInput,
    holding_duration_min,
)

_ACT_ID = uuid4()
_OPEN_RUN = uuid4()
_CLOSE_RUN = uuid4()
_EXP_ID = uuid4()
_MODEL = "test-model"
_SYMBOL = "BTC"


def _pos_input(**overrides: object) -> PositionOutcomeInput:
    defaults = dict(
        opening_action_id=_ACT_ID,
        opening_run_id=_OPEN_RUN,
        closing_run_id=_CLOSE_RUN,
        experiment_id=_EXP_ID,
        model_id=_MODEL,
        symbol=_SYMBOL,
        decision_action_confidence=Decimal("0.70"),
        decision_action_time_horizon_min=60,
        realized_pnl_gross_usd=Decimal("50.00"),
        sum_fees_usd=Decimal("5.00"),
        sum_funding_usd=Decimal("0.00"),
        holding_duration_min=45,
    )
    defaults.update(overrides)
    return PositionOutcomeInput(**defaults)  # type: ignore[arg-type]


def _hf_input(**overrides: object) -> HoldFlatOutcomeInput:
    defaults = dict(
        opening_action_id=_ACT_ID,
        opening_run_id=_OPEN_RUN,
        closing_run_id=_CLOSE_RUN,
        experiment_id=_EXP_ID,
        model_id=_MODEL,
        symbol=_SYMBOL,
        decision_action_confidence=Decimal("0.60"),
        decision_action_time_horizon_min=60,
        price_at_decision=Decimal("50000.00"),
        price_at_horizon=Decimal("50050.00"),  # +0.1% move
        fee_roundtrip_pct=Decimal("0.002"),  # 0.2% hurdle
    )
    defaults.update(overrides)
    return HoldFlatOutcomeInput(**defaults)  # type: ignore[arg-type]


class TestHoldingDurationMin:
    """Tests for the shared holding_duration_min helper (ADR-0035; mirrors close_position)."""

    def test_floors_partial_minute(self) -> None:
        opened = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
        closed = datetime(2026, 7, 13, 13, 43, 40, 756000, tzinfo=UTC)  # 103m 40.756s
        assert holding_duration_min(opened, closed) == 103

    def test_exact_minutes(self) -> None:
        opened = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
        closed = datetime(2026, 7, 13, 13, 0, 0, tzinfo=UTC)
        assert holding_duration_min(opened, closed) == 60

    def test_zero_and_negative_clamped_to_zero(self) -> None:
        t = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
        assert holding_duration_min(t, t) == 0
        # closed_at earlier than opened_at (clock skew) must never go negative.
        assert holding_duration_min(t, datetime(2026, 7, 13, 11, 0, 0, tzinfo=UTC)) == 0


class TestResolvePosition:
    """Tests for OutcomeResolver.resolve_position (LONG/SHORT closed positions)."""

    def setup_method(self) -> None:
        self.resolver = OutcomeResolver()

    def test_profitable_position_sets_was_profitable_true(self) -> None:
        inp = _pos_input(
            realized_pnl_gross_usd=Decimal("100.00"),
            sum_fees_usd=Decimal("5.00"),
            sum_funding_usd=Decimal("0.00"),
        )
        result = self.resolver.resolve_position(inp)
        # pnl_net_fee_funding = 100 - 5 - 0 = 95 > 0
        assert result.was_profitable_net is True

    def test_unprofitable_position_sets_was_profitable_false(self) -> None:
        inp = _pos_input(
            realized_pnl_gross_usd=Decimal("3.00"),
            sum_fees_usd=Decimal("5.00"),
            sum_funding_usd=Decimal("0.00"),
        )
        result = self.resolver.resolve_position(inp)
        # pnl_net_fee_funding = 3 - 5 - 0 = -2 ≤ 0
        assert result.was_profitable_net is False

    def test_exact_zero_pnl_not_profitable(self) -> None:
        inp = _pos_input(
            realized_pnl_gross_usd=Decimal("5.00"),
            sum_fees_usd=Decimal("5.00"),
            sum_funding_usd=Decimal("0.00"),
        )
        result = self.resolver.resolve_position(inp)
        assert result.pnl_net_fee_funding_usd == Decimal("0")
        assert result.was_profitable_net is False  # strictly > 0 required

    def test_received_funding_improves_pnl(self) -> None:
        # PRD §3.2.6: funding_amount_usd negative = received (ricevi) → improves PnL.
        inp = _pos_input(
            realized_pnl_gross_usd=Decimal("10.00"),
            sum_fees_usd=Decimal("1.00"),
            sum_funding_usd=Decimal("-12.00"),  # model receives funding
        )
        result = self.resolver.resolve_position(inp)
        # pnl_net_fee_funding = 10 - 1 - (-12) = 21
        assert result.pnl_net_fee_funding_usd == Decimal("21.00")
        assert result.was_profitable_net is True

    def test_paid_funding_worsens_pnl(self) -> None:
        # PRD §3.2.6: funding_amount_usd positive = paid (paghi) → reduces PnL.
        inp = _pos_input(
            realized_pnl_gross_usd=Decimal("1.00"),
            sum_fees_usd=Decimal("5.00"),
            sum_funding_usd=Decimal("10.00"),  # model pays funding
        )
        result = self.resolver.resolve_position(inp)
        # pnl_net_fee_funding = 1 - 5 - 10 = -14
        assert result.pnl_net_fee_funding_usd == Decimal("-14.00")
        assert result.was_profitable_net is False

    def test_funding_sign_matches_positions_repository(self) -> None:
        # Cross-path reconciliation: same (gross, fees, funding) must yield the same
        # pnl_net_fee_funding as PositionsRepository.close_position. The integration
        # test test_close_position_with_funding_events fixes funding=+2.00 →
        # pnl_net_fee_funding = 9.50 - 2.00 = 7.50; the resolver must agree.
        inp = _pos_input(
            realized_pnl_gross_usd=Decimal("10.00"),
            sum_fees_usd=Decimal("0.50"),
            sum_funding_usd=Decimal("2.00"),  # paid → subtracted
        )
        result = self.resolver.resolve_position(inp)
        assert result.pnl_net_fee_usd == Decimal("9.50")
        assert result.pnl_net_fee_funding_usd == Decimal("7.50")
        assert result.was_profitable_net is True

    def test_pnl_computations_are_consistent(self) -> None:
        inp = _pos_input(
            realized_pnl_gross_usd=Decimal("80.00"),
            sum_fees_usd=Decimal("8.00"),
            sum_funding_usd=Decimal("-2.00"),
        )
        result = self.resolver.resolve_position(inp)
        assert result.pnl_net_fee_usd == Decimal("72.00")  # 80 - 8
        # funding -2.00 = received → pnl_net_fee_funding = 72 - (-2) = 74
        assert result.pnl_net_fee_funding_usd == Decimal("74.00")

    def test_tax_sim_always_zero(self) -> None:
        result = self.resolver.resolve_position(_pos_input())
        assert result.pnl_net_fee_funding_tax_sim_usd == Decimal("0")

    def test_horizon_met_true_when_duration_within_limit(self) -> None:
        inp = _pos_input(holding_duration_min=59, decision_action_time_horizon_min=60)
        assert self.resolver.resolve_position(inp).horizon_met is True

    def test_horizon_met_true_exactly_at_limit(self) -> None:
        inp = _pos_input(holding_duration_min=60, decision_action_time_horizon_min=60)
        assert self.resolver.resolve_position(inp).horizon_met is True

    def test_horizon_met_false_when_over_limit(self) -> None:
        inp = _pos_input(holding_duration_min=61, decision_action_time_horizon_min=60)
        assert self.resolver.resolve_position(inp).horizon_met is False

    def test_identity_fields_preserved(self) -> None:
        inp = _pos_input()
        result = self.resolver.resolve_position(inp)
        assert result.opening_action_id == _ACT_ID
        assert result.opening_run_id == _OPEN_RUN
        assert result.closing_run_id == _CLOSE_RUN
        assert result.experiment_id == _EXP_ID
        assert result.model_id == _MODEL
        assert result.symbol == _SYMBOL
        assert result.decision_action_confidence == Decimal("0.70")
        assert result.decision_action_time_horizon_min == 60
        assert result.holding_duration_min == 45

    def test_sum_fees_preserved_in_result(self) -> None:
        inp = _pos_input(sum_fees_usd=Decimal("3.75"))
        result = self.resolver.resolve_position(inp)
        assert result.sum_fees_usd == Decimal("3.75")

    def test_large_loss_position(self) -> None:
        inp = _pos_input(
            realized_pnl_gross_usd=Decimal("-500.00"),
            sum_fees_usd=Decimal("10.00"),
            sum_funding_usd=Decimal("0.00"),
        )
        result = self.resolver.resolve_position(inp)
        assert result.pnl_net_fee_usd == Decimal("-510.00")
        assert result.was_profitable_net is False


class TestResolveHoldFlat:
    """Tests for OutcomeResolver.resolve_hold_flat (D2 counterfactual rule)."""

    def setup_method(self) -> None:
        self.resolver = OutcomeResolver()

    def test_tiny_price_move_below_threshold_is_profitable(self) -> None:
        # |0.1%| < 0.2% threshold → HOLD was correct
        inp = _hf_input(
            price_at_decision=Decimal("50000.00"),
            price_at_horizon=Decimal("50050.00"),  # +0.1%
            fee_roundtrip_pct=Decimal("0.002"),
        )
        assert self.resolver.resolve_hold_flat(inp).was_profitable_net is True

    def test_large_price_move_above_threshold_is_not_profitable(self) -> None:
        # |1%| > 0.2% threshold → HOLD missed the move
        inp = _hf_input(
            price_at_decision=Decimal("50000.00"),
            price_at_horizon=Decimal("50500.00"),  # +1%
            fee_roundtrip_pct=Decimal("0.002"),
        )
        assert self.resolver.resolve_hold_flat(inp).was_profitable_net is False

    def test_price_move_exactly_at_threshold_is_profitable(self) -> None:
        # |0.2%| == 0.2% threshold → boundary: ≤ is inclusive → True
        inp = _hf_input(
            price_at_decision=Decimal("50000.00"),
            price_at_horizon=Decimal("50100.00"),  # exactly +0.2%
            fee_roundtrip_pct=Decimal("0.002"),
        )
        assert self.resolver.resolve_hold_flat(inp).was_profitable_net is True

    def test_price_drop_below_threshold_is_profitable(self) -> None:
        # |-0.1%| < 0.2% threshold → HOLD was correct even with price drop
        inp = _hf_input(
            price_at_decision=Decimal("50000.00"),
            price_at_horizon=Decimal("49950.00"),  # -0.1%
            fee_roundtrip_pct=Decimal("0.002"),
        )
        assert self.resolver.resolve_hold_flat(inp).was_profitable_net is True

    def test_price_drop_above_threshold_is_not_profitable(self) -> None:
        # |-1%| > 0.2% → a SHORT would have made money, HOLD was wrong
        inp = _hf_input(
            price_at_decision=Decimal("50000.00"),
            price_at_horizon=Decimal("49500.00"),  # -1%
            fee_roundtrip_pct=Decimal("0.002"),
        )
        assert self.resolver.resolve_hold_flat(inp).was_profitable_net is False

    def test_no_price_change_is_profitable(self) -> None:
        inp = _hf_input(
            price_at_decision=Decimal("50000.00"),
            price_at_horizon=Decimal("50000.00"),
        )
        assert self.resolver.resolve_hold_flat(inp).was_profitable_net is True

    def test_all_pnl_fields_are_zero(self) -> None:
        result = self.resolver.resolve_hold_flat(_hf_input())
        assert result.realized_pnl_gross_usd == Decimal("0")
        assert result.sum_fees_usd == Decimal("0")
        assert result.sum_funding_usd == Decimal("0")
        assert result.pnl_net_fee_usd == Decimal("0")
        assert result.pnl_net_fee_funding_usd == Decimal("0")
        assert result.pnl_net_fee_funding_tax_sim_usd == Decimal("0")

    def test_holding_duration_equals_time_horizon(self) -> None:
        inp = _hf_input(decision_action_time_horizon_min=90)
        result = self.resolver.resolve_hold_flat(inp)
        assert result.holding_duration_min == 90

    def test_horizon_met_always_true(self) -> None:
        result = self.resolver.resolve_hold_flat(_hf_input())
        assert result.horizon_met is True

    def test_identity_fields_preserved(self) -> None:
        inp = _hf_input()
        result = self.resolver.resolve_hold_flat(inp)
        assert result.opening_action_id == _ACT_ID
        assert result.opening_run_id == _OPEN_RUN
        assert result.closing_run_id == _CLOSE_RUN
        assert result.experiment_id == _EXP_ID
        assert result.model_id == _MODEL
        assert result.symbol == _SYMBOL
        assert result.decision_action_confidence == Decimal("0.60")
        assert result.decision_action_time_horizon_min == 60

    def test_uses_absolute_price_change(self) -> None:
        # Symmetric: +0.1% and -0.1% should both be profitable
        up_inp = _hf_input(
            price_at_decision=Decimal("40000"),
            price_at_horizon=Decimal("40040"),  # +0.1%
            fee_roundtrip_pct=Decimal("0.002"),
        )
        down_inp = _hf_input(
            price_at_decision=Decimal("40000"),
            price_at_horizon=Decimal("39960"),  # -0.1%
            fee_roundtrip_pct=Decimal("0.002"),
        )
        assert self.resolver.resolve_hold_flat(up_inp).was_profitable_net is True
        assert self.resolver.resolve_hold_flat(down_inp).was_profitable_net is True
