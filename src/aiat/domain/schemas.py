"""Domain schemas — Pydantic v2 strict models (§6.2, §6.3, §6.4)."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aiat.domain.enums import EntryType, Side

# ---------------------------------------------------------------------------
# §6.2 — Controlled signal vocabulary (preliminary; finalized in M3-T06/D4)
# ---------------------------------------------------------------------------

ControlledSignal = Literal[
    "technical.rsi_extreme",
    "technical.macd_cross",
    "technical.ema_alignment",
    "technical.bollinger_squeeze",
    "technical.atr_spike",
    "technical.support_resistance",
    "sentiment.news_polarity",
    "sentiment.fear_greed",
    "sentiment.market_panic",
    "onchain.funding_rate_extreme",
    "onchain.open_interest_shift",
    "onchain.liquidation_cascade",
    "market.volatility_regime",
    "market.volume_anomaly",
    "market.basis_perp_spot",
    "portfolio.exposure_high",
    "portfolio.unrealized_pnl",
    "portfolio.position_aging",
]


# ---------------------------------------------------------------------------
# §6.2 — Decision schemas
# ---------------------------------------------------------------------------


class ActionDecision(BaseModel):
    """Output strutturato del modello per UN simbolo (action-level)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: Literal["BTC", "ETH", "SOL"]
    side: Side
    leverage: Annotated[Decimal, Field(ge=0, le=50, decimal_places=2)]
    size_pct: Annotated[Decimal, Field(ge=0, le=1, decimal_places=4)]
    stop_loss_pct: Annotated[Decimal | None, Field(default=None, gt=0, decimal_places=4)]
    take_profit_pct: Annotated[Decimal | None, Field(default=None, gt=0, decimal_places=4)]
    entry_type: EntryType
    limit_price: Annotated[Decimal | None, Field(default=None, gt=0, decimal_places=8)]

    confidence: Annotated[Decimal, Field(ge=0, le=1, decimal_places=4)] = Field(
        description=(
            "Estimated probability ∈ [0, 1] that this specific action will produce "
            "positive net PnL (after fees and funding) within time_horizon_min. "
            "For HOLD/FLAT, probability that this passive choice is preferable to "
            "the active alternatives at this moment."
        )
    )
    time_horizon_min: Annotated[int, Field(gt=0, le=1440)] = Field(
        description="Time horizon in minutes within which the confidence is calibrated."
    )
    action_reasoning: Annotated[str, Field(min_length=20, max_length=2000)]
    action_key_signals: list[ControlledSignal] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_side_consistency(self) -> "ActionDecision":
        """Vincoli condizionali coerenti con DDL chk_hold_flat_no_sizing
        e chk_open_close_has_sizing."""
        if self.side in (Side.HOLD, Side.FLAT):
            if self.size_pct != 0 or self.leverage != 0:
                raise ValueError("HOLD/FLAT must have size_pct=0 and leverage=0")
            if self.entry_type != EntryType.NONE:
                raise ValueError("HOLD/FLAT must have entry_type='none'")
            if self.stop_loss_pct is not None or self.take_profit_pct is not None:
                raise ValueError("HOLD/FLAT must not declare SL/TP")
            if self.limit_price is not None:
                raise ValueError("HOLD/FLAT must not specify limit_price")  # fix A.1
        else:  # LONG/SHORT
            if self.size_pct <= 0 or self.leverage < 1:
                raise ValueError("LONG/SHORT must have size_pct>0 and leverage>=1")
            if self.entry_type not in (EntryType.MARKET, EntryType.LIMIT):
                raise ValueError("LONG/SHORT must have entry_type='market' or 'limit'")
            if self.stop_loss_pct is None or self.take_profit_pct is None:
                raise ValueError("LONG/SHORT must declare both SL and TP (Figma F1)")
            if self.entry_type == EntryType.LIMIT and self.limit_price is None:
                raise ValueError("entry_type='limit' requires limit_price")
            if self.entry_type == EntryType.MARKET and self.limit_price is not None:
                raise ValueError("entry_type='market' must not specify limit_price")
        return self


class TradeDecision(BaseModel):
    """Output completo del modello per UN tick portfolio-level (RESEARCH §1.0)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    portfolio_reasoning: Annotated[str, Field(min_length=50, max_length=4000)]
    risk_assessment: Annotated[str, Field(min_length=30, max_length=2000)]
    portfolio_confidence: Annotated[
        Decimal | None, Field(default=None, ge=0, le=1, decimal_places=4)
    ]
    actions: Annotated[list[ActionDecision], Field(min_length=3, max_length=3)] = Field(
        description="Exactly 3 actions, one per symbol (BTC, ETH, SOL), in any order."
    )

    @model_validator(mode="after")
    def validate_all_symbols_covered(self) -> "TradeDecision":
        symbols = {a.symbol for a in self.actions}
        if symbols != {"BTC", "ETH", "SOL"}:
            raise ValueError(f"actions must cover exactly BTC/ETH/SOL, got {symbols}")
        return self


# ---------------------------------------------------------------------------
# §6.3 — Context schemas
# ---------------------------------------------------------------------------


class TechnicalIndicators(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: Literal["BTC", "ETH", "SOL"]
    price_usd: Decimal
    rsi_14: Decimal
    macd_signal_diff: Decimal
    ema_20: Decimal
    ema_50: Decimal
    bollinger_upper: Decimal
    bollinger_lower: Decimal
    atr_14: Decimal
    volume_24h_usd: Decimal


class SentimentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fear_greed_index: Annotated[int, Field(ge=0, le=100)]
    fear_greed_label: Literal["extreme_fear", "fear", "neutral", "greed", "extreme_greed"]
    fetched_at: str  # ISO timestamp


class NewsItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(max_length=300)]
    summary: Annotated[str, Field(max_length=600)]
    source: str
    published_at: str
    sentiment_polarity: Annotated[Decimal, Field(ge=-1, le=1)] | None = None


class OnChainSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: Literal["BTC", "ETH", "SOL"]
    funding_rate_8h: Decimal
    open_interest_usd: Decimal
    long_short_ratio: Decimal
    liquidations_24h_usd: Decimal


class OpenPositionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: Literal["BTC", "ETH", "SOL"]
    side: Literal["LONG", "SHORT"]
    entry_price: Decimal
    current_price: Decimal
    size_units: Decimal
    leverage: Decimal
    unrealized_pnl_usd: Decimal
    age_minutes: int


class PortfolioState(BaseModel):
    """Stato model-specific. Diverge cross-model dopo il primo tick (RESEARCH §3.2)."""

    model_config = ConfigDict(extra="forbid")

    equity_usd: Decimal
    available_usd: Decimal
    margin_used_usd: Decimal
    n_open_positions: int
    unrealized_pnl_usd: Decimal
    open_positions: list[OpenPositionSummary]


class ContextBundle(BaseModel):
    """Output del ContextOrchestrator. Market context byte-identico cross-model.

    NOTA: questa struttura rappresenta SOLO il market context (technical, sentiment,
    news, onchain). Il prompt finale somministrato al LLM combina questo bundle con
    il `PortfolioState` model-specific (che diverge cross-model dopo il primo tick
    di trading). Vedi invariante #13 in §5: "market parity vs portfolio independence".
    """

    model_config = ConfigDict(extra="forbid")

    tick_id: str
    tick_at: str
    technical: list[TechnicalIndicators]
    sentiment: SentimentSnapshot
    news: list[NewsItem]
    onchain: list[OnChainSnapshot]
    source_timestamps: dict[str, str]


# ---------------------------------------------------------------------------
# §6.4 — Runtime DTOs
# ---------------------------------------------------------------------------


class CostEventData(BaseModel):
    """DTO restituito da LLMClient.invoke(), persistito DOPO la decisione (invariante #4).

    Aggregato cumulativo se vengono fatti più tentativi LLM (primary + fallback freetext):
    `input_tokens`, `output_tokens`, `reasoning_tokens` e `cost_usd` riflettono il TOTALE
    di tutte le chiamate LLM eseguite per produrre questa decisione (fix B.8 review-r2).
    """

    model_config = ConfigDict(extra="forbid")

    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    reasoning_tokens: Annotated[int, Field(ge=0)] = 0
    cost_usd: Annotated[Decimal, Field(ge=0, decimal_places=8)]
    pricing_snapshot: dict[str, Decimal]
    n_attempts: Annotated[int, Field(ge=1)] = 1


class LLMInvocationResult(BaseModel):
    """Output completo di LLMClient.invoke()."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    decision: TradeDecision
    cost: CostEventData
    latency_ms: Annotated[int, Field(ge=0)]
    raw_response_id: str | None = None
    raw_payload: dict[str, object]
    fallback_used: bool = False
    provider_snapshot: str
    model_name_api_snapshot: str
    temperature: Annotated[Decimal | None, Field(default=None, ge=0)]
    top_p: Annotated[Decimal | None, Field(default=None, gt=0, le=1)]
    max_tokens: Annotated[int | None, Field(default=None, gt=0)]
    seed: int | None = None


class GuardrailReport(BaseModel):
    """Output di Guardrails.apply(). Una row per action."""

    model_config = ConfigDict(extra="forbid")

    symbol: Literal["BTC", "ETH", "SOL"]
    original_side: Side
    leverage_clamped: bool
    size_pct_clamped: bool
    forced_hold: bool
    final_action: ActionDecision
