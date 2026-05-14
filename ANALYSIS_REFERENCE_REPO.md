# Analisi TradingAgents come Reference per V2

## 0. Sintesi esecutiva

TradingAgents è un framework multi-agent stock-on-demand orchestrato con LangGraph: per noi è oro su 4 layer (provider-abstraction LLM, Pydantic schema + fallback strutturato, memory log atomico con outcome deferred, stats callback), ma scope-creep su tutto il resto (graph engine, debate teams, risk mgmt, dataflow yfinance/SEC). Adottiamo la *infrastruttura* (llm_clients, structured.py, memory.py, stats_handler, pyproject + tests + Dockerfile) e SCARTIAMO la *coreografia* (LangGraph, bull/bear, risk debators, portfolio manager). Il loro pattern "4 analyst → state slots tipizzati" si trasferisce perfettamente alle 4 SEZIONI del nostro prompt single-agent. Tutto ciò che riguarda crypto, Hyperliquid, fee/funding, IT-tax, cron 15m, multi-deploy isolato e schema DB scientifico è gap che dobbiamo costruire ex-novo.

## 1. Mappa architetturale di TradingAgents

```
TradingAgents/
├── cli/                              CLI Typer + Rich (questionary), entrypoint pubblico
│   ├── main.py                       comando `tradingagents analyze`
│   ├── models.py / utils.py          model picker, prompt utente
│   └── stats_handler.py              CallbackHandler per LLM/tool/token (RILEVANTE)
├── tradingagents/
│   ├── default_config.py             CONFIG = dict semplice + env override
│   ├── agents/
│   │   ├── analysts/                 4 analyst-factories (1 file ciascuno)
│   │   │   ├── market_analyst.py     technical (indicatori) — più rilevante crypto
│   │   │   ├── news_analyst.py       macro/news
│   │   │   ├── social_media_analyst.py  sentiment
│   │   │   └── fundamentals_analyst.py  10-K, balance sheet (NON ci serve)
│   │   ├── researchers/              bull/bear debate (SCARTA)
│   │   ├── risk_mgmt/                aggressive/conservative/neutral (SCARTA)
│   │   ├── managers/                 research_manager.py + portfolio_manager.py
│   │   ├── trader/trader.py          decisione finale strutturata (RILEVANTE)
│   │   ├── schemas.py                Pydantic ResearchPlan/TraderProposal/PortfolioDecision (RILEVANTE)
│   │   └── utils/
│   │       ├── agent_states.py       TypedDict di LangGraph (ADATTA come state contract)
│   │       ├── agent_utils.py        @tool registrar + helper prompt
│   │       ├── memory.py             TradingMemoryLog (RILEVANTE)
│   │       ├── structured.py         bind_structured + fallback (RILEVANTE)
│   │       └── rating.py             parse 5-tier rating
│   ├── dataflows/                    yfinance + alpha vantage + caches (NON applicabile)
│   ├── graph/
│   │   ├── trading_graph.py          orchestratore (LangGraph)
│   │   ├── setup.py                  costruzione grafo
│   │   ├── propagation.py            init state + run args
│   │   ├── conditional_logic.py      router rounds debate
│   │   ├── checkpointer.py           SqliteSaver per-ticker (RILEVANTE come pattern)
│   │   ├── reflection.py             reflect_on_final_decision (RILEVANTE)
│   │   └── signal_processing.py      estrae BUY/HOLD/SELL da freetext (NON serve, abbiamo Pydantic)
│   └── llm_clients/                  ASTRAZIONE PROVIDER (RILEVANTISSIMO)
│       ├── base_client.py            ABC BaseLLMClient + normalize_content
│       ├── factory.py                create_llm_client(provider, model, ...)
│       ├── openai_client.py          OpenAI + xAI + DeepSeek + Qwen + GLM + OpenRouter + Ollama
│       ├── anthropic_client.py
│       ├── google_client.py
│       ├── azure_client.py
│       ├── model_catalog.py          dizionario provider→{quick:[...],deep:[...]}
│       └── validators.py             warn-only validation modello
├── tests/                            pytest + markers unit/integration/smoke (RILEVANTE)
├── pyproject.toml + uv.lock          uv stack (RILEVANTE)
└── Dockerfile + docker-compose.yml   multi-stage + appuser non-root (RILEVANTE)
```

Dipendenze principali: `langchain-core`, `langchain-{openai,anthropic,google-genai,experimental}`, `langgraph`, `langgraph-checkpoint-sqlite`, `pydantic` (transitivo), `pandas`, `yfinance`, `stockstats`, `redis`, `typer`, `rich`, `questionary`.

## 2. LLM Provider Abstraction

**File**: `tradingagents/llm_clients/{base_client.py, factory.py, openai_client.py, anthropic_client.py, google_client.py, azure_client.py, model_catalog.py, validators.py}`.

**Pattern**:
- `BaseLLMClient(ABC)` con `get_llm()` astratto (`base_client.py:25-62`).
- `create_llm_client(provider, model, base_url, **kwargs)` factory con import lazy (`factory.py:11-53`): provider OpenAI-compatible (`openai`, `xai`, `deepseek`, `qwen`, `glm`, `ollama`, `openrouter`) condividono `OpenAIClient` con `_PROVIDER_CONFIG` dict di base_url + env-var (`openai_client.py:113-120`); Anthropic / Google / Azure hanno client dedicati.
- Ogni client ritorna una sottoclasse di `Chat<Provider>` (es. `NormalizedChatOpenAI`) che override `invoke()` per chiamare `normalize_content(...)` — appiattisce i blocchi tipati `[{type:"reasoning"...}, {type:"text"...}]` di Responses API / Gemini 3 in stringa singola (`base_client.py:6-22`).
- Quirk provider-specifici in subclass dedicate (DeepSeek thinking-mode round-trip, `openai_client.py:52-104`).
- `with_structured_output(schema)` esposto uniformemente; per OpenAI forzato `method="function_calling"` per evitare warning (`openai_client.py:29-32`).

**deep_think_llm vs quick_think_llm**: due istanze separate create con stesso provider ma modelli diversi (`trading_graph.py:86-100`). Non c'è routing intelligente — è il `graph_setup` che decide a mano: deep va a research_manager + portfolio_manager, quick a tutti gli analyst e al trader (`graph/setup.py:78-87`). Configurazione: `config["deep_think_llm"]` / `config["quick_think_llm"]` + `config["llm_provider"]`.

**Retry/backoff/error**: nessun retry manuale; delegato a langchain (`max_retries` passthrough in `_PASSTHROUGH_KWARGS`, `openai_client.py:107-110`). Nessun circuit-breaker; `validate_model()` emette solo `RuntimeWarning` non blocca (`base_client.py:40-52`).

**Token usage / reasoning**: `cli/stats_handler.py` registra un `BaseCallbackHandler` che legge `usage_metadata.input_tokens / output_tokens` su `on_llm_end` (`stats_handler.py:40-56`). Reasoning trace di DeepSeek conservato in `additional_kwargs["reasoning_content"]` (`openai_client.py:80-95`). Nessuna persistenza nativa del reasoning su DB.

**VERDETTO: ADOTTA in toto.** Riscriviamo come `aiat/llm/{base.py, factory.py, providers.py}` con i 4 provider che ci servono (USA-premium = OpenAI/Anthropic, USA-cheap = OpenAI mini o Gemini Flash, CN-premium = DeepSeek-V4-Pro o Qwen-Max, CN-cheap = DeepSeek-chat o GLM). Il pattern `OpenAIClient` che copre 6 provider OpenAI-compatible è esattamente quello che ci serve per non duplicare codice.

**SNIPPET 1 — factory pattern** (`llm_clients/factory.py:5-53`):
```python
_OPENAI_COMPATIBLE = ("openai","xai","deepseek","qwen","glm","ollama","openrouter")

def create_llm_client(provider: str, model: str, base_url=None, **kwargs):
    p = provider.lower()
    if p in _OPENAI_COMPATIBLE:
        from .openai_client import OpenAIClient
        return OpenAIClient(model, base_url, provider=p, **kwargs)
    if p == "anthropic":
        from .anthropic_client import AnthropicClient
        return AnthropicClient(model, base_url, **kwargs)
    if p == "google":
        from .google_client import GoogleClient
        return GoogleClient(model, base_url, **kwargs)
    raise ValueError(f"Unsupported LLM provider: {provider}")
```

**SNIPPET 2 — provider config tabellare** (`llm_clients/openai_client.py:113-120`):
```python
_PROVIDER_CONFIG = {
    "xai":        ("https://api.x.ai/v1",                                  "XAI_API_KEY"),
    "deepseek":   ("https://api.deepseek.com",                             "DEEPSEEK_API_KEY"),
    "qwen":       ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1","DASHSCOPE_API_KEY"),
    "glm":        ("https://api.z.ai/api/paas/v4/",                        "ZHIPU_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1",                         "OPENROUTER_API_KEY"),
    "ollama":     ("http://localhost:11434/v1",                             None),
}
```

## 3. Composizione del prompt e del contesto (Analyst Team)

I 4 analyst sono *node-factories*: `create_<X>_analyst(llm) -> callable(state) -> dict`. Ogni analyst:
1. legge `state["trade_date"]` + `state["company_of_interest"]`
2. costruisce `instrument_context = build_instrument_context(...)` (asset class, market hours, ecc.)
3. definisce un set di tool LangChain (`get_stock_data`, `get_news`, `get_fundamentals`, ...)
4. costruisce un `ChatPromptTemplate` con due messaggi: system collaborativo + `MessagesPlaceholder("messages")`
5. lega tools (`llm.bind_tools(tools)`), invoca, ritorna `{"messages":[result], "<X>_report": result.content}`

**Hand-off**: i 4 report (`market_report`, `sentiment_report`, `news_report`, `fundamentals_report`) finiscono come slot tipizzati di `AgentState` (`agents/utils/agent_states.py:46-74`). Il Trader li riceve via state, NON c'è "merge prompt": il trader legge solo `state["investment_plan"]` (output del Research Manager che già aggregava i 4) e `state["company_of_interest"]` (`agents/trader/trader.py:21-44`).

**Per noi (single-agent)**: NON costruiamo 4 sub-agent, costruiamo 1 prompt con 4 sezioni `## Technical`, `## Sentiment`, `## News`, `## Fundamentals/On-chain`. Ogni sezione viene popolata da una funzione collector deterministica (no LLM), che fa il fetch dati e li formatta. Questa è esattamente la differenza chiave: in TradingAgents l'analyst è un LLM con tool-binding che decide quali tool chiamare; per noi (cron 15m, latenza, costo) i dati sono fetched a monte e iniettati come testo già formattato. L'unico LLM call è quello del decisore.

**VERDETTI per analyst**:
- **Market/Technical** → ADATTA come sezione `## Technical Indicators`. Lo schema "≤8 indicatori complementari + tabella riassuntiva markdown" è ottimo (`market_analyst.py:23-50`); il catalogo (SMA50/200, EMA10, MACD/MACDS/MACDH, RSI, Bollinger, ATR, VWMA) è già crypto-friendly, `stockstats` lo calcola tale-quale su OHLCV BTC/ETH.
- **News** → ADATTA come sezione `## Macro & News`. Sostituire `get_news`/`get_global_news` con CryptoPanic / CoinDesk RSS / generic web RSS.
- **Sentiment** → ADATTA come sezione `## Sentiment`. Sostituire con LunarCrush / Santiment / X-firehose.
- **Fundamentals** → SCARTA (10-K/balance sheet non si applicano). Sostituire con sezione `## On-chain & Funding`: open interest, funding rate, liquidations, basis perp-spot, Hyperliquid order book depth.

**SNIPPET — struttura system del Technical Analyst** (`analysts/market_analyst.py:23-50`, abbreviato):
```python
system_message = (
    "You are a trading assistant tasked with analyzing financial markets. "
    "Your role is to select the **most relevant indicators** for a given market "
    "condition or trading strategy from the following list. The goal is to choose "
    "up to **8 indicators** that provide complementary insights without redundancy.\n"
    "Moving Averages: close_50_sma, close_200_sma, close_10_ema ...\n"
    "MACD Related: macd, macds, macdh ...\n"
    "Momentum: rsi ...\n"
    "Volatility: boll, boll_ub, boll_lb, atr ...\n"
    "Volume: vwma ...\n"
    "Select indicators that provide diverse and complementary information. "
    "Avoid redundancy (e.g., do not select both rsi and stochrsi). "
    "Append a Markdown table at the end of the report to organize key points."
)
```

## 4. Schema decisionale strutturato (output del Trader)

**File chiave**: `tradingagents/agents/schemas.py` + `tradingagents/agents/utils/structured.py`.

**Formato output**: Pydantic `BaseModel` + `Enum` per discriminator + `with_structured_output(schema)` provider-nativo (json_schema OpenAI/xAI, response_schema Gemini, tool-use Anthropic). Render-helper riconverte l'istanza Pydantic in markdown per il logging downstream.

**Campi `TraderProposal`** (`schemas.py:109-138`):
- `action: TraderAction` enum {BUY, HOLD, SELL} (3-tier — il PM ha 5-tier con Overweight/Underweight)
- `reasoning: str` (descrizione: "two to four sentences anchored in analysts' reports")
- `entry_price: Optional[float]`
- `stop_loss: Optional[float]`
- `position_sizing: Optional[str]` (string testuale "5% of portfolio")

**Pattern fallback** (`utils/structured.py:31-73`): `bind_structured(llm, schema, agent_name)` ritorna `None` se `with_structured_output` non supportato (Ollama vecchi, deepseek-reasoner). `invoke_structured_or_freetext` fa retry one-shot in free-text se la chiamata strutturata esplode.

**VERDETTO: ADOTTA il pattern, RIDISEGNA lo schema per crypto perp.**

Schema da adottare per V2 (proposta di partenza, da raffinare in PRD):
```python
class Side(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"  # chiudi e resta out

class TradeDecision(BaseModel):
    side: Side
    confidence: float = Field(ge=0.0, le=1.0)
    leverage: float = Field(ge=1.0, le=10.0, default=1.0)
    size_pct_equity: float = Field(ge=0.0, le=1.0)  # frazione equity allocata
    entry_type: Literal["market", "limit"] = "market"
    limit_price: Optional[float] = None
    stop_loss_pct: Optional[float] = Field(None, ge=0.001, le=0.20)
    take_profit_pct: Optional[float] = Field(None, ge=0.001, le=0.50)
    time_horizon_min: int = Field(ge=15, le=10080)  # 15m..1w
    reasoning: str  # chain narrativa
    key_signals: list[str]  # bullet evidenze attivate
    risk_assessment: str
```

Il pattern `render_to_markdown(decision)` di TradingAgents (`schemas.py:141-163`) lo rinominiamo `to_log_markdown(decision)` e diventa il payload che salviamo in Postgres `decisions.reasoning_md`.

**SNIPPET — schema Pydantic con render** (`schemas.py:109-163`, condensato):
```python
class TraderAction(str, Enum):
    BUY = "Buy"; HOLD = "Hold"; SELL = "Sell"

class TraderProposal(BaseModel):
    action: TraderAction = Field(description="Exactly one of Buy / Hold / Sell.")
    reasoning: str = Field(description="Anchored in analysts' reports. 2-4 sentences.")
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    position_sizing: Optional[str] = None

def render_trader_proposal(p: TraderProposal) -> str:
    parts = [f"**Action**: {p.action.value}", "", f"**Reasoning**: {p.reasoning}"]
    if p.entry_price is not None: parts += ["", f"**Entry Price**: {p.entry_price}"]
    if p.stop_loss is not None: parts += ["", f"**Stop Loss**: {p.stop_loss}"]
    parts += ["", f"FINAL TRANSACTION PROPOSAL: **{p.action.value.upper()}**"]
    return "\n".join(parts)
```

## 5. Configurazione e setup

**File**: `tradingagents/default_config.py` (50 righe, dict puro).

**Pattern**: `DEFAULT_CONFIG` è un dict Python con default override-abili via `os.getenv(...)` per i path filesystem (`TRADINGAGENTS_RESULTS_DIR`, `TRADINGAGENTS_CACHE_DIR`, `TRADINGAGENTS_MEMORY_LOG_PATH`). Niente Pydantic Settings, niente YAML, niente `dotenv` integrato. L'API d'uso è `config = DEFAULT_CONFIG.copy(); config["llm_provider"] = "anthropic"; ta = TradingAgentsGraph(config=config)`.

**Configurabile**: provider, modelli (deep+quick), backend_url override, thinking-level (Google/OpenAI/Anthropic separatamente), debate rounds, recursion limit, lingua output, memory log path/cap, data vendor selection per categoria. **Hardcoded**: i nomi degli analyst, l'ordine del grafo, i prompt system, la struttura state.

**VERDETTO: ADATTA con upgrade a Pydantic Settings.** Per noi serve di più:
- 4 servizi Railway = 4 config diverse, scelta provider+modello da env (`AIAT_LLM_PROVIDER`, `AIAT_LLM_MODEL`, `AIAT_MODEL_ID`, `AIAT_EXPERIMENT_ID`, `AIAT_HYPERLIQUID_PRIVATE_KEY`, `AIAT_DATABASE_URL`)
- Ogni servizio deve auto-identificarsi nel DB con `model_id` che non possiamo lasciare a un dict mutabile
- Pydantic Settings dà validazione type-safe + `model_config = SettingsConfigDict(env_prefix="AIAT_")` + supporto `.env`

Schema di partenza (da PRD):
```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIAT_", env_file=".env")
    llm_provider: Literal["openai","anthropic","google","deepseek","qwen","glm"]
    llm_model: str
    model_id: str       # "us_premium" / "us_cheap" / "cn_premium" / "cn_cheap"
    experiment_id: str  # "exp_v2_run01"
    hyperliquid_private_key: SecretStr
    hyperliquid_testnet: bool = True
    database_url: SecretStr
    cron_interval_min: int = 15
    symbol: str = "BTC"
    max_leverage: float = 5.0
```

Dei loro env-override sui path conserviamo l'idea ma applichiamoli a logging dir e cache locale (Railway ha disco effimero, comunque tutto va su DB).

## 6. Stack tecnico

**`pyproject.toml`** (`pyproject.toml:1-54`): setuptools build, `requires-python>=3.10`, deps elencate piatte (no extras). `[project.scripts] tradingagents = "cli.main:app"`. `[tool.pytest.ini_options]` con `markers = [unit, integration, smoke]` e `filterwarnings = ["ignore::DeprecationWarning"]`.

**Stack runtime**: LangChain Core + langchain-{openai, anthropic, google-genai, experimental}, **LangGraph + langgraph-checkpoint-sqlite** (graph engine + checkpointer), pandas, **stockstats** (indicatori tecnici da OHLCV — utile per noi), yfinance (da rimpiazzare), redis (cache opzionale), typer + rich + questionary (CLI).

**`uv.lock`** presente: usano `uv` come resolver/lockfile. `requirements.txt` quasi vuoto (legacy). Buona pratica: lockfile committed.

**Tests**: pytest puro, ~1600 LoC totali. Marker discipline: `@pytest.mark.unit` quasi ovunque, `integration` per quelli che richiedono service reali. `tests/conftest.py` ha fixture autouse `_dummy_api_keys` che injecta env-var placeholder per evitare hang in CI senza chiavi (`conftest.py:14-31`) + `mock_llm_client` con `MagicMock` (riga 34-42). **Pattern molto pulito da copiare.**

**Docker**: `Dockerfile` multi-stage (builder Python 3.12-slim → runtime slim), `pip install --no-cache-dir .`, `useradd appuser` non-root, ENTRYPOINT direttamente al CLI script (`Dockerfile:1-27`). `docker-compose.yml` con volume nominato `tradingagents_data` per `/home/appuser/.tradingagents` + profile `ollama` per il caso self-host.

**VERDETTO: ADOTTA in larga parte:**
- ✅ `pyproject.toml` con `uv.lock` (deterministic build su Railway)
- ✅ pytest + markers `unit/integration/smoke` + fixture `_dummy_api_keys` (la copiamo identica nel nostro `conftest.py`)
- ✅ Dockerfile multi-stage + non-root user (esattamente quello che ci serve per Railway)
- ✅ `stockstats` per indicatori tecnici (BTC/ETH OHLCV → SMA/EMA/MACD/RSI/BB/ATR senza scrivere niente)
- ❌ LangChain Core / LangGraph: vedi §7 — qui scartiamo il graph engine ma manteniamo `langchain-core` per `with_structured_output` di langchain-openai/anthropic/google. Discussione aperta nel PRD: alternativa "SDK nativi + instructor / pydantic-ai" è più snella per single-agent.

## 7. Cose da NON adottare (anti-pattern per il NOSTRO scope)

- **LangGraph come graph engine** — è ottimo per multi-node con conditional routing e debate loops, ma il nostro flusso è lineare: `collect_data → format_prompt → llm.invoke → validate(Pydantic) → execute → log_db`. Aggiungerlo introduce concetti (`StateGraph`, `MessagesState`, `ToolNode`, `conditional_edges`) e dipendenze pesanti senza alcun beneficio. Manteniamo `langchain-core` solo per il binding strutturato; il "grafo" è una funzione Python di ~80 righe.
- **Multi-agent collaboration / debate rounds** (`agents/researchers/{bull,bear}_researcher.py`, `conditional_logic.max_debate_rounds`) — il nostro design è esplicitamente single-agent comparativo: aggiungere debate interno significherebbe (a) raddoppiare/triplicare i token per call e (b) confondere il segnale dell'esperimento (è il modello a parlare, non un meta-modello). Le 4 prospettive le diamo come 4 sezioni del prompt, non come round dialogici.
- **Researcher Team / Risk Manager / Portfolio Manager separati** (`agents/managers/*.py`, `agents/risk_mgmt/*.py`) — sarebbe scope creep totale. Il single-agent fa già tutto: lettura contesto → reasoning → decisione strutturata. Il "risk assessment" è un campo dello schema, non un agente separato.
- **Stock-specific data sources** (`dataflows/y_finance.py`, `dataflows/alpha_vantage_*.py`) — siamo crypto perpetuals: ci servono Hyperliquid SDK (price + funding + OI), CryptoPanic/CoinDesk RSS, eventualmente Glassnode/Santiment per on-chain. SCARTIAMO `yfinance`, `alpha_vantage_*`, tutto `dataflows/`.
- **Fundamentals analyst (10-K/balance-sheet)** (`agents/analysts/fundamentals_analyst.py`, `agents/utils/fundamental_data_tools.py`) — concetto inapplicabile a BTC/ETH. La sezione equivalente nel nostro prompt diventa "On-chain & Funding".
- **`signal_processing.py`** (estrazione `BUY/HOLD/SELL` da freetext con un LLM extra) — non ci serve, abbiamo Pydantic structured output che ci dà l'enum garantito. -1 LLM call per decisione.
- **`alpha vs SPY` returns** (`graph/trading_graph.py:191-227`) — il loro PnL è alpha vs SPY benchmark. Per crypto perp benchmark naturale è **buy-and-hold spot** dello stesso symbol, e separatamente **PnL netto post-fee/funding/tax**. Riscriviamo l'outcome resolver.

## 8. Cose che MANCANO (TradingAgents non ce le ha)

- **Cron 15m continuo + state-machine di posizione** — TradingAgents è on-demand: l'utente lancia `tradingagents analyze NVDA 2026-01-15` una volta. Non ha alcun loop, scheduler, o concetto di "ho già una posizione aperta". Per V2 dobbiamo costruire: trigger cron Railway (o `run_in_background` interno), check posizione corrente (Hyperliquid SDK), decisione condizionata ("ho già long → tengo / chiudo / inverto"), idempotency lock per evitare doppia esecuzione.
- **Hyperliquid SDK + execution layer** — TradingAgents non esegue trade reali, finisce con un markdown "FINAL TRANSACTION PROPOSAL: **BUY**". Manca completamente: order placement (market/limit), gestione slippage, query `userState`, fill confirmation, retry su tx failure, gestione liquidation risk.
- **Fee tracking real-time** — non c'è nulla di trasparente sui costi. Per V2: tracking per ogni fill di taker/maker fee, funding pagato/ricevuto su posizioni aperte (8h cycle Hyperliquid), aggregazione in `trade_costs` table.
- **Simulazione tasse italiane** — quadro RW + 26% sui capital gain crypto, gestione minusvalenze. Zero in TradingAgents (US-centric).
- **Multi-deploy isolato con DB scientifico condiviso** — TradingAgents è single-deploy. Per V2: 4 servizi Railway su wallet Hyperliquid distinti, ognuno con `model_id` proprio, tutti che scrivono allo stesso Postgres su tabelle indicizzate per `(experiment_id, model_id, run_id)`. Garanzia di isolamento: nessuna lettura cross-model durante run (per non contaminare il segnale "comportamento emergente").
- **Schema DB Postgres con `experiment_id / model_id / run_id`** — TradingAgents salva un markdown locale (`~/.tradingagents/memory/trading_memory.md`) e JSON dump di state. Per V2 servirà schema relazionale: `experiments`, `runs`, `decisions`, `positions`, `fills`, `costs`, `outcomes`, con FK e indici per query analitiche cross-model.
- **Logging completo di reasoning trace + cost per decisione** — VERIFICATO: TradingAgents ha `StatsCallbackHandler` che traccia `llm_calls / tool_calls / tokens_in / tokens_out` (`cli/stats_handler.py:68-76`), MA: (a) è solo in-memory per la durata del run CLI, non persistito, (b) non calcola costo $, (c) non collega le statistiche alla decisione finale (è un contatore globale). Per V2: persistenza per-decisione di `tokens_in`, `tokens_out`, `tokens_reasoning` (se disponibile via `additional_kwargs["reasoning_content"]`), `cost_usd` (calcolato da pricing table per modello), `latency_ms`, `reasoning_text` full.
- **Coerenza multi-run dello stesso modello** — TradingAgents valuta ogni decisione in isolamento. Per la nostra RQ "firme comportamentali" servirà metriche aggregate: variance della direction, distribuzione confidence, distribuzione `reasoning_length`, ecc. — funzioni di analisi che non esistono nel repo reference.
- **Prompt versioning / immutabilità del contesto** — il prompt va versionato (`prompt_version_hash`) e identico tra i 4 modelli. TradingAgents non ha questa preoccupazione perché i prompt sono hardcoded in stringa e cambiano con i commit. Per noi va estratto in file `prompts/v2_decision.md` con hash committato a ogni run.
- **Dashboard / esposizione metriche live** — non esiste in TradingAgents (è un CLI). Per V2 minimal: endpoint health-check su Railway + dashboard read-only su Postgres (Grafana o Streamlit).

## 9. Top 5 takeaway operativi

1. **Adotta in toto il pattern `llm_clients/`** (factory + BaseLLMClient + provider subclass con `normalize_content`). Da `tradingagents/llm_clients/{factory.py, base_client.py, openai_client.py}`. Risolve in 200 righe il problema "4 provider eterogenei con stesso prompt": OpenAI/Anthropic/Google/DeepSeek funzionano subito, e copri 6 provider OpenAI-compatible con un solo client. Senza questo, ogni servizio Railway diventerebbe un branch del codice — e l'esperimento perderebbe rigore.

2. **Adotta il pattern Pydantic schema + `with_structured_output` + fallback freetext** da `tradingagents/agents/utils/structured.py:31-73` e `tradingagents/agents/schemas.py:109-163`. Il `bind_structured` graceful-fallback è critico: deepseek-reasoner non supporta tool_choice, alcuni Qwen/GLM sono erratici sul JSON schema. Senza fallback, un fail strutturato fa cadere l'intera decisione → buco nei dati dell'esperimento.

3. **Adotta il `TradingMemoryLog` come pattern, non come implementazione**, da `tradingagents/agents/utils/memory.py`. Il design "store_decision pending → resolve outcome on next run con tmp+rename atomic" e la separazione "n same-ticker recenti + n cross-ticker lessons" iniettati nel prompt sono direttamente trasferibili a Postgres. Riscriviamolo come `MemoryRepo` con due query: `insert_pending(decision)` e `resolve_outcomes(model_id, before_ts)` schedulato. La logica di context injection (5 same + 3 cross) è un punto di partenza ragionevole.

4. **Adotta `StatsCallbackHandler`** da `cli/stats_handler.py:9-76` esteso con `cost_usd` (lookup pricing per modello) e legato alla `run_id`. Snapshot dei contatori prima/dopo la `llm.invoke()` → record `decisions.tokens_in/out/cost_usd/latency_ms`. Senza questo, la nostra RQ "fattibilità costi" è non misurabile.

5. **Adotta lo scaffold di test + Docker**: `pyproject.toml` con markers `unit/integration/smoke`, `tests/conftest.py:14-42` con `_dummy_api_keys` autouse + `mock_llm_client` (evita hang CI senza chiavi reali), `Dockerfile` multi-stage non-root. Sono ~50 righe di config che ci risparmiano una settimana di setup CI/CD su Railway e GitHub Actions.

## 10. Aggiornamenti suggeriti al PRE_PRD esistente

Senza accesso al testo completo del PRE_PRD, ipotizzando le 6 decisioni strategiche standard (single-agent, crypto perpetuals/Hyperliquid, cron 15m, 4 modelli paralleli isolati, Postgres scientifico, 4 settimane), ecco i punti che meritano review:

1. **Decisione "stack LLM"** — esplicitare l'adozione del pattern `llm_clients/` e il fatto che `langchain-core` resta come dipendenza *solo* per `with_structured_output` (non come framework). In alternativa, valutare `instructor` o `pydantic-ai` per evitare langchain del tutto: TradingAgents ha langchain perché ha il graph; noi non abbiamo il graph. Decisione da prendere ora, perché impatta tutto il modulo `llm/`.

2. **Decisione "schema decisione"** — aggiungere alla decisione strategica l'obbligo di **structured output Pydantic + fallback freetext**, non lasciarlo a "best practice". È un requisito di robustezza esperimentale (ogni decisione mancata = buco nel dataset).

3. **Decisione "context window strategy"** — TradingAgents inietta nel prompt del Portfolio Manager: 5 decisioni stesso ticker + 3 cross-ticker lessons + reflection esiti passati. Per noi single-symbol single-model questo si semplifica MA introduce un dilemma scientifico: **quanto contesto storico ricevi tra le run?** Zero contesto = ogni run è indipendente (più pulita per misurare baseline), N decisioni passate = misuri anche capacità di apprendimento dal proprio stato. Da decidere e formalizzare nel PRD: probabilmente partire con N=0 nelle prime 2 settimane e introdurre N=K nelle ultime 2 (o tenere fisso e non confondere il segnale).

4. **Decisione "sezioni del prompt"** — formalizzare le 4 sezioni come **`## Technical`, `## Sentiment`, `## News & Macro`, `## On-chain & Funding`** (sostituendo Fundamentals). Ogni sezione ha un *collector deterministico* a monte (no LLM, dati formattati). Documentare nel PRD le fonti per ciascuna sezione (Hyperliquid SDK per technical+funding, CryptoPanic/CoinDesk per news, LunarCrush per sentiment, Glassnode/Santiment per on-chain). Aggiunge un requisito di "data source tier" per tenere costi sotto controllo.

5. **Decisione "outcome benchmark"** — sostituire alpha-vs-SPY (loro) con benchmark **buy-and-hold spot symbol** + reporting separato di **PnL lordo / PnL netto-fee / PnL netto-fee-funding / PnL netto-fee-funding-tax**. Le 4 metriche vanno tutte in `outcomes` table. Senza questa quadrupletta, la RQ "fattibilità post-tax" è ambigua.

6. **Aggiunta non-ancora-decisione: prompt versioning** — proporre nel PRD una sezione "Prompt Governance": un `prompts/v2_decision.md` versionato, hash SHA256 calcolato a runtime e scritto in `runs.prompt_version`. Garantisce riproducibilità + permette di mostrare in tesi "esperimento in 2 fasi: prompt v1 settimane 1-2, prompt v2 settimane 3-4" se decidiamo di iterare.

7. **Aggiunta non-ancora-decisione: pricing/cost ledger** — formalizzare nel PRD un `model_pricing.yaml` (input $/1M tok + output $/1M tok per ciascuno dei 4 modelli, aggiornato manualmente) usato dallo `StatsCallbackHandler` per scrivere `decisions.cost_usd`. Evita di calcolare ex-post da log esterni (errori di provenance).

8. **Decisione "data isolation tra modelli"** — formalizzare che i 4 servizi Railway scrivono **append-only** sulle stesse tabelle, MA le query di context injection (se decidiamo di averle, vedi punto 3) filtrano sempre `WHERE model_id = $1`. Test di non-contaminazione da inserire nella suite (`tests/test_isolation.py`): un servizio non deve vedere decisioni di un altro modello. È esattamente lo specchio inverso del pattern memory log di TradingAgents (loro condividono per ticker, noi isoliamo per modello).
