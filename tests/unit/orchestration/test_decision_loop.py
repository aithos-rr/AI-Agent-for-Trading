"""Unit tests for DecisionLoop (PRD §4.1) with all collaborators mocked."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiat.config.settings import AgentSettings
from aiat.domain.enums import (
    CloseReason,
    EntryType,
    ExecutionStatus,
    OrderKind,
    RunStatus,
    Side,
)
from aiat.domain.exceptions import ExecutionRejectedError, ExecutionTimeoutError
from aiat.domain.schemas import (
    ActionDecision,
    ContextBundle,
    CostEventData,
    GuardrailReport,
    LLMInvocationResult,
    NewsItem,
    OnChainSnapshot,
    OpenPositionSummary,
    PortfolioState,
    SentimentSnapshot,
    TechnicalIndicators,
    TradeDecision,
)
from aiat.execution.hyperliquid_client import (
    MockHyperliquidClient,
    OrderResult,
    PositionClosureInfo,
)
from aiat.orchestration.decision_loop import (
    DecisionLoop,
    _action_execution_outcome,
    _render_prompt,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

TICK_ID = "2026-06-14T14:30:00+00:00"
SCHEDULED_FOR = datetime(2026, 6, 14, 14, 30, tzinfo=UTC)
EXPERIMENT_ID = str(uuid.uuid4())
MODEL_ID = "openai-gpt4o"
RUN_ID = str(uuid.uuid4())
SNAPSHOT_ID = str(uuid.uuid4())
DECISION_ID = str(uuid.uuid4())
TEMPLATE_HASH = "abc" * 21 + "ab"  # 64 hex chars
GIT_SHA = "deadbeef" * 5


def _make_hold_action(symbol: str) -> ActionDecision:
    return ActionDecision(
        symbol=symbol,  # type: ignore[arg-type]
        side=Side.HOLD,
        leverage=Decimal("0"),
        size_pct=Decimal("0"),
        stop_loss_pct=None,
        take_profit_pct=None,
        entry_type=EntryType.NONE,
        limit_price=None,
        confidence=Decimal("0.5"),
        time_horizon_min=60,
        action_reasoning="Hold for stability" * 2,
        action_key_signals=[],
    )


def _make_long_action(symbol: str) -> ActionDecision:
    return ActionDecision(
        symbol=symbol,  # type: ignore[arg-type]
        side=Side.LONG,
        leverage=Decimal("2"),
        size_pct=Decimal("0.10"),
        stop_loss_pct=Decimal("0.05"),
        take_profit_pct=Decimal("0.10"),
        entry_type=EntryType.MARKET,
        limit_price=None,
        confidence=Decimal("0.75"),
        time_horizon_min=120,
        action_reasoning="Bullish momentum detected based on RSI/MACD alignment",
        action_key_signals=["technical.rsi_extreme"],
    )


def _make_trade_decision(btc_action: ActionDecision | None = None) -> TradeDecision:
    btc = btc_action or _make_hold_action("BTC")
    return TradeDecision(
        portfolio_reasoning=(
            "Market conditions suggest cautious approach across all positions"
            " for the next 15 minutes"
        ),
        risk_assessment=(
            "Medium risk environment; volatility elevated but manageable with current exposure"
        ),
        portfolio_confidence=Decimal("0.6"),
        actions=[btc, _make_hold_action("ETH"), _make_hold_action("SOL")],
    )


def _make_invocation_result(decision: TradeDecision | None = None) -> LLMInvocationResult:
    d = decision or _make_trade_decision()
    return LLMInvocationResult(
        decision=d,
        cost=CostEventData(
            input_tokens=1000,
            output_tokens=200,
            reasoning_tokens=0,
            cost_usd=Decimal("0.01000000"),
            pricing_snapshot={"input": Decimal("0.01"), "output": Decimal("0.03")},
            n_attempts=1,
        ),
        latency_ms=1500,
        raw_response_id="resp-123",
        raw_payload={"model": "gpt-4o"},
        fallback_used=False,
        provider_snapshot="openai",
        model_name_api_snapshot="gpt-4o",
        temperature=Decimal("0.7"),
        top_p=None,
        max_tokens=4096,
        seed=None,
    )


def _make_portfolio_state(with_position: bool = False) -> PortfolioState:
    positions = []
    if with_position:
        positions = [
            OpenPositionSummary(
                symbol="BTC",
                side="LONG",
                entry_price=Decimal("65000"),
                current_price=Decimal("67000"),
                size_units=Decimal("0.01"),
                leverage=Decimal("2"),
                unrealized_pnl_usd=Decimal("20"),
                age_minutes=30,
            )
        ]
    return PortfolioState(
        equity_usd=Decimal("10000"),
        available_usd=Decimal("9500"),
        margin_used_usd=Decimal("500"),
        n_open_positions=len(positions),
        unrealized_pnl_usd=Decimal("0"),
        open_positions=positions,
    )


def _make_context_bundle() -> ContextBundle:
    tech = TechnicalIndicators(
        symbol="BTC",
        price_usd=Decimal("65000"),
        rsi_14=Decimal("55"),
        macd_signal_diff=Decimal("100"),
        ema_20=Decimal("64000"),
        ema_50=Decimal("63000"),
        bollinger_upper=Decimal("68000"),
        bollinger_lower=Decimal("62000"),
        atr_14=Decimal("500"),
        volume_24h_usd=Decimal("1000000000"),
    )
    return ContextBundle(
        tick_id=TICK_ID,
        tick_at=TICK_ID,
        technical=[
            tech,
            tech.model_copy(update={"symbol": "ETH", "price_usd": Decimal("3500")}),
            tech.model_copy(update={"symbol": "SOL", "price_usd": Decimal("180")}),
        ],
        sentiment=SentimentSnapshot(
            fear_greed_index=55,
            fear_greed_label="greed",
            fetched_at=TICK_ID,
        ),
        news=[
            NewsItem(
                title="BTC price update",
                summary="Bitcoin consolidates",
                source="coindesk",
                published_at=TICK_ID,
            )
        ],
        onchain=[
            OnChainSnapshot(
                symbol="BTC",
                funding_rate_8h=Decimal("0.0001"),
                open_interest_usd=Decimal("10000000"),
                premium=Decimal("-0.0002"),
                liquidations_24h_usd=Decimal("1000000"),
            )
        ],
        source_timestamps={"technical_btc": TICK_ID},
    )


_BASE_AGENT: dict[str, object] = {
    "experiment_id": EXPERIMENT_ID,
    "git_commit_sha": GIT_SHA,
    "database_url": "postgresql+asyncpg://x:x@localhost/x",
    "network": "testnet",
    "service_role": "agent",
    "model_id": MODEL_ID,
    "prompt_template_hash": TEMPLATE_HASH,
    "schema_version": "v1",
    "llm_provider": "openai",
    "model_name_api": "gpt-4o",
    "openai_api_key": "sk-test",
    "hl_wallet_private_key": "0x" + "0" * 64,
    "hl_wallet_address": "0x" + "0" * 40,
    "llm_gateway": "direct",
    "max_size_pct": Decimal("0.20"),
    "hard_max_leverage": Decimal("10"),
    "min_open_confidence": Decimal("0.4"),
    "hard_timeout_seconds": 180,
}


def _make_agent_settings(**overrides: object) -> AgentSettings:
    """Build AgentSettings without loading from env (matches test_lifecycle.py pattern)."""
    return AgentSettings(**{**_BASE_AGENT, **overrides})  # type: ignore[arg-type]


def _make_mock_snapshot() -> MagicMock:
    """Return a mock ContextSnapshot with the fields used by decision_loop."""
    snap = MagicMock()
    snap.id = uuid.UUID(SNAPSHOT_ID)
    snap.context_json = _make_context_bundle().model_dump(mode="json")
    return snap


def _make_mock_template() -> MagicMock:
    tmpl = MagicMock()
    tmpl.template_text = "You are a trading agent. Make decisions based on the context."
    tmpl.confidence_def = "confidence = probability of positive net PnL"
    tmpl.sha256_hash = TEMPLATE_HASH
    return tmpl


def _make_mock_db_action(symbol: str) -> MagicMock:
    action = MagicMock()
    action.id = uuid.uuid4()
    action.symbol = symbol
    return action


def _make_session_factory(session: MagicMock) -> MagicMock:
    """Wrap mock session in a factory that supports `async with session_factory() as session:`."""
    factory = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    factory.return_value = cm
    return factory


def _setup_session(
    snap: MagicMock | None,
    template: MagicMock,
    run_id: str = RUN_ID,
    decision_id: str = DECISION_ID,
    db_actions: list[MagicMock] | None = None,
    open_positions: list[MagicMock] | None = None,
) -> MagicMock:
    """Build a fully-configured mock AsyncSession for the happy path."""
    session = AsyncMock()
    session.commit = AsyncMock()

    # session.get: ContextSnapshot is read via SnapshotsRepository (not session.get),
    # PromptTemplate is read via session.get
    session.get = AsyncMock(return_value=template)

    # session.execute for DecisionAction query
    mock_result = MagicMock()
    actions = db_actions or [
        _make_mock_db_action("BTC"),
        _make_mock_db_action("ETH"),
        _make_mock_db_action("SOL"),
    ]
    mock_result.scalars.return_value.all.return_value = actions
    session.execute = AsyncMock(return_value=mock_result)

    return session


# ---------------------------------------------------------------------------
# Tests for _render_prompt helper
# ---------------------------------------------------------------------------


class TestRenderPrompt:
    def test_returns_rendered_text_and_hash(self) -> None:
        bundle = _make_context_bundle()
        portfolio = _make_portfolio_state()
        text, h = _render_prompt("TEMPLATE", bundle, portfolio, "CONF_DEF")
        assert "TEMPLATE" in text
        assert "MARKET CONTEXT" in text
        assert "PORTFOLIO STATE" in text
        assert "CONFIDENCE DEFINITION" in text
        assert len(h) == 64  # sha256 hex

    def test_hash_is_deterministic(self) -> None:
        bundle = _make_context_bundle()
        portfolio = _make_portfolio_state()
        _, h1 = _render_prompt("T", bundle, portfolio, "C")
        _, h2 = _render_prompt("T", bundle, portfolio, "C")
        assert h1 == h2

    def test_different_inputs_different_hash(self) -> None:
        bundle = _make_context_bundle()
        portfolio = _make_portfolio_state()
        _, h1 = _render_prompt("T1", bundle, portfolio, "C")
        _, h2 = _render_prompt("T2", bundle, portfolio, "C")
        assert h1 != h2


# ---------------------------------------------------------------------------
# Tests for DecisionLoop.run_once — happy path (all HOLD)
# ---------------------------------------------------------------------------


class TestDecisionLoopRunOnce:
    def _make_loop(
        self,
        session: MagicMock,
        snap: MagicMock | None,
        run_id: str = RUN_ID,
        decision_id: str = DECISION_ID,
        portfolio: PortfolioState | None = None,
        invocation: LLMInvocationResult | None = None,
    ) -> tuple[DecisionLoop, MagicMock, MagicMock, MagicMock]:
        settings = _make_agent_settings()
        factory = _make_session_factory(session)

        # Mock HL client
        hl_client = AsyncMock()
        hl_client.fetch_portfolio_state = AsyncMock(
            return_value=portfolio or _make_portfolio_state()
        )
        hl_client.execute_action = AsyncMock(return_value=[])
        hl_client.check_position_closure = AsyncMock(return_value=None)

        # Mock LLM client
        llm_client = AsyncMock()
        llm_client.invoke = AsyncMock(return_value=invocation or _make_invocation_result())

        # Patch all repositories used inside _execute_tick
        guardrails = MagicMock()
        decision = (invocation or _make_invocation_result()).decision
        reports = [
            GuardrailReport(
                symbol=a.symbol,
                original_side=a.side,
                leverage_clamped=False,
                size_pct_clamped=False,
                forced_hold=False,
                final_action=a,
            )
            for a in decision.actions
        ]
        guardrails.apply = MagicMock(return_value=(decision, reports))

        loop = DecisionLoop(
            settings=settings,
            llm_client=llm_client,
            hl_client=hl_client,
            session_factory=factory,
            guardrails=guardrails,
        )
        return loop, hl_client, llm_client, guardrails

    @pytest.mark.asyncio
    async def test_happy_path_returns_run_id(self) -> None:
        snap = _make_mock_snapshot()
        template = _make_mock_template()
        session = _setup_session(snap, template)

        with (
            patch("aiat.orchestration.decision_loop.SnapshotsRepository") as MockSnapshotsRepo,
            patch("aiat.orchestration.decision_loop.RunsRepository") as MockRunsRepo,
            patch("aiat.orchestration.decision_loop.DecisionsRepository") as MockDecisionsRepo,
            patch("aiat.orchestration.decision_loop.PositionsRepository") as MockPositionsRepo,
        ):
            # SnapshotsRepository.get_context_snapshot returns snapshot then snapshot
            mock_sr = AsyncMock()
            mock_sr.get_context_snapshot = AsyncMock(return_value=snap)
            mock_sr.persist_account_snapshot = AsyncMock(return_value=str(uuid.uuid4()))
            MockSnapshotsRepo.return_value = mock_sr

            # RunsRepository
            mock_rr = AsyncMock()
            mock_rr.create_run = AsyncMock(return_value=RUN_ID)
            mock_rr.update_status = AsyncMock()
            mock_rr.log_error = AsyncMock()
            MockRunsRepo.return_value = mock_rr

            # DecisionsRepository
            mock_dr = AsyncMock()
            mock_dr.persist_decision = AsyncMock(return_value=DECISION_ID)
            MockDecisionsRepo.return_value = mock_dr

            # PositionsRepository
            mock_pr = AsyncMock()
            mock_pr.list_open_for_model = AsyncMock(return_value=[])
            MockPositionsRepo.return_value = mock_pr

            loop, hl, llm, guardrails = self._make_loop(session, snap)
            result = await loop.run_once(TICK_ID, SCHEDULED_FOR)

        assert result == RUN_ID

    @pytest.mark.asyncio
    async def test_happy_path_calls_collaborators_in_order(self) -> None:
        """Verify §4.1 ordering: context → portfolio → run → LLM → guardrails → persist."""
        snap = _make_mock_snapshot()
        template = _make_mock_template()
        session = _setup_session(snap, template)

        call_order: list[str] = []

        with (
            patch("aiat.orchestration.decision_loop.SnapshotsRepository") as MockSnapshotsRepo,
            patch("aiat.orchestration.decision_loop.RunsRepository") as MockRunsRepo,
            patch("aiat.orchestration.decision_loop.DecisionsRepository") as MockDecisionsRepo,
            patch("aiat.orchestration.decision_loop.PositionsRepository") as MockPositionsRepo,
        ):

            async def _get_snapshot(exp_id: str, tick: str) -> MagicMock:
                call_order.append("get_context_snapshot")
                return snap

            async def _fetch_portfolio() -> PortfolioState:
                call_order.append("fetch_portfolio_state")
                return _make_portfolio_state()

            async def _invoke(prompt: str, *, timeout_seconds: int = 90) -> LLMInvocationResult:
                call_order.append("llm_invoke")
                return _make_invocation_result()

            async def _persist_decision(**_kwargs: object) -> str:
                call_order.append("persist_decision")
                return DECISION_ID

            async def _create_run(**_kwargs: object) -> str:
                call_order.append("create_run")
                return RUN_ID

            async def _update_status(run_id: str, status: RunStatus, *args: object) -> None:
                call_order.append(f"update_status_{status.value}")

            mock_sr = AsyncMock()
            mock_sr.get_context_snapshot = AsyncMock(side_effect=_get_snapshot)
            mock_sr.persist_account_snapshot = AsyncMock(return_value=str(uuid.uuid4()))
            MockSnapshotsRepo.return_value = mock_sr

            mock_rr = AsyncMock()
            mock_rr.create_run = AsyncMock(side_effect=_create_run)
            mock_rr.update_status = AsyncMock(side_effect=_update_status)
            mock_rr.log_error = AsyncMock()
            MockRunsRepo.return_value = mock_rr

            mock_dr = AsyncMock()
            mock_dr.persist_decision = AsyncMock(side_effect=_persist_decision)
            MockDecisionsRepo.return_value = mock_dr

            mock_pr = AsyncMock()
            mock_pr.list_open_for_model = AsyncMock(return_value=[])
            MockPositionsRepo.return_value = mock_pr

            settings = _make_agent_settings()
            factory = _make_session_factory(session)
            hl_client = AsyncMock()
            hl_client.fetch_portfolio_state = AsyncMock(side_effect=_fetch_portfolio)
            hl_client.execute_action = AsyncMock(return_value=[])
            hl_client.check_position_closure = AsyncMock(return_value=None)
            llm_client = AsyncMock()
            llm_client.invoke = AsyncMock(side_effect=_invoke)

            inv = _make_invocation_result()
            reports = [
                GuardrailReport(
                    symbol=a.symbol,
                    original_side=a.side,
                    leverage_clamped=False,
                    size_pct_clamped=False,
                    forced_hold=False,
                    final_action=a,
                )
                for a in inv.decision.actions
            ]
            guardrails = MagicMock()
            guardrails.apply = MagicMock(return_value=(inv.decision, reports))

            loop = DecisionLoop(
                settings=settings,
                llm_client=llm_client,
                hl_client=hl_client,
                session_factory=factory,
                guardrails=guardrails,
            )
            await loop.run_once(TICK_ID, SCHEDULED_FOR)

        # Verify ordering: context → portfolio → run → LLM → persist → success
        ctx_idx = call_order.index("get_context_snapshot")
        portfolio_idx = call_order.index("fetch_portfolio_state")
        run_idx = call_order.index("create_run")
        llm_idx = call_order.index("llm_invoke")
        persist_idx = call_order.index("persist_decision")
        success_idx = call_order.index("update_status_success")

        assert ctx_idx < portfolio_idx
        assert portfolio_idx < run_idx
        assert run_idx < llm_idx
        assert llm_idx < persist_idx
        assert persist_idx < success_idx

    @pytest.mark.asyncio
    async def test_missed_tick_returns_none(self) -> None:
        """When context_snapshot unavailable after retries → return None, no run created."""
        session = _setup_session(None, _make_mock_template())

        with (
            patch("aiat.orchestration.decision_loop.SnapshotsRepository") as MockSnapshotsRepo,
            patch("aiat.orchestration.decision_loop.RunsRepository") as MockRunsRepo,
            patch("aiat.orchestration.decision_loop.DecisionsRepository") as MockDecisionsRepo,
            patch("aiat.orchestration.decision_loop.PositionsRepository"),
            patch("aiat.orchestration.decision_loop.asyncio.sleep"),
        ):
            mock_sr = AsyncMock()
            mock_sr.get_context_snapshot = AsyncMock(return_value=None)  # always None
            MockSnapshotsRepo.return_value = mock_sr

            mock_rr = AsyncMock()
            mock_rr.log_error = AsyncMock()
            mock_rr.create_run = AsyncMock()
            MockRunsRepo.return_value = mock_rr

            mock_dr = AsyncMock()
            MockDecisionsRepo.return_value = mock_dr

            settings = _make_agent_settings()
            factory = _make_session_factory(session)
            hl_client = AsyncMock()
            llm_client = AsyncMock()
            loop = DecisionLoop(
                settings=settings,
                llm_client=llm_client,
                hl_client=hl_client,
                session_factory=factory,
            )
            result = await loop.run_once(TICK_ID, SCHEDULED_FOR)

        assert result is None
        mock_rr.log_error.assert_called_once()
        mock_rr.create_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_missed_tick_retries_3_times(self) -> None:
        """context_snapshot lookup is retried exactly 3 times before giving up."""
        session = _setup_session(None, _make_mock_template())

        with (
            patch("aiat.orchestration.decision_loop.SnapshotsRepository") as MockSnapshotsRepo,
            patch("aiat.orchestration.decision_loop.RunsRepository") as MockRunsRepo,
            patch("aiat.orchestration.decision_loop.PositionsRepository"),
            patch("aiat.orchestration.decision_loop.asyncio.sleep") as mock_sleep,
        ):
            mock_sr = AsyncMock()
            mock_sr.get_context_snapshot = AsyncMock(return_value=None)
            MockSnapshotsRepo.return_value = mock_sr

            mock_rr = AsyncMock()
            mock_rr.log_error = AsyncMock()
            mock_rr.create_run = AsyncMock()
            MockRunsRepo.return_value = mock_rr

            settings = _make_agent_settings()
            factory = _make_session_factory(session)
            loop = DecisionLoop(
                settings=settings,
                llm_client=AsyncMock(),
                hl_client=AsyncMock(),
                session_factory=factory,
            )
            await loop.run_once(TICK_ID, SCHEDULED_FOR)

        # 4 total calls (initial + 3 retries), 3 sleeps between them
        assert mock_sr.get_context_snapshot.call_count == 4
        assert mock_sleep.call_count == 3

    @pytest.mark.asyncio
    async def test_llm_invoke_called_with_rendered_prompt(self) -> None:
        """LLM client must receive the rendered prompt text."""
        snap = _make_mock_snapshot()
        template = _make_mock_template()
        session = _setup_session(snap, template)
        received_prompt: list[str] = []

        with (
            patch("aiat.orchestration.decision_loop.SnapshotsRepository") as MockSnapshotsRepo,
            patch("aiat.orchestration.decision_loop.RunsRepository") as MockRunsRepo,
            patch("aiat.orchestration.decision_loop.DecisionsRepository") as MockDecisionsRepo,
            patch("aiat.orchestration.decision_loop.PositionsRepository") as MockPositionsRepo,
        ):
            mock_sr = AsyncMock()
            mock_sr.get_context_snapshot = AsyncMock(return_value=snap)
            mock_sr.persist_account_snapshot = AsyncMock(return_value=str(uuid.uuid4()))
            MockSnapshotsRepo.return_value = mock_sr

            mock_rr = AsyncMock()
            mock_rr.create_run = AsyncMock(return_value=RUN_ID)
            mock_rr.update_status = AsyncMock()
            MockRunsRepo.return_value = mock_rr

            mock_dr = AsyncMock()
            mock_dr.persist_decision = AsyncMock(return_value=DECISION_ID)
            MockDecisionsRepo.return_value = mock_dr

            mock_pr = AsyncMock()
            mock_pr.list_open_for_model = AsyncMock(return_value=[])
            MockPositionsRepo.return_value = mock_pr

            async def _capture_invoke(
                prompt: str, *, timeout_seconds: int = 90
            ) -> LLMInvocationResult:
                received_prompt.append(prompt)
                return _make_invocation_result()

            settings = _make_agent_settings()
            factory = _make_session_factory(session)
            hl_client = AsyncMock()
            hl_client.fetch_portfolio_state = AsyncMock(return_value=_make_portfolio_state())
            hl_client.execute_action = AsyncMock(return_value=[])
            hl_client.check_position_closure = AsyncMock(return_value=None)
            llm_client = AsyncMock()
            llm_client.invoke = AsyncMock(side_effect=_capture_invoke)

            inv = _make_invocation_result()
            reports = [
                GuardrailReport(
                    symbol=a.symbol,
                    original_side=a.side,
                    leverage_clamped=False,
                    size_pct_clamped=False,
                    forced_hold=False,
                    final_action=a,
                )
                for a in inv.decision.actions
            ]
            guardrails = MagicMock()
            guardrails.apply = MagicMock(return_value=(inv.decision, reports))

            loop = DecisionLoop(
                settings=settings,
                llm_client=llm_client,
                hl_client=hl_client,
                session_factory=factory,
                guardrails=guardrails,
            )
            await loop.run_once(TICK_ID, SCHEDULED_FOR)

        assert len(received_prompt) == 1
        assert template.template_text in received_prompt[0]
        assert "MARKET CONTEXT" in received_prompt[0]
        assert "PORTFOLIO STATE" in received_prompt[0]

    @pytest.mark.asyncio
    async def test_guardrails_applied_with_settings_params(self) -> None:
        """Guardrails.apply() must be called with the settings guardrail parameters."""
        snap = _make_mock_snapshot()
        session = _setup_session(snap, _make_mock_template())

        with (
            patch("aiat.orchestration.decision_loop.SnapshotsRepository") as MockSnapshotsRepo,
            patch("aiat.orchestration.decision_loop.RunsRepository") as MockRunsRepo,
            patch("aiat.orchestration.decision_loop.DecisionsRepository") as MockDecisionsRepo,
            patch("aiat.orchestration.decision_loop.PositionsRepository") as MockPositionsRepo,
        ):
            mock_sr = AsyncMock()
            mock_sr.get_context_snapshot = AsyncMock(return_value=snap)
            mock_sr.persist_account_snapshot = AsyncMock(return_value=str(uuid.uuid4()))
            MockSnapshotsRepo.return_value = mock_sr

            mock_rr = AsyncMock()
            mock_rr.create_run = AsyncMock(return_value=RUN_ID)
            mock_rr.update_status = AsyncMock()
            MockRunsRepo.return_value = mock_rr

            mock_dr = AsyncMock()
            mock_dr.persist_decision = AsyncMock(return_value=DECISION_ID)
            MockDecisionsRepo.return_value = mock_dr

            mock_pr = AsyncMock()
            mock_pr.list_open_for_model = AsyncMock(return_value=[])
            MockPositionsRepo.return_value = mock_pr

            settings = _make_agent_settings(
                max_size_pct=Decimal("0.15"),
                hard_max_leverage=Decimal("8"),
                min_open_confidence=Decimal("0.5"),
            )
            factory = _make_session_factory(session)
            hl_client = AsyncMock()
            hl_client.fetch_portfolio_state = AsyncMock(return_value=_make_portfolio_state())
            hl_client.execute_action = AsyncMock(return_value=[])
            hl_client.check_position_closure = AsyncMock(return_value=None)
            llm_client = AsyncMock()
            inv = _make_invocation_result()
            llm_client.invoke = AsyncMock(return_value=inv)

            reports = [
                GuardrailReport(
                    symbol=a.symbol,
                    original_side=a.side,
                    leverage_clamped=False,
                    size_pct_clamped=False,
                    forced_hold=False,
                    final_action=a,
                )
                for a in inv.decision.actions
            ]
            guardrails = MagicMock()
            guardrails.apply = MagicMock(return_value=(inv.decision, reports))

            loop = DecisionLoop(
                settings=settings,
                llm_client=llm_client,
                hl_client=hl_client,
                session_factory=factory,
                guardrails=guardrails,
            )
            await loop.run_once(TICK_ID, SCHEDULED_FOR)

        guardrails.apply.assert_called_once()
        _, kwargs = guardrails.apply.call_args
        assert kwargs["max_size_pct"] == Decimal("0.15")
        assert kwargs["hard_max_leverage"] == Decimal("8")
        assert kwargs["min_open_confidence"] == Decimal("0.5")

    @pytest.mark.asyncio
    async def test_execute_action_called_for_long(self) -> None:
        """execute_action must be called for non-HOLD actions."""
        snap = _make_mock_snapshot()
        session = _setup_session(snap, _make_mock_template())

        long_action = _make_long_action("BTC")
        decision = TradeDecision(
            portfolio_reasoning=("Bull market detected; entering BTC long with SL/TP configured"),
            risk_assessment=(
                "Moderate risk; volatility is above average but trend is clearly bullish"
            ),
            portfolio_confidence=Decimal("0.75"),
            actions=[long_action, _make_hold_action("ETH"), _make_hold_action("SOL")],
        )
        inv = _make_invocation_result(decision)

        with (
            patch("aiat.orchestration.decision_loop.SnapshotsRepository") as MockSnapshotsRepo,
            patch("aiat.orchestration.decision_loop.RunsRepository") as MockRunsRepo,
            patch("aiat.orchestration.decision_loop.DecisionsRepository") as MockDecisionsRepo,
            patch("aiat.orchestration.decision_loop.PositionsRepository") as MockPositionsRepo,
        ):
            mock_sr = AsyncMock()
            mock_sr.get_context_snapshot = AsyncMock(return_value=snap)
            mock_sr.persist_account_snapshot = AsyncMock(return_value=str(uuid.uuid4()))
            MockSnapshotsRepo.return_value = mock_sr

            mock_rr = AsyncMock()
            mock_rr.create_run = AsyncMock(return_value=RUN_ID)
            mock_rr.update_status = AsyncMock()
            MockRunsRepo.return_value = mock_rr

            mock_dr = AsyncMock()
            mock_dr.persist_decision = AsyncMock(return_value=DECISION_ID)
            MockDecisionsRepo.return_value = mock_dr

            mock_pr = AsyncMock()
            mock_pr.list_open_for_model = AsyncMock(return_value=[])
            mock_pr.open_position = AsyncMock(return_value=str(uuid.uuid4()))
            MockPositionsRepo.return_value = mock_pr

            reports = [
                GuardrailReport(
                    symbol=a.symbol,
                    original_side=a.side,
                    leverage_clamped=False,
                    size_pct_clamped=False,
                    forced_hold=False,
                    final_action=a,
                )
                for a in decision.actions
            ]
            guardrails = MagicMock()
            guardrails.apply = MagicMock(return_value=(decision, reports))

            # HL mock returns entry+SL+TP for LONG BTC
            entry = OrderResult(
                hl_order_id=str(uuid.uuid4()),
                client_order_id=str(uuid.uuid4()),
                order_kind=OrderKind.ENTRY,
                status="filled",
                requested_price=None,
                filled_price=Decimal("65000"),
                requested_size_units=Decimal("0.01"),
                filled_size_units=Decimal("0.01"),
                slippage_bps=Decimal("5"),
                fee_usd=Decimal("1.00"),
                raw_response={},
            )
            settings = _make_agent_settings()
            factory = _make_session_factory(session)
            hl_client = AsyncMock()
            hl_client.fetch_portfolio_state = AsyncMock(return_value=_make_portfolio_state())
            hl_client.execute_action = AsyncMock(return_value=[entry])
            hl_client.check_position_closure = AsyncMock(return_value=None)
            llm_client = AsyncMock()
            llm_client.invoke = AsyncMock(return_value=inv)

            loop = DecisionLoop(
                settings=settings,
                llm_client=llm_client,
                hl_client=hl_client,
                session_factory=factory,
                guardrails=guardrails,
            )
            result = await loop.run_once(TICK_ID, SCHEDULED_FOR)

        assert result == RUN_ID
        # execute_action called once (BTC LONG; ETH+SOL are HOLD)
        assert hl_client.execute_action.call_count == 1
        call_args = hl_client.execute_action.call_args
        assert call_args[0][0].symbol == "BTC"
        assert call_args[0][0].side == Side.LONG

    @pytest.mark.asyncio
    async def test_timeout_updates_run_status(self) -> None:
        """When the tick exceeds hard_timeout_seconds, run status must be set to TIMEOUT."""
        snap = _make_mock_snapshot()
        session = _setup_session(snap, _make_mock_template())

        with (
            patch("aiat.orchestration.decision_loop.SnapshotsRepository") as MockSnapshotsRepo,
            patch("aiat.orchestration.decision_loop.RunsRepository") as MockRunsRepo,
            patch("aiat.orchestration.decision_loop.DecisionsRepository"),
            patch("aiat.orchestration.decision_loop.PositionsRepository"),
        ):
            mock_sr = AsyncMock()
            mock_sr.get_context_snapshot = AsyncMock(return_value=snap)
            mock_sr.persist_account_snapshot = AsyncMock(return_value=str(uuid.uuid4()))
            MockSnapshotsRepo.return_value = mock_sr

            mock_rr = AsyncMock()
            mock_rr.create_run = AsyncMock(return_value=RUN_ID)
            mock_rr.update_status = AsyncMock()
            mock_rr.log_error = AsyncMock()
            # update_status is called both inline and in _finalize_run (new factory call)
            MockRunsRepo.return_value = mock_rr

            settings = _make_agent_settings(hard_timeout_seconds=1)
            session2 = AsyncMock()
            session2.commit = AsyncMock()
            calls: list[MagicMock] = [session, session2]

            def _make_cm(s: MagicMock) -> MagicMock:
                cm = AsyncMock()
                cm.__aenter__ = AsyncMock(return_value=s)
                cm.__aexit__ = AsyncMock(return_value=None)
                return cm

            call_count = [0]

            def _factory() -> MagicMock:
                idx = call_count[0] % len(calls)
                call_count[0] += 1
                return _make_cm(calls[idx])

            factory = MagicMock(side_effect=_factory)

            async def _slow_invoke(
                prompt: str, *, timeout_seconds: int = 90
            ) -> LLMInvocationResult:
                await asyncio.sleep(10)  # simulate LLM taking too long
                return _make_invocation_result()

            hl_client = AsyncMock()
            hl_client.fetch_portfolio_state = AsyncMock(return_value=_make_portfolio_state())
            hl_client.execute_action = AsyncMock(return_value=[])
            hl_client.check_position_closure = AsyncMock(return_value=None)
            llm_client = AsyncMock()
            llm_client.invoke = AsyncMock(side_effect=_slow_invoke)

            inv = _make_invocation_result()
            reports = [
                GuardrailReport(
                    symbol=a.symbol,
                    original_side=a.side,
                    leverage_clamped=False,
                    size_pct_clamped=False,
                    forced_hold=False,
                    final_action=a,
                )
                for a in inv.decision.actions
            ]
            guardrails = MagicMock()
            guardrails.apply = MagicMock(return_value=(inv.decision, reports))

            loop = DecisionLoop(
                settings=settings,
                llm_client=llm_client,
                hl_client=hl_client,
                session_factory=factory,
                guardrails=guardrails,
            )
            result = await loop.run_once(TICK_ID, SCHEDULED_FOR)

        # Returns run_id (set before LLM invoke), not None
        assert result == RUN_ID
        # update_status called with TIMEOUT
        calls_args = mock_rr.update_status.call_args_list
        statuses = [c[0][1] for c in calls_args]
        assert RunStatus.TIMEOUT in statuses

    @pytest.mark.asyncio
    async def test_run_status_success_on_happy_path(self) -> None:
        """Final update_status must be called with RunStatus.SUCCESS."""
        snap = _make_mock_snapshot()
        session = _setup_session(snap, _make_mock_template())

        with (
            patch("aiat.orchestration.decision_loop.SnapshotsRepository") as MockSnapshotsRepo,
            patch("aiat.orchestration.decision_loop.RunsRepository") as MockRunsRepo,
            patch("aiat.orchestration.decision_loop.DecisionsRepository") as MockDecisionsRepo,
            patch("aiat.orchestration.decision_loop.PositionsRepository") as MockPositionsRepo,
        ):
            mock_sr = AsyncMock()
            mock_sr.get_context_snapshot = AsyncMock(return_value=snap)
            mock_sr.persist_account_snapshot = AsyncMock(return_value=str(uuid.uuid4()))
            MockSnapshotsRepo.return_value = mock_sr

            mock_rr = AsyncMock()
            mock_rr.create_run = AsyncMock(return_value=RUN_ID)
            mock_rr.update_status = AsyncMock()
            MockRunsRepo.return_value = mock_rr

            mock_dr = AsyncMock()
            mock_dr.persist_decision = AsyncMock(return_value=DECISION_ID)
            MockDecisionsRepo.return_value = mock_dr

            mock_pr = AsyncMock()
            mock_pr.list_open_for_model = AsyncMock(return_value=[])
            MockPositionsRepo.return_value = mock_pr

            settings = _make_agent_settings()
            factory = _make_session_factory(session)
            hl_client = AsyncMock()
            hl_client.fetch_portfolio_state = AsyncMock(return_value=_make_portfolio_state())
            hl_client.execute_action = AsyncMock(return_value=[])
            hl_client.check_position_closure = AsyncMock(return_value=None)
            llm_client = AsyncMock()
            inv = _make_invocation_result()
            llm_client.invoke = AsyncMock(return_value=inv)

            reports = [
                GuardrailReport(
                    symbol=a.symbol,
                    original_side=a.side,
                    leverage_clamped=False,
                    size_pct_clamped=False,
                    forced_hold=False,
                    final_action=a,
                )
                for a in inv.decision.actions
            ]
            guardrails = MagicMock()
            guardrails.apply = MagicMock(return_value=(inv.decision, reports))

            loop = DecisionLoop(
                settings=settings,
                llm_client=llm_client,
                hl_client=hl_client,
                session_factory=factory,
                guardrails=guardrails,
            )
            await loop.run_once(TICK_ID, SCHEDULED_FOR)

        mock_rr.update_status.assert_called_once_with(RUN_ID, RunStatus.SUCCESS)

    @pytest.mark.asyncio
    async def test_default_guardrails_used_when_none_passed(self) -> None:
        """If no guardrails arg, DecisionLoop creates a Guardrails() instance internally."""
        settings = _make_agent_settings()
        loop = DecisionLoop(
            settings=settings,
            llm_client=AsyncMock(),
            hl_client=AsyncMock(),
            session_factory=AsyncMock(),
        )
        from aiat.execution.guardrails import Guardrails

        assert isinstance(loop._guardrails, Guardrails)

    @pytest.mark.asyncio
    async def test_prompt_template_missing_raises_runtime_error(self) -> None:
        """If prompt_template_hash not in DB, RuntimeError is raised."""
        snap = _make_mock_snapshot()
        session = AsyncMock()
        session.commit = AsyncMock()
        session.get = AsyncMock(return_value=None)  # template not found
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        with (
            patch("aiat.orchestration.decision_loop.SnapshotsRepository") as MockSnapshotsRepo,
            patch("aiat.orchestration.decision_loop.RunsRepository") as MockRunsRepo,
            patch("aiat.orchestration.decision_loop.PositionsRepository"),
        ):
            mock_sr = AsyncMock()
            mock_sr.get_context_snapshot = AsyncMock(return_value=snap)
            MockSnapshotsRepo.return_value = mock_sr

            mock_rr = AsyncMock()
            mock_rr.log_error = AsyncMock()
            MockRunsRepo.return_value = mock_rr

            settings = _make_agent_settings()
            factory = _make_session_factory(session)
            hl_client = AsyncMock()
            hl_client.fetch_portfolio_state = AsyncMock(return_value=_make_portfolio_state())
            llm_client = AsyncMock()

            loop = DecisionLoop(
                settings=settings,
                llm_client=llm_client,
                hl_client=hl_client,
                session_factory=factory,
            )
            with pytest.raises(RuntimeError, match="PromptTemplate"):
                await loop.run_once(TICK_ID, SCHEDULED_FOR)

    @pytest.mark.asyncio
    async def test_check_pending_closures_called(self) -> None:
        """_check_pending_closures must be called even when no positions opened this tick."""
        snap = _make_mock_snapshot()
        session = _setup_session(snap, _make_mock_template())

        with (
            patch("aiat.orchestration.decision_loop.SnapshotsRepository") as MockSnapshotsRepo,
            patch("aiat.orchestration.decision_loop.RunsRepository") as MockRunsRepo,
            patch("aiat.orchestration.decision_loop.DecisionsRepository") as MockDecisionsRepo,
            patch("aiat.orchestration.decision_loop.PositionsRepository") as MockPositionsRepo,
        ):
            mock_sr = AsyncMock()
            mock_sr.get_context_snapshot = AsyncMock(return_value=snap)
            mock_sr.persist_account_snapshot = AsyncMock(return_value=str(uuid.uuid4()))
            MockSnapshotsRepo.return_value = mock_sr

            mock_rr = AsyncMock()
            mock_rr.create_run = AsyncMock(return_value=RUN_ID)
            mock_rr.update_status = AsyncMock()
            MockRunsRepo.return_value = mock_rr

            mock_dr = AsyncMock()
            mock_dr.persist_decision = AsyncMock(return_value=DECISION_ID)
            MockDecisionsRepo.return_value = mock_dr

            mock_pr = AsyncMock()
            mock_pr.list_open_for_model = AsyncMock(return_value=[])
            MockPositionsRepo.return_value = mock_pr

            settings = _make_agent_settings()
            factory = _make_session_factory(session)
            hl_client = AsyncMock()
            hl_client.fetch_portfolio_state = AsyncMock(return_value=_make_portfolio_state())
            hl_client.execute_action = AsyncMock(return_value=[])
            hl_client.check_position_closure = AsyncMock(return_value=None)
            llm_client = AsyncMock()
            inv = _make_invocation_result()
            llm_client.invoke = AsyncMock(return_value=inv)

            reports = [
                GuardrailReport(
                    symbol=a.symbol,
                    original_side=a.side,
                    leverage_clamped=False,
                    size_pct_clamped=False,
                    forced_hold=False,
                    final_action=a,
                )
                for a in inv.decision.actions
            ]
            guardrails = MagicMock()
            guardrails.apply = MagicMock(return_value=(inv.decision, reports))

            loop = DecisionLoop(
                settings=settings,
                llm_client=llm_client,
                hl_client=hl_client,
                session_factory=factory,
                guardrails=guardrails,
            )
            await loop.run_once(TICK_ID, SCHEDULED_FOR)

        # list_open_for_model is called for _execute_actions (step 8) and for
        # _check_pending_closures (step 9)
        assert mock_pr.list_open_for_model.call_count >= 1

    @pytest.mark.asyncio
    async def test_check_pending_closures_detects_closure_by_symbol(self) -> None:
        """TEETH (ADR-0016): an open position whose SYMBOL has a registered closure must
        be detected and closed.

        Regression guard for the M5 bug where _check_pending_closures keyed closure
        detection off a position id (always None) instead of the coin symbol. We use a
        REAL MockHyperliquidClient keyed by the argument passed, so the test is sensitive
        to which value is passed: only resolving by symbol looks up closed_positions["BTC"]
        and closes; anything else would never close and this assertion would fail.
        """
        open_position = SimpleNamespace(id=uuid.uuid4(), symbol="BTC")
        closure = PositionClosureInfo(
            closed_at="2026-06-14T15:00:00+00:00",
            exit_price=Decimal("105"),
            close_reason=CloseReason.STOP_LOSS,
            realized_pnl_usd=Decimal("-50"),
        )
        hl_client = MockHyperliquidClient(closed_positions={"BTC": closure})

        with patch("aiat.orchestration.decision_loop.PositionsRepository") as MockPositionsRepo:
            mock_pr = AsyncMock()
            mock_pr.list_open_for_model = AsyncMock(return_value=[open_position])
            mock_pr.close_position = AsyncMock()
            MockPositionsRepo.return_value = mock_pr

            loop = DecisionLoop(
                settings=_make_agent_settings(),
                llm_client=AsyncMock(),
                hl_client=hl_client,
                session_factory=_make_session_factory(AsyncMock()),
                guardrails=MagicMock(),
            )
            await loop._check_pending_closures(AsyncMock(), RUN_ID)

        mock_pr.close_position.assert_awaited_once()
        call_args = mock_pr.close_position.call_args.args
        assert call_args[0] == str(open_position.id)
        assert call_args[1] is closure
        assert call_args[2] == RUN_ID


# ---------------------------------------------------------------------------
# Tests for _execute_actions execution-status bookkeeping + per-action isolation
# (ADR-0024). These drive the real _execute_actions with the two repositories
# patched, so the mark_action_execution / open_position calls can be asserted.
# ---------------------------------------------------------------------------


def _filled_entry(price: str = "65000", size: str = "0.01") -> OrderResult:
    return OrderResult(
        hl_order_id=str(uuid.uuid4()),
        client_order_id=str(uuid.uuid4()),
        order_kind=OrderKind.ENTRY,
        status="filled",
        requested_price=None,
        filled_price=Decimal(price),
        requested_size_units=Decimal(size),
        filled_size_units=Decimal(size),
        slippage_bps=Decimal("5"),
        fee_usd=Decimal("1.00"),
        raw_response={},
    )


def _order_result(
    kind: OrderKind,
    status: str,
    *,
    filled_price: str | None = "65000",
    size: str = "0.01",
) -> OrderResult:
    """Build an OrderResult of any kind/status for outcome-mapping tests."""
    return OrderResult(
        hl_order_id=str(uuid.uuid4()),
        client_order_id=str(uuid.uuid4()),
        order_kind=kind,
        status=status,  # type: ignore[arg-type]
        requested_price=None,
        filled_price=Decimal(filled_price) if filled_price is not None else None,
        requested_size_units=Decimal(size),
        filled_size_units=Decimal(size) if filled_price is not None else None,
        slippage_bps=None,
        fee_usd=None,
        raw_response={},
    )


def _session_with_db_actions(actions: list[MagicMock]) -> AsyncMock:
    """A session whose select(DecisionAction) returns the given db actions."""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = actions
    session.execute = AsyncMock(return_value=mock_result)
    return session


def _mixed_decision(*actions: ActionDecision) -> TradeDecision:
    return TradeDecision(
        portfolio_reasoning="Mixed actions across the book for the next 15-minute horizon",
        risk_assessment="Risk is moderate; sizing kept conservative under current volatility",
        portfolio_confidence=Decimal("0.6"),
        actions=list(actions),
    )


def _marks_by_action_id(mock_dr: AsyncMock) -> dict[str, dict[str, object]]:
    """Map action_id → kwargs for every mark_action_execution call."""
    out: dict[str, dict[str, object]] = {}
    for call in mock_dr.mark_action_execution.call_args_list:
        out[call.args[0]] = dict(call.kwargs)
    return out


class TestActionExecutionOutcome:
    """Direct unit tests for the _action_execution_outcome status mapping (ADR-0024)."""

    def test_filled_entry_is_filled_executed(self) -> None:
        assert _action_execution_outcome([_filled_entry()]) == (ExecutionStatus.FILLED, True)

    def test_partial_entry_is_partial_executed(self) -> None:
        """A partial entry fill → PARTIAL + executed=True (mapping is live even if dormant)."""
        partial = _order_result(OrderKind.ENTRY, "partial")
        assert _action_execution_outcome([partial]) == (ExecutionStatus.PARTIAL, True)

    def test_close_only_filled_is_filled_executed(self) -> None:
        """A pure FLAT (CLOSE only) that fills → FILLED + executed=True."""
        close = _order_result(OrderKind.CLOSE, "filled")
        assert _action_execution_outcome([close]) == (ExecutionStatus.FILLED, True)

    def test_flip_uses_entry_as_primary(self) -> None:
        """An opposite-side flip emits CLOSE+ENTRY (+SL/TP); the ENTRY is the primary order."""
        orders = [
            _order_result(OrderKind.CLOSE, "filled"),
            _order_result(OrderKind.ENTRY, "filled"),
            _order_result(OrderKind.STOP_LOSS, "triggered", filled_price=None),
            _order_result(OrderKind.TAKE_PROFIT, "triggered", filled_price=None),
        ]
        assert _action_execution_outcome(orders) == (ExecutionStatus.FILLED, True)

    def test_only_triggered_orders_is_not_applicable(self) -> None:
        """No primary entry/close (only protective triggers) → NOT_APPLICABLE, not executed."""
        triggers = [
            _order_result(OrderKind.STOP_LOSS, "triggered", filled_price=None),
            _order_result(OrderKind.TAKE_PROFIT, "triggered", filled_price=None),
        ]
        assert _action_execution_outcome(triggers) == (ExecutionStatus.NOT_APPLICABLE, False)

    def test_empty_orders_is_not_applicable(self) -> None:
        assert _action_execution_outcome([]) == (ExecutionStatus.NOT_APPLICABLE, False)


class TestExecuteActionsStateTransition:
    def _loop_with_hl(self, hl_client: object) -> DecisionLoop:
        return DecisionLoop(
            settings=_make_agent_settings(),
            llm_client=AsyncMock(),
            hl_client=hl_client,  # type: ignore[arg-type]
            session_factory=_make_session_factory(AsyncMock()),
            guardrails=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_filled_long_marks_action_filled_and_executed(self) -> None:
        """A LONG whose entry fills → execution_status=FILLED, executed=True."""
        btc, eth, sol = (
            _make_mock_db_action("BTC"),
            _make_mock_db_action("ETH"),
            _make_mock_db_action("SOL"),
        )
        session = _session_with_db_actions([btc, eth, sol])
        decision = _mixed_decision(
            _make_long_action("BTC"), _make_hold_action("ETH"), _make_hold_action("SOL")
        )

        hl_client = AsyncMock()
        hl_client.execute_action = AsyncMock(return_value=[_filled_entry()])

        with (
            patch("aiat.orchestration.decision_loop.PositionsRepository") as MockPR,
            patch("aiat.orchestration.decision_loop.DecisionsRepository") as MockDR,
        ):
            mock_pr = AsyncMock()
            mock_pr.open_position = AsyncMock(return_value=str(uuid.uuid4()))
            MockPR.return_value = mock_pr
            mock_dr = AsyncMock()
            MockDR.return_value = mock_dr

            loop = self._loop_with_hl(hl_client)
            failed = await loop._execute_actions(session, RUN_ID, decision, _make_portfolio_state())

        assert failed == 0
        mock_pr.open_position.assert_awaited_once()
        marks = _marks_by_action_id(mock_dr)
        assert marks[str(btc.id)] == {"status": ExecutionStatus.FILLED, "executed": True}
        assert marks[str(eth.id)] == {"status": ExecutionStatus.NOT_APPLICABLE, "executed": False}
        assert marks[str(sol.id)] == {"status": ExecutionStatus.NOT_APPLICABLE, "executed": False}

    @pytest.mark.asyncio
    async def test_hold_marks_not_applicable_without_touching_exchange(self) -> None:
        """HOLD → execution_status=NOT_APPLICABLE and execute_action is never called."""
        btc, eth, sol = (
            _make_mock_db_action("BTC"),
            _make_mock_db_action("ETH"),
            _make_mock_db_action("SOL"),
        )
        session = _session_with_db_actions([btc, eth, sol])
        decision = _make_trade_decision()  # all HOLD

        hl_client = AsyncMock()
        hl_client.execute_action = AsyncMock(return_value=[])

        with (
            patch("aiat.orchestration.decision_loop.PositionsRepository") as MockPR,
            patch("aiat.orchestration.decision_loop.DecisionsRepository") as MockDR,
        ):
            MockPR.return_value = AsyncMock()
            mock_dr = AsyncMock()
            MockDR.return_value = mock_dr

            loop = self._loop_with_hl(hl_client)
            failed = await loop._execute_actions(session, RUN_ID, decision, _make_portfolio_state())

        assert failed == 0
        hl_client.execute_action.assert_not_called()
        marks = _marks_by_action_id(mock_dr)
        for action in (btc, eth, sol):
            assert marks[str(action.id)] == {
                "status": ExecutionStatus.NOT_APPLICABLE,
                "executed": False,
            }

    @pytest.mark.asyncio
    async def test_rejected_action_isolated_others_proceed(self) -> None:
        """A rejected action is marked FAILED+error; the other actions still execute."""
        btc, eth, sol = (
            _make_mock_db_action("BTC"),
            _make_mock_db_action("ETH"),
            _make_mock_db_action("SOL"),
        )
        session = _session_with_db_actions([btc, eth, sol])
        decision = _mixed_decision(
            _make_long_action("BTC"), _make_long_action("ETH"), _make_hold_action("SOL")
        )

        async def _exec(
            action: ActionDecision, run_id: str, current_pos: object
        ) -> list[OrderResult]:
            if action.symbol == "BTC":
                raise ExecutionRejectedError("margin too low for BTC")
            return [_filled_entry(price="3500", size="0.1")]

        hl_client = AsyncMock()
        hl_client.execute_action = AsyncMock(side_effect=_exec)

        with (
            patch("aiat.orchestration.decision_loop.PositionsRepository") as MockPR,
            patch("aiat.orchestration.decision_loop.DecisionsRepository") as MockDR,
        ):
            mock_pr = AsyncMock()
            mock_pr.open_position = AsyncMock(return_value=str(uuid.uuid4()))
            MockPR.return_value = mock_pr
            mock_dr = AsyncMock()
            MockDR.return_value = mock_dr

            loop = self._loop_with_hl(hl_client)
            failed = await loop._execute_actions(session, RUN_ID, decision, _make_portfolio_state())

        assert failed == 1
        # ETH still executed despite BTC's rejection
        mock_pr.open_position.assert_awaited_once()
        marks = _marks_by_action_id(mock_dr)
        assert marks[str(btc.id)]["status"] == ExecutionStatus.FAILED
        assert marks[str(btc.id)]["executed"] is False
        assert "margin too low" in str(marks[str(btc.id)]["error"])
        assert marks[str(eth.id)] == {"status": ExecutionStatus.FILLED, "executed": True}
        assert marks[str(sol.id)] == {"status": ExecutionStatus.NOT_APPLICABLE, "executed": False}

    @pytest.mark.asyncio
    async def test_timeout_action_isolated_others_proceed(self) -> None:
        """ExecutionTimeoutError is isolated identically to ExecutionRejectedError."""
        btc, eth, sol = (
            _make_mock_db_action("BTC"),
            _make_mock_db_action("ETH"),
            _make_mock_db_action("SOL"),
        )
        session = _session_with_db_actions([btc, eth, sol])
        decision = _mixed_decision(
            _make_long_action("BTC"), _make_long_action("ETH"), _make_hold_action("SOL")
        )

        async def _exec(
            action: ActionDecision, run_id: str, current_pos: object
        ) -> list[OrderResult]:
            if action.symbol == "BTC":
                raise ExecutionTimeoutError("HL call timed out for BTC")
            return [_filled_entry(price="3500", size="0.1")]

        hl_client = AsyncMock()
        hl_client.execute_action = AsyncMock(side_effect=_exec)

        with (
            patch("aiat.orchestration.decision_loop.PositionsRepository") as MockPR,
            patch("aiat.orchestration.decision_loop.DecisionsRepository") as MockDR,
        ):
            mock_pr = AsyncMock()
            mock_pr.open_position = AsyncMock(return_value=str(uuid.uuid4()))
            MockPR.return_value = mock_pr
            mock_dr = AsyncMock()
            MockDR.return_value = mock_dr

            loop = self._loop_with_hl(hl_client)
            failed = await loop._execute_actions(session, RUN_ID, decision, _make_portfolio_state())

        assert failed == 1
        mock_pr.open_position.assert_awaited_once()  # ETH still executed
        marks = _marks_by_action_id(mock_dr)
        assert marks[str(btc.id)]["status"] == ExecutionStatus.FAILED
        assert marks[str(btc.id)]["executed"] is False
        assert "timed out" in str(marks[str(btc.id)]["error"])
        assert marks[str(eth.id)] == {"status": ExecutionStatus.FILLED, "executed": True}

    @pytest.mark.asyncio
    async def test_run_status_partial_when_action_rejected(self) -> None:
        """End-to-end: one rejected action → run finalized PARTIAL, not SUCCESS (ADR-0024)."""
        snap = _make_mock_snapshot()
        session = _setup_session(snap, _make_mock_template())
        decision = _mixed_decision(
            _make_long_action("BTC"), _make_long_action("ETH"), _make_hold_action("SOL")
        )
        inv = _make_invocation_result(decision)

        with (
            patch("aiat.orchestration.decision_loop.SnapshotsRepository") as MockSnapshotsRepo,
            patch("aiat.orchestration.decision_loop.RunsRepository") as MockRunsRepo,
            patch("aiat.orchestration.decision_loop.DecisionsRepository") as MockDecisionsRepo,
            patch("aiat.orchestration.decision_loop.PositionsRepository") as MockPositionsRepo,
        ):
            mock_sr = AsyncMock()
            mock_sr.get_context_snapshot = AsyncMock(return_value=snap)
            mock_sr.persist_account_snapshot = AsyncMock(return_value=str(uuid.uuid4()))
            MockSnapshotsRepo.return_value = mock_sr

            mock_rr = AsyncMock()
            mock_rr.create_run = AsyncMock(return_value=RUN_ID)
            mock_rr.update_status = AsyncMock()
            MockRunsRepo.return_value = mock_rr

            mock_dr = AsyncMock()
            mock_dr.persist_decision = AsyncMock(return_value=DECISION_ID)
            MockDecisionsRepo.return_value = mock_dr

            mock_pr = AsyncMock()
            mock_pr.list_open_for_model = AsyncMock(return_value=[])
            mock_pr.open_position = AsyncMock(return_value=str(uuid.uuid4()))
            MockPositionsRepo.return_value = mock_pr

            reports = [
                GuardrailReport(
                    symbol=a.symbol,
                    original_side=a.side,
                    leverage_clamped=False,
                    size_pct_clamped=False,
                    forced_hold=False,
                    final_action=a,
                )
                for a in decision.actions
            ]
            guardrails = MagicMock()
            guardrails.apply = MagicMock(return_value=(decision, reports))

            async def _exec(
                action: ActionDecision, run_id: str, current_pos: object
            ) -> list[OrderResult]:
                if action.symbol == "BTC":
                    raise ExecutionRejectedError("margin too low for BTC")
                return [_filled_entry(price="3500", size="0.1")]

            hl_client = AsyncMock()
            hl_client.fetch_portfolio_state = AsyncMock(return_value=_make_portfolio_state())
            hl_client.execute_action = AsyncMock(side_effect=_exec)
            hl_client.check_position_closure = AsyncMock(return_value=None)
            llm_client = AsyncMock()
            llm_client.invoke = AsyncMock(return_value=inv)

            loop = DecisionLoop(
                settings=_make_agent_settings(),
                llm_client=llm_client,
                hl_client=hl_client,
                session_factory=_make_session_factory(session),
                guardrails=guardrails,
            )
            result = await loop.run_once(TICK_ID, SCHEDULED_FOR)

        assert result == RUN_ID
        mock_rr.update_status.assert_called_once_with(RUN_ID, RunStatus.PARTIAL)
