"""Unit tests for 4 guardrail Strategia C+ (§7.4, §9.2 cases)."""

from decimal import Decimal

from aiat.domain.enums import EntryType, Side
from aiat.domain.schemas import ActionDecision, GuardrailReport, TradeDecision
from aiat.execution.guardrails import Guardrails

# Default guardrail parameters (PRD §10.3 / AIAT_* env defaults)
_MAX_SIZE_PCT = Decimal("0.20")
_HARD_MAX_LEVERAGE = Decimal("10")
_MIN_OPEN_CONFIDENCE = Decimal("0.4")


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _long(
    symbol: str = "BTC",
    *,
    size_pct: Decimal = Decimal("0.10"),
    leverage: Decimal = Decimal("3"),
    confidence: Decimal = Decimal("0.8000"),
    stop_loss_pct: Decimal | None = Decimal("0.0500"),
    take_profit_pct: Decimal | None = Decimal("0.1000"),
) -> ActionDecision:
    return ActionDecision(
        symbol=symbol,  # type: ignore[arg-type]
        side=Side.LONG,
        leverage=leverage,
        size_pct=size_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        entry_type=EntryType.MARKET,
        limit_price=None,
        confidence=confidence,
        time_horizon_min=60,
        action_reasoning="Bullish signal from multiple indicators confirms uptrend",
        action_key_signals=["technical.rsi_extreme"],
    )


def _hold(symbol: str = "BTC", *, confidence: Decimal = Decimal("0.5000")) -> ActionDecision:
    return ActionDecision(
        symbol=symbol,  # type: ignore[arg-type]
        side=Side.HOLD,
        leverage=Decimal("0"),
        size_pct=Decimal("0"),
        stop_loss_pct=None,
        take_profit_pct=None,
        entry_type=EntryType.NONE,
        limit_price=None,
        confidence=confidence,
        time_horizon_min=60,
        action_reasoning="No clear signal, maintaining current position for now",
        action_key_signals=[],
    )


def _decision(
    btc: ActionDecision | None = None,
    eth: ActionDecision | None = None,
    sol: ActionDecision | None = None,
) -> TradeDecision:
    """Build a valid TradeDecision with 3 actions (BTC/ETH/SOL)."""
    return TradeDecision(
        portfolio_reasoning=(
            "Portfolio analysis shows opportunity in BTC while ETH and SOL remain "
            "neutral awaiting further confirmation of market trend"
        ),
        risk_assessment=(
            "Market volatility moderate, BTC dominance increasing with clear support levels"
        ),
        portfolio_confidence=Decimal("0.7000"),
        actions=[
            btc if btc is not None else _long("BTC"),
            eth if eth is not None else _hold("ETH"),
            sol if sol is not None else _hold("SOL"),
        ],
    )


def _decision_raw(*actions: ActionDecision) -> TradeDecision:
    """Build a TradeDecision bypassing validators (for malformed-action tests)."""
    return TradeDecision.model_construct(
        portfolio_reasoning=(
            "Portfolio analysis shows opportunity in BTC while ETH and SOL remain "
            "neutral awaiting further confirmation of market trend"
        ),
        risk_assessment=(
            "Market volatility moderate, BTC dominance increasing with clear support levels"
        ),
        portfolio_confidence=Decimal("0.7000"),
        actions=list(actions),
    )


def _report(reports: list[GuardrailReport], symbol: str) -> GuardrailReport:
    return next(r for r in reports if r.symbol == symbol)


# ---------------------------------------------------------------------------
# Clean pass-through
# ---------------------------------------------------------------------------


class TestCleanPassthrough:
    def test_no_flags_on_valid_long(self) -> None:
        g = Guardrails()
        result, reports = g.apply(
            _decision(),
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        r = _report(reports, "BTC")
        assert not r.leverage_clamped
        assert not r.size_pct_clamped
        assert not r.forced_hold
        assert r.original_side == Side.LONG
        assert r.final_action.side == Side.LONG

    def test_returns_three_reports_with_correct_symbols(self) -> None:
        g = Guardrails()
        _, reports = g.apply(
            _decision(),
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        assert len(reports) == 3
        assert {r.symbol for r in reports} == {"BTC", "ETH", "SOL"}

    def test_result_decision_symbols_unchanged(self) -> None:
        g = Guardrails()
        result, _ = g.apply(
            _decision(),
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        assert {a.symbol for a in result.actions} == {"BTC", "ETH", "SOL"}


# ---------------------------------------------------------------------------
# Guardrail 1 — SL/TP mandatory (§9.2: HOLD forced if SL missing on LONG)
# ---------------------------------------------------------------------------


class TestGuardrail1SLTPMandatory:
    def _malformed_long(
        self, *, stop_loss_pct: Decimal | None, take_profit_pct: Decimal | None
    ) -> ActionDecision:
        """Create a LONG action bypassing schema validation (for g1 defense-in-depth tests)."""
        return ActionDecision.model_construct(
            symbol="BTC",
            side=Side.LONG,
            leverage=Decimal("3"),
            size_pct=Decimal("0.10"),
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            entry_type=EntryType.MARKET,
            limit_price=None,
            confidence=Decimal("0.8000"),
            time_horizon_min=60,
            action_reasoning="Bullish signal from multiple indicators confirms uptrend",
            action_key_signals=["technical.rsi_extreme"],
        )

    def test_hold_forced_if_sl_missing(self) -> None:
        btc = self._malformed_long(stop_loss_pct=None, take_profit_pct=Decimal("0.10"))
        decision = _decision_raw(btc, _hold("ETH"), _hold("SOL"))
        _, reports = Guardrails().apply(
            decision,
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        r = _report(reports, "BTC")
        assert r.forced_hold
        assert r.original_side == Side.LONG
        assert r.final_action.side == Side.HOLD
        assert r.final_action.size_pct == Decimal("0")
        assert r.final_action.leverage == Decimal("0")
        assert not r.size_pct_clamped
        assert not r.leverage_clamped

    def test_hold_forced_if_tp_missing(self) -> None:
        btc = self._malformed_long(stop_loss_pct=Decimal("0.05"), take_profit_pct=None)
        decision = _decision_raw(btc, _hold("ETH"), _hold("SOL"))
        _, reports = Guardrails().apply(
            decision,
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        r = _report(reports, "BTC")
        assert r.forced_hold
        assert r.final_action.side == Side.HOLD

    def test_hold_forced_if_both_sl_tp_missing(self) -> None:
        btc = self._malformed_long(stop_loss_pct=None, take_profit_pct=None)
        decision = _decision_raw(btc, _hold("ETH"), _hold("SOL"))
        _, reports = Guardrails().apply(
            decision,
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        assert _report(reports, "BTC").forced_hold

    def test_g1_skips_g2_g3_clamps(self) -> None:
        """When guardrail 1 forces HOLD, size and leverage clamps are not applied."""
        btc = self._malformed_long(stop_loss_pct=None, take_profit_pct=None)
        decision = _decision_raw(btc, _hold("ETH"), _hold("SOL"))
        _, reports = Guardrails().apply(
            decision,
            max_size_pct=Decimal("0.05"),  # would clamp 0.10 → 0.05
            hard_max_leverage=Decimal("2"),  # would clamp 3 → 2
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        r = _report(reports, "BTC")
        assert r.forced_hold
        assert not r.size_pct_clamped  # never reached
        assert not r.leverage_clamped  # never reached


# ---------------------------------------------------------------------------
# Guardrail 2 — size_pct clamp (§9.2: 0.50 → 0.20)
# ---------------------------------------------------------------------------


class TestGuardrail2SizePctClamp:
    def test_size_pct_clamped_to_max(self) -> None:
        # §9.2: size_pct=0.50 clamped to AIAT_MAX_SIZE_PCT=0.20
        btc = _long("BTC", size_pct=Decimal("0.5000"))
        _, reports = Guardrails().apply(
            _decision(btc=btc),
            max_size_pct=Decimal("0.20"),
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        r = _report(reports, "BTC")
        assert r.size_pct_clamped
        assert r.final_action.size_pct == Decimal("0.20")
        assert not r.forced_hold

    def test_size_pct_at_limit_not_clamped(self) -> None:
        btc = _long("BTC", size_pct=Decimal("0.2000"))
        _, reports = Guardrails().apply(
            _decision(btc=btc),
            max_size_pct=Decimal("0.20"),
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        assert not _report(reports, "BTC").size_pct_clamped

    def test_size_pct_within_limit_not_clamped(self) -> None:
        btc = _long("BTC", size_pct=Decimal("0.1500"))
        _, reports = Guardrails().apply(
            _decision(btc=btc),
            max_size_pct=Decimal("0.20"),
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        r = _report(reports, "BTC")
        assert not r.size_pct_clamped
        assert r.final_action.size_pct == Decimal("0.1500")


# ---------------------------------------------------------------------------
# Guardrail 3 — leverage clamp (§9.2: 20 → 1 + confidence×9)
# ---------------------------------------------------------------------------


class TestGuardrail3LeverageClamp:
    def test_leverage_clamped_by_confidence_formula(self) -> None:
        # §9.2: leverage=20 clamped to 1 + confidence*9
        # confidence=0.8 → cap = 1 + 7.2 = 8.2, hard_max=10 → cap=8.2
        btc = _long("BTC", leverage=Decimal("20"), confidence=Decimal("0.8000"))
        _, reports = Guardrails().apply(
            _decision(btc=btc),
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=Decimal("10"),
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        r = _report(reports, "BTC")
        assert r.leverage_clamped
        assert r.final_action.leverage == Decimal("8.2")
        assert not r.forced_hold

    def test_leverage_clamped_to_hard_max_when_lower(self) -> None:
        # confidence=1.0 → formula gives 10.0, hard_max=5 → cap=5
        btc = _long("BTC", leverage=Decimal("20"), confidence=Decimal("1.0000"))
        _, reports = Guardrails().apply(
            _decision(btc=btc),
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=Decimal("5"),
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        r = _report(reports, "BTC")
        assert r.leverage_clamped
        assert r.final_action.leverage == Decimal("5")

    def test_leverage_at_cap_not_clamped(self) -> None:
        # confidence=0.8 → cap=8.2; leverage=8 < 8.2 → no clamp
        btc = _long("BTC", leverage=Decimal("8"), confidence=Decimal("0.8000"))
        _, reports = Guardrails().apply(
            _decision(btc=btc),
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=Decimal("10"),
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        assert not _report(reports, "BTC").leverage_clamped

    def test_leverage_cap_rounds_down(self) -> None:
        # confidence=0.5555 → 1 + 0.5555*9 = 1 + 4.9995 = 5.9995 → ROUND_DOWN to 5.99
        btc = _long("BTC", leverage=Decimal("20"), confidence=Decimal("0.5555"))
        _, reports = Guardrails().apply(
            _decision(btc=btc),
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=Decimal("10"),
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        r = _report(reports, "BTC")
        assert r.leverage_clamped
        assert r.final_action.leverage == Decimal("5.99")


# ---------------------------------------------------------------------------
# Guardrail 4 — confidence gate (§9.2: 0.3 → forced HOLD, threshold=0.4)
# ---------------------------------------------------------------------------


class TestGuardrail4ConfidenceGate:
    def test_low_confidence_forces_hold(self) -> None:
        # §9.2: confidence=0.3 → forced HOLD (AIAT_MIN_OPEN_CONFIDENCE=0.4)
        btc = _long("BTC", confidence=Decimal("0.3000"))
        _, reports = Guardrails().apply(
            _decision(btc=btc),
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=Decimal("0.4"),
        )
        r = _report(reports, "BTC")
        assert r.forced_hold
        assert r.original_side == Side.LONG
        assert r.final_action.side == Side.HOLD

    def test_confidence_at_threshold_passes(self) -> None:
        # confidence=0.4, min_open=0.4 → 0.4 is NOT < 0.4 → not forced
        btc = _long("BTC", confidence=Decimal("0.4000"))
        _, reports = Guardrails().apply(
            _decision(btc=btc),
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=Decimal("0.4"),
        )
        r = _report(reports, "BTC")
        assert not r.forced_hold
        assert r.final_action.side == Side.LONG

    def test_confidence_above_threshold_passes(self) -> None:
        btc = _long("BTC", confidence=Decimal("0.9000"))
        _, reports = Guardrails().apply(
            _decision(btc=btc),
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=Decimal("0.4"),
        )
        assert not _report(reports, "BTC").forced_hold


# ---------------------------------------------------------------------------
# Guardrail ordering — §9.2: SL → size → leverage → confidence
# ---------------------------------------------------------------------------


class TestGuardrailOrdering:
    def test_g1_prevents_g2_and_g3(self) -> None:
        """SL missing → HOLD at guardrail 1; size/leverage clamps never applied."""
        btc = ActionDecision.model_construct(
            symbol="BTC",
            side=Side.LONG,
            leverage=Decimal("20"),  # would be clamped by g3
            size_pct=Decimal("0.5000"),  # would be clamped by g2
            stop_loss_pct=None,
            take_profit_pct=Decimal("0.1000"),
            entry_type=EntryType.MARKET,
            limit_price=None,
            confidence=Decimal("0.8000"),
            time_horizon_min=60,
            action_reasoning="Bullish signal from multiple indicators confirms uptrend",
            action_key_signals=["technical.rsi_extreme"],
        )
        decision = _decision_raw(btc, _hold("ETH"), _hold("SOL"))
        _, reports = Guardrails().apply(
            decision,
            max_size_pct=Decimal("0.20"),
            hard_max_leverage=Decimal("10"),
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        r = _report(reports, "BTC")
        assert r.forced_hold
        assert not r.size_pct_clamped
        assert not r.leverage_clamped

    def test_g2_and_g3_before_g4_all_flags_set(self) -> None:
        """§9.2: all 4 guardrails in sequence — size clamped, leverage clamped, then HOLD."""
        btc = _long(
            "BTC",
            size_pct=Decimal("0.5000"),  # g2: clamped to 0.20
            leverage=Decimal("20"),  # g3: clamped to cap
            confidence=Decimal("0.3000"),  # g4: forced HOLD (<0.4)
        )
        _, reports = Guardrails().apply(
            _decision(btc=btc),
            max_size_pct=Decimal("0.20"),
            hard_max_leverage=Decimal("10"),
            min_open_confidence=Decimal("0.4"),
        )
        r = _report(reports, "BTC")
        assert r.size_pct_clamped  # guardrail 2 fired
        assert r.leverage_clamped  # guardrail 3 fired
        assert r.forced_hold  # guardrail 4 fired
        assert r.final_action.side == Side.HOLD

    def test_g4_does_not_fire_for_already_held_by_g1(self) -> None:
        """Once g1 forces HOLD, g4 has nothing to do (already HOLD)."""
        btc = ActionDecision.model_construct(
            symbol="BTC",
            side=Side.LONG,
            leverage=Decimal("3"),
            size_pct=Decimal("0.10"),
            stop_loss_pct=None,  # triggers g1
            take_profit_pct=Decimal("0.10"),
            entry_type=EntryType.MARKET,
            limit_price=None,
            confidence=Decimal("0.1000"),  # would trigger g4 too, but g1 runs first
            time_horizon_min=60,
            action_reasoning="Bullish signal from multiple indicators confirms uptrend",
            action_key_signals=[],
        )
        decision = _decision_raw(btc, _hold("ETH"), _hold("SOL"))
        _, reports = Guardrails().apply(
            decision,
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=Decimal("0.4"),
        )
        r = _report(reports, "BTC")
        # forced_hold is set (by g1), but g4 didn't set it again (HOLD side already)
        assert r.forced_hold
        assert r.final_action.side == Side.HOLD


# ---------------------------------------------------------------------------
# HOLD/FLAT actions unaffected
# ---------------------------------------------------------------------------


class TestHoldFlatUnaffected:
    def test_hold_action_passes_unchanged(self) -> None:
        decision = _decision(
            btc=_hold("BTC"),
            eth=_hold("ETH"),
            sol=_hold("SOL"),
        )
        _, reports = Guardrails().apply(
            decision,
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        for r in reports:
            assert not r.forced_hold
            assert not r.size_pct_clamped
            assert not r.leverage_clamped
            assert r.original_side == Side.HOLD
            assert r.final_action.side == Side.HOLD
            assert r.final_action.size_pct == Decimal("0")
            assert r.final_action.leverage == Decimal("0")

    def test_hold_low_confidence_not_forced(self) -> None:
        """HOLD with confidence < min_open does NOT get re-forced (already HOLD)."""
        btc = _hold("BTC", confidence=Decimal("0.1000"))
        _, reports = Guardrails().apply(
            _decision(btc=btc),
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=Decimal("0.4"),
        )
        r = _report(reports, "BTC")
        assert not r.forced_hold
        assert r.final_action.side == Side.HOLD


# ---------------------------------------------------------------------------
# GuardrailReport structure
# ---------------------------------------------------------------------------


class TestGuardrailReportStructure:
    def test_report_original_side_preserved_on_force_hold(self) -> None:
        btc = ActionDecision.model_construct(
            symbol="BTC",
            side=Side.LONG,
            leverage=Decimal("3"),
            size_pct=Decimal("0.10"),
            stop_loss_pct=None,
            take_profit_pct=Decimal("0.10"),
            entry_type=EntryType.MARKET,
            limit_price=None,
            confidence=Decimal("0.8000"),
            time_horizon_min=60,
            action_reasoning="Bullish signal from multiple indicators confirms uptrend",
            action_key_signals=[],
        )
        decision = _decision_raw(btc, _hold("ETH"), _hold("SOL"))
        _, reports = Guardrails().apply(
            decision,
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        r = _report(reports, "BTC")
        assert r.original_side == Side.LONG
        assert r.final_action.side == Side.HOLD

    def test_report_is_guardrail_report_instance(self) -> None:
        _, reports = Guardrails().apply(
            _decision(),
            max_size_pct=_MAX_SIZE_PCT,
            hard_max_leverage=_HARD_MAX_LEVERAGE,
            min_open_confidence=_MIN_OPEN_CONFIDENCE,
        )
        for r in reports:
            assert isinstance(r, GuardrailReport)
