# PRD V2 — AI Trading Agent (Thesis Edition)

> Documento tecnico-implementativo per l'agente V2. Traduce le 18 decisioni del `PRE_PRD.md` (commit `73e3d02`) e la cornice scientifica del `RESEARCH_DESIGN.md` (commit `2e1df14`) in specifiche eseguibili: architettura moduli, schema DB completo (DDL), contratti API, flussi dati, strategia di deploy, test plan, milestones, risk register.
>
> **Stato**: ROUND 1 v3/3 — questa versione **post DUE cicli di peer-review esterna** copre §1-§5 (introduzione + stack + architettura + schema DB + flussi dati). Cicli di integrazione: v1→v2 integrava 18 fix (8 HIGH + 7 MEDIUM + 3 LOW) sulla prima review; v2→v3 integra ulteriori 8 fix (4 dei 5 parziali della prima review portati a completo + 3 HIGH + 2 MEDIUM emersi nella seconda review). Round 2 aggiungerà contratti API + LLM provider abstraction + test plan. Round 3 aggiungerà deploy strategy + milestones + risk register + propagation map.
>
> Branch: `prd/v2-design`. Da committare separatamente per ogni round per tracciabilità.

---

## 0. Riferimenti normativi

Questo PRD eredita vincolanti da:

| Documento | Commit | Ruolo nel PRD |
|-----------|--------|---------------|
| `PRE_PRD.md` | `73e3d02` | 18 decisioni strategiche (modelli, deploy, guardrail, schema, ...) |
| `RESEARCH_DESIGN.md` | `2e1df14` | Cornice scientifica (3 RQ, ipotesi, variabili, baseline) |
| `AUDIT_V1.md` | `1e9c8f2` (branch `audit/v1-review`) | Componenti V1 REUSE/REFACTOR/REPLACE/REMOVE |
| `ANALYSIS_REFERENCE_REPO.md` | `df2d87b` | Pattern adottati da TradingAgents |
| `ANALYSIS_FIGMA.md` | `42dbb3d` | F1/F2/F3 requisiti operativi |

In caso di conflitto: PRD V2 specifica concretamente; PRE_PRD e RESEARCH_DESIGN restano i vincoli alti.

---

## 1. Stack tecnologico (consolidato)

### 1.1 Linguaggio e package manager

- **Python**: 3.12+ (`uv` non supporta < 3.10; 3.12 è il default LTS-like per data science)
- **Package manager**: `uv` (manifest: `pyproject.toml`, lockfile: `uv.lock`, entrambi committed)
- **Virtual env**: gestita da `uv` in `.venv/` (gitignored)

### 1.2 Stack runtime

| Categoria | Scelta | Razionale |
|-----------|--------|-----------|
| LLM abstraction | `langchain-core` + `langchain-openai` + `langchain-anthropic` (minimal) | Vedi PRE_PRD §11.1 |
| Data validation | `pydantic` 2.x | Schema decisionali type-safe, integrazione con `langchain` |
| Settings | `pydantic-settings` con `env_prefix="AIAT_"` | Type-safe, validazione startup, env-driven |
| DB ORM | `sqlalchemy` 2.x (async, `Mapped`/`DeclarativeBase`) | Type safety, async I/O, ecosistema maturo |
| DB driver | `asyncpg` (Postgres async) | Performance, integrazione SQLAlchemy 2.x async |
| Migrations | `alembic` | Versioning schema, rollback, autogenerate dal modello SQLAlchemy |
| Scheduler | `apscheduler` (versione 3.x stabile) | In-process, deterministico, retry built-in |
| Exchange SDK | `hyperliquid-python-sdk` (vedi V1) | Già usato, funzionante |
| HTTP client | `httpx` (async) | Per fetch RSS, sentiment, on-chain |
| Logging | `structlog` con formatter JSON | Logging strutturato, log shipping cloud-friendly |
| Test | `pytest` + `pytest-asyncio` + `pytest-cov` | Standard moderno |
| Linter / formatter | `ruff` (linter + formatter) | Velocissimo, sostituisce flake8 + black + isort |
| Type checker | `mypy` (strict mode su moduli core) | Type safety statica |
| Container | `Docker` multi-stage, non-root user | Pattern da TradingAgents (vedi ANALYSIS §6) |

### 1.3 Dipendenze numeriche/tecniche

- `numpy`, `pandas` per analisi posteriori (no per cron-loop, troppo overhead)
- `pandas-ta` per indicatori tecnici (preferito a `ta-lib` perché pure-Python, no compilazione C richiesta)
- `python-decimal` (stdlib) per sizing posizioni con precisione finanziaria
- `tenacity` per retry/backoff strutturato delle chiamate API

### 1.4 Cosa NON usiamo (decisioni esplicite)

| Componente | Motivo dell'esclusione |
|-----------|------------------------|
| LangGraph | Scope multi-agent, non ci serve (vedi ANALYSIS §7) |
| LangChain (high-level) | Bloat non giustificato (vedi PRE_PRD §11.1) |
| FastAPI / web framework | L'agent non espone API; la dashboard lo farà, ma vive in altra repo |
| Celery / Redis | Overhead non giustificato; APScheduler in-process basta |
| Kafka / message broker | Overhead non giustificato; il DB Postgres condiviso è il "bus" implicito |
| SQLModel | Maturità inferiore a SQLAlchemy 2.x per il nostro caso |
| Whale alert (V1) | Dead code, rimosso (vedi AUDIT V1 §7) |

---

## 2. Architettura ad alto livello

### 2.1 Vista deployment

```
                                  ┌─────────────────────┐
                                  │  Postgres (Railway) │
                                  │   schema condiviso  │
                                  └──────────▲──────────┘
                                             │
                       ┌─────────────────────┼─────────────────────┐
                       │                     │                     │
              ┌────────┴─────────┐           │           ┌─────────┴────────┐
              │ context-         │           │           │   Hyperliquid    │
              │ orchestrator     │           │           │   testnet        │
              │ (Railway,        │           │           │   4 wallets      │
              │  scheduled at    │           │           │                  │
              │  HH:00/15/30/45) │           │           └──────────────────┘
              │                  │           │
              │ writes ONE       │           │
              │ context_snapshot │           │
              │ per tick         │           │
              └──────────────────┘           │
                                             │
              ┌─────────────────┬────────────┼────────────┬─────────────────┐
              │                 │            │            │                 │
       ┌──────┴───────┐  ┌──────┴───────┐ ┌──┴────────┐ ┌─┴───────────┐
       │ agent-openai │  │ agent-       │ │ agent-    │ │ agent-      │
       │ (Railway     │  │ anthropic    │ │ deepseek  │ │ qwen        │
       │  service)    │  │              │ │           │ │             │
       │              │  │              │ │           │ │             │
       │ wallet_1     │  │ wallet_2     │ │ wallet_3  │ │ wallet_4    │
       │              │  │              │ │           │ │             │
       │ reads tick   │  │ reads tick   │ │ reads tick│ │ reads tick  │
       │ context from │  │ context from │ │ context   │ │ context     │
       │ DB           │  │ DB           │ │ from DB   │ │ from DB     │
       └─────┬────────┘  └─────┬────────┘ └─────┬─────┘ └─────┬───────┘
             │                 │                │             │
             └────────┬────────┴───────┬────────┴──────┬──────┘
                      ▼                ▼               ▼
              ┌───────────────┐ ┌────────────┐ ┌─────────────┐
              │ Hyperliquid   │ │  RSS news  │ │ CoinMarket  │
              │ testnet       │ │  CryptoP.  │ │  Cap, F&G   │
              │ (per execution)│ │            │ │             │
              └───────────────┘ └────────────┘ └─────────────┘
                                    ▲                ▲
                                    │                │
                            ┌───────┴────────────────┴────────┐
                            │ context-orchestrator fetches    │
                            │ external sources here, NOT the  │
                            │ agents. Agents read context     │
                            │ from DB, no external duplicate. │
                            └─────────────────────────────────┘
```

**Punti chiave**:

- **5 servizi Railway totali**, non 4. Il 5° è il **Context Orchestrator** (`context-orchestrator`):
  - Schedulato sui 15 minuti esatti (HH:00, HH:15, HH:30, HH:45 UTC)
  - Unico servizio che fetcha le sorgenti esterne (RSS, sentiment, prezzi HL)
  - Materializza **un solo `ContextBundle` per tick** e lo persiste come `context_snapshot` row in DB
  - I 4 agent partono ~30s dopo (HH:00:30, HH:15:30, ...) e leggono il context per il `tick_id` corrente dal DB
- **Garantisce parità cross-model del market context**: tutti i 4 agent ricevono **byte-identico** lo stesso *market context* al medesimo tick (stesso `context_hash`, stessa `context_snapshot.id`). Il *prompt finale* somministrato a ciascun modello, però, include anche il `portfolio_state` model-specific (che diverge dopo il primo tick di trading, perché i 4 wallet evolvono indipendentemente). Quindi: **market context byte-identico cross-model + portfolio state isolato per modello**. Il `portfolio_state_hash` viene loggato in `account_snapshots` per audit.
- **Decoupling**: se il context-orchestrator fallisce, i 4 agent leggono il *fallback* del tick precedente o saltano il tick (vedi §4.1 e §5)
- 1 Postgres condiviso, schema unico, scrittura distinta per `model_id`
- Hyperliquid testnet: 4 wallet distinti, 1000$ ciascuno (solo gli agent toccano HL per esecuzione; il context-orchestrator legge prezzi via HL info endpoint, non opera trade)

**Razionale della scelta (Context Orchestrator vs advisory lock)**:
- *Advisory lock pattern*: il primo agent che parte acquisisce un lock Postgres, costruisce il contesto, lo persiste, rilascia; gli altri 3 leggono. **Funziona ma è fragile**: ordine di arrivo non garantito, retry complicati, debugging difficile.
- *Context Orchestrator separato*: un solo servizio dedicato, ruolo chiaro, fallimento isolato dai modelli, auditabilità totale. **Costo aggiuntivo trascurabile** (servizio leggerissimo, ~$2/mese Railway Hobby) e **valore scientifico altissimo** (parità input garantita per definizione).

Scelta: **Context Orchestrator separato**. Documentata in §5 invariante #13.

### 2.2 Vista repository (struttura cartelle)

```
ai-agent-for-trading/
├── .github/
│   └── workflows/
│       └── ci.yml                    # tests + lint su PR
├── alembic/
│   ├── versions/                     # migration history
│   ├── env.py
│   └── script.py.mako
├── alembic.ini
├── docker/
│   └── Dockerfile                    # multi-stage, non-root
├── docs/
│   ├── PRE_PRD.md
│   ├── RESEARCH_DESIGN.md
│   ├── PRD_V2.md                     # questo documento
│   ├── ANALYSIS_REFERENCE_REPO.md
│   ├── ANALYSIS_FIGMA.md
│   └── AUDIT_V1.md                   # copia per riferimento (originale su branch separato)
├── src/
│   └── aiat/                         # AI Agent for Trading (root package)
│       ├── __init__.py
│       ├── __main__.py               # entrypoint: python -m aiat
│       ├── config/
│       │   ├── __init__.py
│       │   ├── settings.py           # Pydantic Settings, env_prefix=AIAT_
│       │   └── model_pricing.yaml    # cost ledger pricing per modello
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── enums.py              # Side, Tier, GuardrailKind, ...
│       │   ├── schemas.py            # Pydantic: TradeDecision, OpenAction, ...
│       │   └── exceptions.py
│       ├── db/
│       │   ├── __init__.py
│       │   ├── session.py            # async engine, SessionLocal
│       │   ├── models/               # SQLAlchemy declarative models
│       │   │   ├── __init__.py
│       │   │   ├── base.py
│       │   │   ├── experiment.py
│       │   │   ├── model.py
│       │   │   ├── run.py
│       │   │   ├── decision.py
│       │   │   ├── action.py
│       │   │   ├── outcome.py
│       │   │   ├── account_snapshot.py
│       │   │   ├── position.py
│       │   │   ├── order.py                       # NEW: orders/fills (orders table)
│       │   │   ├── fee_event.py
│       │   │   ├── funding_event.py
│       │   │   ├── cost_event.py
│       │   │   ├── llm_invocation.py              # NEW: nuisance variables snapshot
│       │   │   ├── tax_sim.py                     # ora tax_sim_periods (aggregati)
│       │   │   ├── error.py
│       │   │   ├── prompt_template.py             # MODIFIED: template statico
│       │   │   ├── context_snapshot.py            # NEW: contesto materializzato per tick
│       │   │   ├── context_build_run.py           # NEW: audit log tentativi orchestrator (fix B.2 review-v2)
│       │   │   ├── baseline_config.py             # NEW: pre-registrazione parametri baseline (fix B.5 review-v2)
│       │   │   └── baseline_equity_snapshot.py    # NEW: equity baseline a posteriori
│       │   └── repositories/
│       │       ├── __init__.py
│       │       ├── decisions.py
│       │       ├── positions.py
│       │       ├── snapshots.py
│       │       └── ledger.py
│       ├── context/
│       │   ├── __init__.py
│       │   ├── builder.py            # ContextBuilder principale
│       │   ├── collectors/
│       │   │   ├── __init__.py
│       │   │   ├── technical.py     # porting da V1 indicators.py
│       │   │   ├── sentiment.py     # Fear&Greed
│       │   │   ├── news.py          # RSS CryptoPanic, CoinDesk
│       │   │   └── onchain.py       # funding, OI, liquidations
│       │   ├── controlled_signals.py # vocabolario controllato (RESEARCH §3.3)
│       │   └── memory.py             # MemoryRepo (infrastruttura, off di default)
│       ├── prompts/
│       │   ├── __init__.py
│       │   ├── templates/
│       │   │   └── v1_baseline.md
│       │   ├── renderer.py           # rendering template → finale + SHA256
│       │   └── confidence_def.md     # definizione confidence vincolata (RESEARCH §2.1)
│       ├── llm/
│       │   ├── __init__.py
│       │   ├── base.py               # BaseLLMClient (ABC)
│       │   ├── factory.py            # load_llm() dispatcher
│       │   ├── openai_client.py
│       │   ├── anthropic_client.py
│       │   ├── openai_compatible_client.py  # condiviso da deepseek/qwen (custom base_url)
│       │   ├── structured.py         # with_structured_output + freetext fallback
│       │   └── stats_handler.py      # cost ledger callback (PRE_PRD §11.7)
│       ├── execution/
│       │   ├── __init__.py
│       │   ├── hyperliquid_client.py # refactor da V1 hyperliquid_trader.py
│       │   ├── guardrails.py         # 4 guardrail Strategia C+ (PRE_PRD §13.3)
│       │   ├── sizing.py             # Decimal sizing posizioni
│       │   └── outcome_resolver.py
│       ├── orchestration/
│       │   ├── __init__.py
│       │   ├── scheduler.py          # APScheduler setup, 15m cron
│       │   ├── decision_loop.py      # 1 tick: collect → prompt → LLM → execute → log
│       │   ├── context_orchestrator.py  # NEW: entrypoint del 5° servizio Railway
│       │   └── lifecycle.py
│       └── observability/
│           ├── __init__.py
│           ├── logging_config.py
│           └── metrics.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   │   ├── test_db_repositories.py
│   │   └── test_llm_providers.py     # con vcr/cassette
│   └── e2e/
│       ├── test_decision_loop_smoke.py
│       └── test_isolation.py         # cross-model isolation (PRE_PRD §11.8)
├── scripts/
│   ├── seed_experiment.py            # crea experiment + 4 model rows in DB
│   ├── verify_wallets.py             # check 4 wallet HL raggiungibili
│   ├── export_dataset.py             # export CSV/Parquet finale
│   └── compute_baselines.py          # B&H, Cash, naive momentum a posteriori
├── .env.example
├── .gitignore
├── pyproject.toml
├── uv.lock
└── README.md
```

**Note di convenzione**:
- Layout `src/aiat/` (non flat) — best practice moderna per evitare ambiguità di import
- Tutti i sotto-package (`domain/`, `db/`, `context/`, `llm/`, `execution/`, `orchestration/`, `observability/`) **dipendono solo da `domain/` e da interfacce esplicite tra di loro**: `orchestration/` compone i moduli runtime; sono vietate dipendenze cicliche e chiamate laterali tra moduli runtime (vedi §5 invariante #14). Verificato in CI da `import-linter`.
- Gli script in `scripts/` sono utilità una-tantum, non parte del runtime di produzione

### 2.3 Componenti chiave (panoramica)

| Componente | Responsabilità | Input | Output |
|-----------|----------------|-------|--------|
| `Scheduler` (APScheduler) | Trigger ogni 15 min, gestione retry, lifecycle. Job separati su context-orchestrator vs agent | timer | invocazione di `decision_loop.run_once()` o `context_orchestrator.build_tick_context()` |
| `ContextOrchestrator` (5° servizio Railway) | Materializza un solo `ContextBundle` per tick, lo persiste come `context_snapshot` row | dati live (HL info, news, sentiment, on-chain) | row `context_snapshot` con `context_hash` |
| `ContextBuilder` (in ogni agent) | Legge il `context_snapshot` per il `tick_id` corrente dal DB (NON fetcha dati esterni) | `tick_id` | `ContextBundle` deserializzato |
| `PromptRenderer` | Materializza il prompt template con il contesto e il portfolio state | `ContextBundle` + portfolio_state + template | prompt finale + SHA256 hash (rendered_prompt_hash) |
| `LLMClient` (4 implementazioni) | Invoca il modello, parse JSON, fallback freetext, estrae token usage e reasoning trace | prompt + schema Pydantic `TradeDecision` | `TradeDecision` validato + metadati (tokens, latency, reasoning), DTO `CostEventData` da persistere dopo |
| `Guardrails` | Applica 4 vincoli Strategia C+, registra clamping | `TradeDecision` raw | `TradeDecision` post-clamp + flag attivazione |
| `HyperliquidClient` | Esegue ordini su HL testnet | `TradeDecision` post-clamp | fill report → 1 row `orders` per ordine elementare + `fee_event` |
| `OutcomeResolver` | Risolve outcome posizioni chiuse, calcola PnL netto | DB state + market state | rows `outcomes` (con `opening_action_id`) |
| `Repositories` (`db/repositories/`) | CRUD strutturato sul DB | SQLAlchemy session | entità domain |
| `StatsHandler` | Cost ledger per chiamata LLM | usage data | DTO `CostEventData` → persistito in `cost_events` dopo creazione `decision` |

---

## 3. Schema database (Postgres + SQLAlchemy 2.x)

Lo schema è **scientifico**: ogni decisione, azione, esito, costo, fee, funding, simulazione fiscale è loggato in tabelle dedicate con `experiment_id` + `model_id` + `run_id` end-to-end.

### 3.1 Mappa entità

```
experiments (1) ──< runs (N) ──< decisions (N) ──< decision_actions (N)
                          │            │                    │
                          │            │                    └──< orders (N)   -- 1 entry + SL + TP + close
                          │            └──< outcomes (N)
                          │                  ▲
                          │                  └──── opening_action_id FK to decision_actions
                          │                  └──── opening_run_id, closing_run_id FK to runs
                          │
                          └──< errors (N)
                          └──< account_snapshots (N)  -- 1 per run, include portfolio_state_hash
                          └──< llm_invocations (1)    -- nuisance snapshot per run

models (1) ──< runs (N)

context_snapshots (1, per tick) ──< runs (4, uno per modello, via FK composita su tick_id+experiment_id)
                                    ▲
                                    └──── tutti i 4 modelli leggono lo STESSO context_snapshot per il tick
context_build_runs (1, per tick) ──   audit log dei tentativi orchestrator (anche falliti)

positions (N)  -- 1:1 con decision_action di apertura via opening_action_id (UNIQUE)
            │  -- include opening_run_id
            │
            └──< fee_events (N)        -- via orders, include run_id
            └──< funding_events (N)    -- NO run_id (funding maturano nel periodo HL 8h, non per run)

cost_events (N) ── 1:1 con decision_id, include run_id
prompt_templates (1) ──< runs (N)      -- template statico
                          │
                          └── runs.rendered_prompt_hash  -- hash del prompt finalizzato per quella specifica run

tax_sim_periods (N)  -- 1 per (model_id, quarter), aggregato

baseline_configs (1, per experiment+baseline_name) ──< baseline_equity_snapshots (N)  -- preregistrazione + risultati
```

### 3.2 DDL completo

> Sintassi PostgreSQL 16. UUID generati lato app (Python `uuid4()`). Timestamp con timezone, UTC strict. I CREATE TABLE qui sotto sono lo schema target; in implementazione la Single Source of Truth saranno i `SQLAlchemy models` in `src/aiat/db/models/`, e le migrations Alembic deriveranno da quelli.
>
> **Convenzioni applicate al DDL**:
> - Ogni tabella operativa porta `experiment_id`, `model_id` e (dove sensato) `run_id` per query 1-hop coerenti con l'invariante #3
> - CHECK condizionali per evitare valori semanticamente vuoti (es. HOLD/FLAT senza leverage/size)
> - CHECK elementari sui valori finanziari (no leverage negativi, no token negativi, no latency negativa)
> - UNIQUE constraint sulle relazioni 1:1 dichiarate

#### 3.2.1 Tabelle anagrafiche e di configurazione

```sql
-- Configurazione dell'esperimento scientifico (1 row per la tesi).
CREATE TABLE experiments (
    id              UUID PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    git_commit_sha  TEXT NOT NULL,
    config_snapshot JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Anagrafica dei 4 modelli LLM partecipanti.
CREATE TABLE models (
    id                  TEXT PRIMARY KEY,                  -- es. "openai-gpt-5.1"
    provider            TEXT NOT NULL,
    model_name_api      TEXT NOT NULL,
    tier                TEXT NOT NULL CHECK (tier IN ('premium','cheap_alt')),
    geography           TEXT NOT NULL CHECK (geography IN ('USA','CN')),
    wallet_address      TEXT NOT NULL UNIQUE,
    pricing_input_usd_per_1m       NUMERIC(12,6) NOT NULL CHECK (pricing_input_usd_per_1m >= 0),
    pricing_output_usd_per_1m      NUMERIC(12,6) NOT NULL CHECK (pricing_output_usd_per_1m >= 0),
    pricing_reasoning_usd_per_1m   NUMERIC(12,6) NOT NULL DEFAULT 0 CHECK (pricing_reasoning_usd_per_1m >= 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Prompt template statico (separato dal rendered prompt per run).
-- Modifica del fix #10: questa tabella contiene SOLO il template, non il prompt finale.
CREATE TABLE prompt_templates (
    sha256_hash         TEXT PRIMARY KEY,                  -- hash del template + confidence_def
    label               TEXT NOT NULL UNIQUE,              -- es. "v1_baseline"
    template_text       TEXT NOT NULL,                     -- template raw con placeholder {{...}}
    confidence_def      TEXT NOT NULL,                     -- definizione confidence vincolata (RESEARCH §2.1)
    controlled_signals  JSONB NOT NULL,                    -- vocabolario controllato key_signals (RESEARCH §3.3)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### 3.2.2 Context snapshot (nuova tabella critica — fix punti 3 e 4)

```sql
-- Contesto di mercato materializzato per UN tick. Scritto SOLO dal context-orchestrator.
-- Letto dai 4 agent. Garantisce parità byte-identica del MARKET CONTEXT cross-model.
-- (Il prompt finale combina questo context con il portfolio_state model-specific:
--  vedi nota di disegno §3.3 e invariante #13 in §5.)
-- Fix punti 3 (riproducibilità) e 4 (parità cross-model) della review esterna.
CREATE TABLE context_snapshots (
    id                  UUID PRIMARY KEY,
    experiment_id       UUID NOT NULL REFERENCES experiments(id),
    tick_id             TEXT NOT NULL,                     -- es. "2026-05-14T14:30:00Z"
    tick_at             TIMESTAMPTZ NOT NULL,
    context_hash        TEXT NOT NULL,                     -- SHA256(context_json)
    context_json        JSONB NOT NULL,                    -- ContextBundle completo
    source_timestamps   JSONB NOT NULL,                    -- timestamp per ogni source: HL, RSS, F&G, ...
    build_duration_ms   INT NOT NULL CHECK (build_duration_ms >= 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, tick_id),
    UNIQUE (id, experiment_id, tick_id)                    -- per FK composita su runs (fix B.3 review-v2)
);
CREATE INDEX idx_context_tick ON context_snapshots(tick_at DESC);

-- Audit log dei tentativi del context-orchestrator (anche quelli falliti).
-- Fix B.2 review-v2: senza questa tabella, un fallimento dell'orchestrator pre-write
-- lascerebbe una run dell'agent in 'missed' senza fonte primaria per debugging.
-- Una row context_build_runs viene scritta SEMPRE all'inizio del tentativo
-- dell'orchestrator (status='running'), aggiornata a 'success'/'partial'/'failed'/'timeout'.
-- Se orchestrator crasha prima di scrivere row, gli agent leggono lo stato come 'missed'
-- (no row trovata) e creano i loro runs in status='missed'.
CREATE TABLE context_build_runs (
    id                  UUID PRIMARY KEY,
    experiment_id       UUID NOT NULL REFERENCES experiments(id),
    tick_id             TEXT NOT NULL,
    tick_at             TIMESTAMPTZ NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL,
    completed_at        TIMESTAMPTZ,
    status              TEXT NOT NULL CHECK (status IN (
                            'running','success','partial','failed','timeout'
                        )),
    failure_stage       TEXT,                                -- 'technical_fetch','sentiment_fetch','news_fetch','onchain_fetch','assemble','persist'
    error_context       JSONB,
    -- se status='success'/'partial', linkiamo allo snapshot prodotto
    context_snapshot_id UUID REFERENCES context_snapshots(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, tick_id)
);
CREATE INDEX idx_context_build_runs_tick ON context_build_runs(tick_at DESC);
CREATE INDEX idx_context_build_runs_status ON context_build_runs(status) WHERE status IN ('failed','timeout','partial');
```

#### 3.2.3 Run e decisioni

```sql
-- Una run = una singola invocazione del cron per un modello per un tick.
-- Fix punto 5: scheduled_for distinto da started_at, tick_id condiviso.
-- Fix punto 13: estesi gli status possibili.
CREATE TABLE runs (
    id                      UUID PRIMARY KEY,
    experiment_id           UUID NOT NULL REFERENCES experiments(id),
    model_id                TEXT NOT NULL REFERENCES models(id),
    tick_id                 TEXT NOT NULL,                  -- shared con context_snapshots.tick_id
    scheduled_for           TIMESTAMPTZ NOT NULL,           -- istante teorico del cron (HH:00/15/30/45)
    run_started_at          TIMESTAMPTZ NOT NULL,           -- istante reale di partenza
    run_completed_at        TIMESTAMPTZ,
    status                  TEXT NOT NULL CHECK (status IN (
                                'running','success','partial','failed','timeout','missed','skipped'
                            )),
    failure_stage           TEXT,                            -- es. 'context_fetch', 'llm_invoke', 'execution'
    last_completed_step     INT NOT NULL DEFAULT 0,
    -- prompt / context references
    prompt_template_hash    TEXT NOT NULL REFERENCES prompt_templates(sha256_hash),
    rendered_prompt_hash    TEXT NOT NULL,                  -- SHA256 del prompt finalizzato per QUESTA run
    rendered_prompt_text    TEXT,                            -- opzionale, popolato per audit; nullable per spazio
    context_snapshot_id     UUID NOT NULL REFERENCES context_snapshots(id),
    schema_version          TEXT NOT NULL,                  -- versione TradeDecision schema
    -- metadata operativi
    retry_count             INT NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    git_commit_sha          TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, model_id, scheduled_for),
    -- FK composita: garantisce che context_snapshot_id punti allo snapshot dello STESSO
    -- experiment_id e dello STESSO tick_id dichiarato dalla run (fix B.3 review-v2).
    -- Senza questo vincolo, schema permetterebbe runs.context_snapshot_id che riferisce
    -- snapshot di altri tick/esperimenti — improbabile applicativamente, ma scientificamente
    -- inaccettabile per pre-registrazione.
    FOREIGN KEY (context_snapshot_id, experiment_id, tick_id)
        REFERENCES context_snapshots(id, experiment_id, tick_id)
);
CREATE INDEX idx_runs_experiment_model_time ON runs(experiment_id, model_id, scheduled_for DESC);
CREATE INDEX idx_runs_tick ON runs(tick_id);

-- Snapshot delle nuisance variables LLM per ogni run.
-- Fix punto 14: provider, temperature, top_p, seed, ecc. come colonne query-friendly.
CREATE TABLE llm_invocations (
    id                      UUID PRIMARY KEY,
    run_id                  UUID NOT NULL UNIQUE REFERENCES runs(id),
    model_id                TEXT NOT NULL REFERENCES models(id),
    provider_snapshot       TEXT NOT NULL,
    model_name_api_snapshot TEXT NOT NULL,
    temperature             NUMERIC(4,3) CHECK (temperature IS NULL OR temperature >= 0),
    top_p                   NUMERIC(4,3) CHECK (top_p IS NULL OR (top_p > 0 AND top_p <= 1)),
    max_tokens              INT CHECK (max_tokens IS NULL OR max_tokens > 0),
    seed                    INT,
    llm_config_snapshot     JSONB NOT NULL,                  -- dump completo config
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Una decision = output strutturato del modello per una run (portfolio-level).
-- Vedi RESEARCH §1.0: unità di osservazione = (timestamp, model_id), non per simbolo.
-- Fix punto 2: confidence e time_horizon SPOSTATI su decision_actions (action-level).
-- Su decisions resta solo eventuale portfolio_confidence opzionale + reasoning globale.
CREATE TABLE decisions (
    id                  UUID PRIMARY KEY,
    run_id              UUID NOT NULL UNIQUE REFERENCES runs(id),
    -- denormalizzati per query veloci
    experiment_id       UUID NOT NULL REFERENCES experiments(id),
    model_id            TEXT NOT NULL REFERENCES models(id),
    decided_at          TIMESTAMPTZ NOT NULL,
    -- output decision-level (NON action-level: vedi decision_actions per quello)
    raw_response_id     TEXT,                               -- es. OpenAI response.id
    portfolio_reasoning TEXT NOT NULL,                       -- ragionamento globale del modello
    risk_assessment     TEXT NOT NULL,
    portfolio_confidence NUMERIC(5,4) CHECK (portfolio_confidence IS NULL OR (portfolio_confidence BETWEEN 0 AND 1)),  -- opzionale, riepilogativa
    -- metadati performance
    latency_ms          INT NOT NULL CHECK (latency_ms >= 0),
    fallback_used       BOOLEAN NOT NULL DEFAULT FALSE,
    raw_payload         JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_decisions_model_time ON decisions(model_id, decided_at DESC);
CREATE INDEX idx_decisions_experiment ON decisions(experiment_id, decided_at DESC);

-- Action elementare per simbolo all'interno di una decision.
-- Fix punto 2: confidence + time_horizon + reasoning + key_signals SONO QUI (action-level).
-- Fix punto 6: CHECK condizionali per HOLD/FLAT (campi nullable / 'none' / zero).
-- Fix punto 9: denormalizzo experiment_id, model_id, run_id per coerenza end-to-end.
CREATE TABLE decision_actions (
    id                  UUID PRIMARY KEY,
    decision_id         UUID NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    -- denormalizzati per coerenza con invariante #3
    experiment_id       UUID NOT NULL REFERENCES experiments(id),
    model_id            TEXT NOT NULL REFERENCES models(id),
    run_id              UUID NOT NULL REFERENCES runs(id),
    symbol              TEXT NOT NULL,
    -- output strutturato action-level (RESEARCH §2.1)
    confidence          NUMERIC(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    time_horizon_min    INT NOT NULL CHECK (time_horizon_min > 0),
    action_reasoning    TEXT NOT NULL,                       -- ragionamento per QUESTA action
    action_key_signals  JSONB NOT NULL DEFAULT '[]'::jsonb,  -- da vocabolario controllato
    -- decisione raw richiesta dal modello
    side_requested      TEXT NOT NULL CHECK (side_requested IN ('LONG','SHORT','FLAT','HOLD')),
    leverage_requested  NUMERIC(5,2) NOT NULL CHECK (leverage_requested >= 0),
    size_pct_requested  NUMERIC(5,4) NOT NULL CHECK (size_pct_requested >= 0 AND size_pct_requested <= 1),
    stop_loss_pct       NUMERIC(5,4) CHECK (stop_loss_pct IS NULL OR stop_loss_pct > 0),  -- NULL se HOLD/FLAT
    take_profit_pct     NUMERIC(5,4) CHECK (take_profit_pct IS NULL OR take_profit_pct > 0),  -- NULL se HOLD/FLAT
    entry_type          TEXT NOT NULL CHECK (entry_type IN ('market','limit','none')),
    limit_price         NUMERIC(20,8) CHECK (limit_price IS NULL OR limit_price > 0),  -- NULL se market/none
    -- decisione post-clamping (4 guardrail Strategia C+)
    side_executed       TEXT NOT NULL CHECK (side_executed IN ('LONG','SHORT','FLAT','HOLD')),
    leverage_executed   NUMERIC(5,2) NOT NULL CHECK (leverage_executed >= 0),
    size_pct_executed   NUMERIC(5,4) NOT NULL CHECK (size_pct_executed >= 0 AND size_pct_executed <= 1),
    -- flag attivazione guardrail
    leverage_clamped    BOOLEAN NOT NULL DEFAULT FALSE,
    size_pct_clamped    BOOLEAN NOT NULL DEFAULT FALSE,
    forced_hold         BOOLEAN NOT NULL DEFAULT FALSE,
    original_side       TEXT,                                -- valorizzato se forced_hold=true
    -- execution result
    -- fix B.4 review-v2: con l'introduzione della tabella `orders` (fix punto 7 review-v1),
    -- l'esecuzione non è più binaria. execution_status è la fonte primaria; executed
    -- (boolean) è mantenuto come campo derivato per backward-compat di query semplici.
    execution_status    TEXT NOT NULL DEFAULT 'pending' CHECK (execution_status IN (
                            'not_applicable',   -- side ∈ {HOLD, FLAT}
                            'pending',          -- ordini in submissione
                            'filled',           -- tutti gli ordini necessari riempiti
                            'partial',          -- alcuni filled, alcuni no (es. entry ok, SL rejected)
                            'failed',           -- entry failed o tutti gli ordini failed
                            'cancelled'         -- annullato esplicitamente
                        )),
    executed            BOOLEAN NOT NULL DEFAULT FALSE,      -- derivato: execution_status = 'filled'
    execution_error     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- CHECK semantici (fix punto 6)
    CONSTRAINT chk_hold_flat_no_sizing CHECK (
        side_requested NOT IN ('HOLD','FLAT')
        OR (size_pct_requested = 0 AND leverage_requested = 0 AND entry_type = 'none'
            AND stop_loss_pct IS NULL AND take_profit_pct IS NULL)
    ),
    CONSTRAINT chk_open_close_has_sizing CHECK (
        side_requested NOT IN ('LONG','SHORT')
        OR (size_pct_requested > 0 AND leverage_requested >= 1 AND entry_type IN ('market','limit')
            AND stop_loss_pct IS NOT NULL AND take_profit_pct IS NOT NULL)
    ),
    CONSTRAINT chk_limit_requires_price CHECK (
        entry_type != 'limit' OR limit_price IS NOT NULL
    ),
    CONSTRAINT chk_market_no_limit_price CHECK (
        entry_type NOT IN ('market','none') OR limit_price IS NULL
    )
);
CREATE UNIQUE INDEX uniq_action_decision_symbol ON decision_actions(decision_id, symbol);
CREATE INDEX idx_actions_symbol_time ON decision_actions(symbol, created_at DESC);
CREATE INDEX idx_actions_model_time ON decision_actions(model_id, created_at DESC);
```

#### 3.2.4 Stato del wallet e posizioni

```sql
CREATE TABLE account_snapshots (
    id              UUID PRIMARY KEY,
    run_id          UUID NOT NULL UNIQUE REFERENCES runs(id),
    experiment_id   UUID NOT NULL REFERENCES experiments(id),
    model_id        TEXT NOT NULL REFERENCES models(id),
    snapshot_at     TIMESTAMPTZ NOT NULL,
    equity_usd      NUMERIC(20,8) NOT NULL CHECK (equity_usd >= 0),
    available_usd   NUMERIC(20,8) NOT NULL CHECK (available_usd >= 0),
    margin_used_usd NUMERIC(20,8) NOT NULL CHECK (margin_used_usd >= 0),
    n_open_positions    INT NOT NULL DEFAULT 0 CHECK (n_open_positions >= 0),
    total_position_value_usd NUMERIC(20,8) NOT NULL DEFAULT 0 CHECK (total_position_value_usd >= 0),
    unrealized_pnl_usd  NUMERIC(20,8) NOT NULL DEFAULT 0,    -- signed
    portfolio_state_hash TEXT NOT NULL,                       -- SHA256 dello stato portafoglio (fix B.1 review-v2): permette audit della parte model-specific del prompt finale
    raw_account_state JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_snapshots_model_time ON account_snapshots(model_id, snapshot_at DESC);

-- Posizioni aperte e chiuse.
-- Fix punto 16: UNIQUE su opening_action_id.
CREATE TABLE positions (
    id                  UUID PRIMARY KEY,
    experiment_id       UUID NOT NULL REFERENCES experiments(id),
    model_id            TEXT NOT NULL REFERENCES models(id),
    opening_run_id      UUID NOT NULL REFERENCES runs(id),    -- run che ha generato l'apertura (fix #9 review-v2)
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL CHECK (side IN ('LONG','SHORT')),
    -- opening
    opening_action_id   UUID NOT NULL REFERENCES decision_actions(id),
    opened_at           TIMESTAMPTZ NOT NULL,
    entry_price         NUMERIC(20,8) NOT NULL CHECK (entry_price > 0),
    size_units          NUMERIC(20,8) NOT NULL CHECK (size_units > 0),
    leverage            NUMERIC(5,2) NOT NULL CHECK (leverage >= 1),
    notional_value_usd  NUMERIC(20,8) NOT NULL CHECK (notional_value_usd > 0),
    initial_margin_usd  NUMERIC(20,8) NOT NULL CHECK (initial_margin_usd > 0),
    stop_loss_price     NUMERIC(20,8) NOT NULL CHECK (stop_loss_price > 0),
    take_profit_price   NUMERIC(20,8) NOT NULL CHECK (take_profit_price > 0),
    -- closing (NULL se aperta)
    closing_action_id   UUID REFERENCES decision_actions(id),
    closed_at           TIMESTAMPTZ,
    exit_price          NUMERIC(20,8) CHECK (exit_price IS NULL OR exit_price > 0),
    close_reason        TEXT CHECK (close_reason IN ('manual','stop_loss','take_profit','liquidated','model_close')),
    realized_pnl_usd    NUMERIC(20,8),                       -- signed, NULL se aperta
    hl_position_id      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_position_closed_consistency CHECK (
        (closed_at IS NULL AND exit_price IS NULL AND realized_pnl_usd IS NULL
         AND close_reason IS NULL AND closing_action_id IS NULL)
        OR
        (closed_at IS NOT NULL AND exit_price IS NOT NULL AND realized_pnl_usd IS NOT NULL
         AND close_reason IS NOT NULL)
    )
);
CREATE UNIQUE INDEX uniq_positions_opening_action ON positions(opening_action_id);
CREATE INDEX idx_positions_model_open ON positions(model_id, closed_at) WHERE closed_at IS NULL;
CREATE INDEX idx_positions_model_symbol ON positions(model_id, symbol, opened_at DESC);
```

#### 3.2.5 Orders / Fills (nuova tabella — fix punto 7)

```sql
-- Audit completo degli ordini emessi su Hyperliquid.
-- Fix punto 7: una decision_action può generare N ordini (entry, SL, TP, close, retry).
-- Permette tracciamento di slippage, fill parziale, errori execution, audit fee per ordine.
CREATE TABLE orders (
    id                      UUID PRIMARY KEY,
    decision_action_id      UUID NOT NULL REFERENCES decision_actions(id),
    experiment_id           UUID NOT NULL REFERENCES experiments(id),
    model_id                TEXT NOT NULL REFERENCES models(id),
    run_id                  UUID NOT NULL REFERENCES runs(id),  -- run di submission (fix #9 review-v2)
    symbol                  TEXT NOT NULL,
    order_kind              TEXT NOT NULL CHECK (order_kind IN ('entry','stop_loss','take_profit','close')),
    -- identificatori esterni
    hl_order_id             TEXT,
    client_order_id         TEXT,
    -- stato e prezzi
    status                  TEXT NOT NULL CHECK (status IN ('pending','filled','partial','cancelled','rejected','triggered')),
    requested_price         NUMERIC(20,8) CHECK (requested_price IS NULL OR requested_price > 0),
    filled_price            NUMERIC(20,8) CHECK (filled_price IS NULL OR filled_price > 0),
    requested_size_units    NUMERIC(20,8) NOT NULL CHECK (requested_size_units > 0),
    filled_size_units       NUMERIC(20,8) CHECK (filled_size_units IS NULL OR filled_size_units >= 0),
    slippage_bps            NUMERIC(10,4),                   -- basis points, signed
    -- raw audit
    raw_order_response      JSONB NOT NULL,
    submitted_at            TIMESTAMPTZ NOT NULL,
    filled_at               TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_orders_action ON orders(decision_action_id);
CREATE INDEX idx_orders_model_time ON orders(model_id, submitted_at DESC);
CREATE INDEX idx_orders_status ON orders(status) WHERE status IN ('pending','partial');
```

#### 3.2.6 Ledger costi (fee, funding, API, tasse)

```sql
-- Fee Hyperliquid: ora legato a `orders`, non più direttamente a `actions`.
CREATE TABLE fee_events (
    id              UUID PRIMARY KEY,
    order_id        UUID NOT NULL REFERENCES orders(id),
    position_id     UUID NOT NULL REFERENCES positions(id),
    experiment_id   UUID NOT NULL REFERENCES experiments(id),
    model_id        TEXT NOT NULL REFERENCES models(id),
    run_id          UUID NOT NULL REFERENCES runs(id),         -- run che ha generato l'ordine (fix #9 review-v2)
    fee_type        TEXT NOT NULL CHECK (fee_type IN ('taker_open','taker_close','maker_open','maker_close')),
    fee_usd         NUMERIC(20,8) NOT NULL CHECK (fee_usd >= 0),
    occurred_at     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_fee_events_model_time ON fee_events(model_id, occurred_at DESC);

-- Funding rate pagato/ricevuto su posizioni aperte.
-- funding_amount_usd resta signed (può essere pagato o ricevuto).
CREATE TABLE funding_events (
    id              UUID PRIMARY KEY,
    position_id     UUID NOT NULL REFERENCES positions(id),
    experiment_id   UUID NOT NULL REFERENCES experiments(id),
    model_id        TEXT NOT NULL REFERENCES models(id),
    funding_rate    NUMERIC(10,8) NOT NULL,                  -- signed
    funding_amount_usd NUMERIC(20,8) NOT NULL,               -- signed: + = paghi, - = ricevi
    funding_period_start TIMESTAMPTZ NOT NULL,
    funding_period_end   TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (funding_period_end > funding_period_start)
);
CREATE INDEX idx_funding_model_time ON funding_events(model_id, funding_period_end DESC);

-- Costo API LLM per ogni chiamata.
-- Fix punto 1: decision_id resta UNIQUE, ma cost_events si scrive DOPO la persistenza della decision.
-- L'invocazione LLMClient.invoke() restituisce un DTO CostEventData; la persistenza avviene nella stessa
-- transazione di insert su decisions, NON dentro l'invoke.
CREATE TABLE cost_events (
    id                  UUID PRIMARY KEY,
    decision_id         UUID NOT NULL UNIQUE REFERENCES decisions(id),
    experiment_id       UUID NOT NULL REFERENCES experiments(id),
    model_id            TEXT NOT NULL REFERENCES models(id),
    run_id              UUID NOT NULL REFERENCES runs(id),     -- coerenza end-to-end (fix #9 review-v2)
    input_tokens        INT NOT NULL CHECK (input_tokens >= 0),
    output_tokens       INT NOT NULL CHECK (output_tokens >= 0),
    reasoning_tokens    INT NOT NULL DEFAULT 0 CHECK (reasoning_tokens >= 0),
    cost_usd            NUMERIC(12,8) NOT NULL CHECK (cost_usd >= 0),
    pricing_snapshot    JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_cost_model_time ON cost_events(model_id, created_at DESC);

-- Simulazione fiscale controfattuale (RESEARCH §3.3).
-- Fix punto 15: aggregato per (model_id, quarter), NON per position (evita falsa precisione).
CREATE TABLE tax_sim_periods (
    id                  UUID PRIMARY KEY,
    experiment_id       UUID NOT NULL REFERENCES experiments(id),
    model_id            TEXT NOT NULL REFERENCES models(id),
    quarter_label       TEXT NOT NULL,                       -- es. "2026Q2"
    period_start        TIMESTAMPTZ NOT NULL,
    period_end          TIMESTAMPTZ NOT NULL,
    total_pnl_gross_usd NUMERIC(20,8) NOT NULL,               -- signed: somma realized PnL del periodo
    total_fees_usd      NUMERIC(20,8) NOT NULL CHECK (total_fees_usd >= 0),
    total_funding_usd   NUMERIC(20,8) NOT NULL,               -- signed
    taxable_base_usd    NUMERIC(20,8) NOT NULL,               -- max(0, gross - fees - funding) in IT
    tax_rate_pct        NUMERIC(5,4) NOT NULL DEFAULT 0.26 CHECK (tax_rate_pct >= 0 AND tax_rate_pct <= 1),
    tax_due_usd         NUMERIC(20,8) NOT NULL CHECK (tax_due_usd >= 0),
    n_positions_closed  INT NOT NULL CHECK (n_positions_closed >= 0),
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, model_id, quarter_label),
    CHECK (period_end > period_start)
);
CREATE INDEX idx_tax_model ON tax_sim_periods(model_id);

-- NOTA su future allocations: una tabella tax_sim_allocations(position_id, tax_share_usd) può essere
-- aggiunta come future work per attribuzioni pro-rata. Non richiesta per la tesi (vedi negoziazione
-- punto 15 review esterna): la tassazione italiana è aggregata per legge, allocare pro-rata è
-- interpretazione, non normativa.
```

#### 3.2.7 Outcome scientifici

```sql
-- Outcome di una position chiusa. 1:1 con position.
-- Fix punto 8: aggiunto opening_action_id per linkare confidence action-level a outcome.
-- Fix punto 2 (riflesso): decision_confidence ora legge da decision_actions, non da decisions.
CREATE TABLE outcomes (
    id                              UUID PRIMARY KEY,
    position_id                     UUID NOT NULL UNIQUE REFERENCES positions(id),
    opening_action_id               UUID NOT NULL REFERENCES decision_actions(id),
    opening_run_id                  UUID NOT NULL REFERENCES runs(id),   -- run di apertura (fix #9 review-v2)
    closing_run_id                  UUID NOT NULL REFERENCES runs(id),   -- run di chiusura (fix #9 review-v2)
    experiment_id                   UUID NOT NULL REFERENCES experiments(id),
    model_id                        TEXT NOT NULL REFERENCES models(id),
    symbol                          TEXT NOT NULL,
    -- esito quantitativo
    realized_pnl_gross_usd          NUMERIC(20,8) NOT NULL,   -- signed
    sum_fees_usd                    NUMERIC(20,8) NOT NULL CHECK (sum_fees_usd >= 0),
    sum_funding_usd                 NUMERIC(20,8) NOT NULL,   -- signed
    pnl_net_fee_usd                 NUMERIC(20,8) NOT NULL,   -- signed
    pnl_net_fee_funding_usd         NUMERIC(20,8) NOT NULL,   -- signed
    -- tax allocation (pro-rata, opzionale): NON usato per tesi, lasciato per future work
    pnl_net_fee_funding_tax_sim_usd NUMERIC(20,8) NOT NULL,   -- signed
    -- esito binario per Brier score (RESEARCH §4.2)
    was_profitable_net              BOOLEAN NOT NULL,         -- pnl_net_fee_funding_usd > 0
    holding_duration_min            INT NOT NULL CHECK (holding_duration_min >= 0),
    -- confidence calibration check (proviene da decision_actions, action-level)
    decision_action_confidence      NUMERIC(5,4) NOT NULL CHECK (decision_action_confidence BETWEEN 0 AND 1),
    decision_action_time_horizon_min INT NOT NULL CHECK (decision_action_time_horizon_min > 0),
    horizon_met                     BOOLEAN NOT NULL,         -- chiusura entro time_horizon?
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_outcomes_model_time ON outcomes(model_id, created_at DESC);
CREATE INDEX idx_outcomes_confidence ON outcomes(model_id, decision_action_confidence);
CREATE INDEX idx_outcomes_action ON outcomes(opening_action_id);
```

#### 3.2.8 Baseline (DDL pre-registrato — fix punto 11 + B.5)

```sql
-- Configurazione pre-registrata dei baseline non-LLM.
-- Fix B.5 review-v2: i parametri del naive momentum (EMA(20)/EMA(50), SL 3%, TP 6%, leva 3×)
-- devono vivere in DB con hash, non solo nel testo del PRD/RESEARCH. Garantisce
-- pre-registrazione tecnica e impedisce modifiche silenti durante o dopo l'esperimento.
-- Una row per (experiment_id, baseline_name), scritta da scripts/seed_experiment.py.
CREATE TABLE baseline_configs (
    id              UUID PRIMARY KEY,
    experiment_id   UUID NOT NULL REFERENCES experiments(id),
    baseline_name   TEXT NOT NULL CHECK (baseline_name IN ('buy_and_hold','cash','naive_momentum_ema_20_50')),
    config_json     JSONB NOT NULL,                          -- es. {"ema_fast":20,"ema_slow":50,"sl_pct":0.03,"tp_pct":0.06,"leverage":3,...}
    config_hash     TEXT NOT NULL,                           -- SHA256(canonical_json(config_json))
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, baseline_name)
);

-- Snapshot equity dei baseline non-LLM, calcolati a posteriori.
-- Fix punto 11 review-v1: DDL definito ORA, popolato a fine esperimento da scripts/compute_baselines.py.
-- Permette confronto fair contro i 4 modelli LLM per ogni tick.
CREATE TABLE baseline_equity_snapshots (
    id              UUID PRIMARY KEY,
    experiment_id   UUID NOT NULL REFERENCES experiments(id),
    baseline_config_id UUID NOT NULL REFERENCES baseline_configs(id),  -- link a config preregistrata (fix B.5)
    baseline_name   TEXT NOT NULL CHECK (baseline_name IN ('buy_and_hold','cash','naive_momentum_ema_20_50')),
    tick_id         TEXT NOT NULL,                            -- shared con context_snapshots.tick_id
    tick_at         TIMESTAMPTZ NOT NULL,
    equity_usd      NUMERIC(20,8) NOT NULL CHECK (equity_usd >= 0),
    pnl_usd_cumulative NUMERIC(20,8) NOT NULL,               -- signed
    raw_state       JSONB NOT NULL,                           -- per audit: posizioni baseline, prezzi, ecc.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, baseline_name, tick_id)
);
CREATE INDEX idx_baseline_name_time ON baseline_equity_snapshots(baseline_name, tick_at DESC);
```

#### 3.2.9 Errori e diagnostica

```sql
CREATE TABLE errors (
    id              UUID PRIMARY KEY,
    run_id          UUID REFERENCES runs(id),
    decision_id     UUID REFERENCES decisions(id),
    experiment_id   UUID REFERENCES experiments(id),
    model_id        TEXT REFERENCES models(id),
    error_kind      TEXT NOT NULL,                            -- "llm_parse_failed", "hl_execution_error", "context_fetch_failed", ...
    error_message   TEXT NOT NULL,
    stack_trace     TEXT,
    context         JSONB,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_errors_model_time ON errors(model_id, occurred_at DESC);
CREATE INDEX idx_errors_kind ON errors(error_kind, occurred_at DESC);
```

### 3.3 Note di disegno DB

**Perché `experiment_id`/`model_id`/`run_id` denormalizzati ovunque?**
Tradeoff pragmatico (fix punto 9 review-v1, completato in review-v2): l'invariante #3 richiede `experiment_id` + `model_id` + `run_id` end-to-end sulle tabelle operative. In v3 sono stati aggiunti `run_id` mancanti su: `positions` (come `opening_run_id`), `orders` (`run_id`), `fee_events` (`run_id`), `cost_events` (`run_id`), `outcomes` (`opening_run_id` + `closing_run_id`). Unica eccezione motivata: `funding_events` NON ha `run_id` — i funding maturano nei periodi 8h di Hyperliquid, non sono attribuibili a una run specifica; quindi tracciarli per run sarebbe forzato e fuorviante. Restano `experiment_id` + `model_id` per coerenza con #3.

**Perché tabelle separate `prompt_templates` vs `runs.rendered_prompt_hash`?**
Fix punto 10 review-v1: il *template statico* (placeholder + confidence_def) ha hash stabile e vive in `prompt_templates`. Il *prompt finalizzato* (template + contesto + portfolio_state interpolati) cambia ad ogni run e il suo hash si salva in `runs.rendered_prompt_hash`. Sono due oggetti diversi: confondere il template con il prompt rendered è un errore di modellazione.

**Perché `context_snapshots` scritta dal context-orchestrator?**
Fix punti 3 e 4 review-v1: senza una tabella che contenga il `ContextBundle` materializzato e leggibile dai 4 agent, (a) la riproducibilità è cifrata-only (hash senza contenuto), (b) la parità cross-model non è garantita (4 servizi che fetcham ono indipendentemente). La nuova architettura sposta la materializzazione del contesto in un servizio dedicato, eliminando il problema alla radice.

**Perché due tabelle distinte `context_snapshots` + `context_build_runs`?**
Fix B.2 review-v2: `context_snapshots` contiene **solo** snapshot validi (success/partial). `context_build_runs` è l'**audit log primario** dei tentativi: scritto sempre, anche se l'orchestrator fallisce. Senza questa separazione, un tick fallito sarebbe diagnosabile solo *post-hoc* dai 4 runs.status='missed' degli agent, senza sapere il motivo (RSS timeout? HL down? assemble bug? DB issue?). Il pattern *audit table separata da data table* è uno standard per sistemi che richiedono tracciabilità anche dei fallimenti.

**Perché `portfolio_state_hash` su `account_snapshots`?**
Fix B.1 review-v2: il prompt finale somministrato a ciascun modello combina (a) **market context byte-identico cross-model** dal `context_snapshot` con (b) **portfolio state isolato per modello** dall'`account_snapshot`. Loggare l'hash dello stato portafoglio permette audit completo della porzione model-specific del prompt. La distinzione *market context parity vs portfolio independence* va riportata anche nel testo della tesi.

**Perché FK composita `runs(context_snapshot_id, experiment_id, tick_id)` → `context_snapshots`?**
Fix B.3 review-v2: il vincolo composito impedisce a livello schema che una `runs` row referenzi uno `context_snapshot` di un altro `tick_id` o `experiment_id`. Applicativamente improbabile, ma per pre-registrazione scientifica lo schema deve rendere lo stato inconsistente *impossibile*, non solo *improbabile*.

**Perché `execution_status` invece di solo `executed BOOLEAN`?**
Fix B.4 review-v2: con la tabella `orders` (introdotta in v2), una `decision_action` può avere stati intermedi non binari: entry filled + SL rejected + TP pending. `execution_status` è la fonte primaria; `executed` resta come campo derivato boolean per backward-compat di query semplici.

**Perché `baseline_configs` separata da `baseline_equity_snapshots`?**
Fix B.5 review-v2: i parametri pre-registrati del naive momentum (EMA(20)/EMA(50), SL 3%, TP 6%, leva 3×) devono vivere in DB con hash, non solo nel testo del PRD/RESEARCH. Una row in `baseline_configs` per `(experiment_id, baseline_name)` con `config_json` + `config_hash` garantisce: (a) i parametri non vengono cambiati silenti durante l'esperimento, (b) ogni `baseline_equity_snapshot` linka esplicitamente alla config usata.

**Perché tabella `orders`?**
Fix punto 7 review-v1: `decision_actions.executed BOOLEAN` non basta. Una sola action può generare: 1 market order entry, 1 SL trigger, 1 TP trigger, eventualmente 1 close order del modello successivo. Con `orders` ogni ordine elementare è auditabile: slippage, fill parziale, retry, status finale, fee per ordine.

**Perché `tax_sim_periods` aggregato, non per position?**
Fix punto 15 review-v1: la tassazione italiana sui capital gain è aggregata trimestralmente per legge, con compensazione perdite/profitti nello stesso quarter. Allocare `tax_due` per singola position è interpretazione, non normativa. L'aggregato per `(model_id, quarter)` è scientificamente onesto. Allocazioni pro-rata restano future work.

**Perché `outcomes.opening_action_id` e `decision_action_confidence`?**
Fix punto 2 (riflesso) + punto 8 review-v1: la confidence ora vive su `decision_actions` (action-level), e l'outcome scientifico (Brier score, calibration) deriva da quella confidence specifica. Senza link diretto a `opening_action_id`, il calcolo richiede JOIN con `positions → decision_actions` ad ogni query.

**Indici progettati per le query di RESEARCH §4**:
- PnL aggregato per modello su finestra temporale → `idx_outcomes_model_time`
- Confidence calibration (Brier score per modello) → `idx_outcomes_confidence`
- Cost ledger cumulato → `idx_cost_model_time`
- Posizioni aperte runtime → `idx_positions_model_open` (parziale, solo aperte)
- Audit ordini ancora in transito → `idx_orders_status` (parziale, solo `pending`/`partial`)
- Tentativi orchestrator falliti → `idx_context_build_runs_status` (parziale, solo failed/timeout/partial)

**Migrations Alembic**:
- 1 migration di bootstrap (`001_initial_schema.py`) contiene tutto il DDL sopra
- Migrations successive solo per estensioni emerse durante sviluppo
- `alembic.ini` configurato per leggere `AIAT_DATABASE_URL` da env

---

## 4. Flussi dati principali

### 4.1 Flusso 1 — Decision Loop (15-minute tick)

Sequenza: il **context-orchestrator** parte sul tick esatto (HH:00/15/30/45 UTC), materializza il context. I **4 agent** partono ~30 secondi dopo, leggono il context dal DB ed eseguono il loop.

```
T0      APScheduler context-orchestrator trigger (es. 14:30:00 UTC)
        │
        ▼
T0+0s   ContextOrchestrator.build_tick_context() entry
        │
        ├─ [CO.1] Fetch parallelo con timeout per source:
        │   ├─ technical (HL info, candles 15m)          timeout 10s
        │   ├─ sentiment (Fear&Greed)                    timeout 5s, cached TTL 60s
        │   ├─ news (RSS sources)                        timeout 8s, cached TTL 90s
        │   └─ onchain (HL funding, OI, liquidations)    timeout 10s
        │
        ├─ [CO.2] Assembla ContextBundle, calcola context_hash (SHA256)
        │
        └─ [CO.3] Persiste row in `context_snapshots` (tick_id, context_json, hash)
        │
        ▼
T0+15s  context_snapshot ready in DB

T0+30s  APScheduler agent triggers (4 agent indipendenti, in parallelo)
        │
        ▼
T0+30s  decision_loop.run_once(tick_id) entry  [per ognuno dei 4 agent]
        │
        ▼
T0+31s  [1] Crea row in `runs` (status='running', scheduled_for=T0, tick_id, git_commit_sha,
        │       prompt_template_hash, schema_version)
        │
        ▼
T0+32s  [2] HyperliquidClient.fetch_account_state() → balance, posizioni aperte
        │   └─ persiste `account_snapshot` row
        │
        ▼
T0+33s  [3] Legge `context_snapshot` per tick_id corrente dal DB
        │   ├─ se NULL (orchestrator non ha completato): retry max 3× ogni 5s, poi status='missed'
        │   └─ valida context_hash byte-identico atteso
        │
        ▼
T0+34s  [4] PromptRenderer.render(template, context, portfolio_state, confidence_def)
        │   └─ produce prompt finale + SHA256 hash → `runs.rendered_prompt_hash`
        │
        ▼
T0+34s  [5] LLMClient.invoke(prompt, schema=TradeDecision)
        │   ├─ struttura primaria: with_structured_output
        │   ├─ fallback se fallisce: freetext + regex parsing
        │   ├─ hard timeout 90s (vedi fix punto 12)
        │   └─ restituisce TradeDecision + DTO `CostEventData` (input/output/reasoning tokens, cost_usd)
        │   (latency tipica: 2-30s; range osservabile: 1-90s)
        │
        ▼
T0+~50s [6] Guardrails.apply(trade_decision)
        │   ├─ check 1: SL/TP presenti se side ∈ {LONG, SHORT}
        │   ├─ check 2: size_pct ≤ AIAT_MAX_SIZE_PCT (clamp se eccede)
        │   ├─ check 3: leverage ≤ 1 + confidence × 9 (clamp se eccede)
        │   ├─ check 4: if confidence < AIAT_MIN_OPEN_CONFIDENCE → force HOLD
        │   └─ restituisce TradeDecision post-clamp + flag attivazione
        │
        ▼
T0+~50s [7] In UNA SINGOLA TRANSAZIONE DB (fix punto 1):
        │   ├─ INSERT decisions
        │   ├─ INSERT decision_actions (1 per simbolo, con confidence/time_horizon/reasoning action-level)
        │   ├─ INSERT cost_events (decision_id derivato, FK valida)
        │   ├─ INSERT llm_invocations (nuisance snapshot)
        │   └─ COMMIT
        │
        ▼
T0+~52s [8] Per ogni decision_action eseguibile (side LONG/SHORT/FLAT):
        │   HyperliquidClient.execute(action)
        │   ├─ market order → INSERT `orders` (order_kind='entry', status, filled_price, slippage_bps)
        │   ├─ submit SL trigger → INSERT `orders` (order_kind='stop_loss')
        │   ├─ submit TP trigger → INSERT `orders` (order_kind='take_profit')
        │   ├─ se apertura: INSERT `positions` row
        │   ├─ se chiusura: UPDATE `positions` + INSERT `outcomes` row
        │   └─ per ogni fill: INSERT `fee_events` (FK a `orders`)
        │
        ▼
T0+~100s [9] OutcomeResolver.check_pending_closures()
        │   per ogni position aperta del modello:
        │   ├─ check se SL/TP scattati nel frattempo (HL state)
        │   ├─ se chiusa → INSERT outcomes (con opening_action_id, decision_action_confidence)
        │   └─ se ancora aperta → marca per check al prossimo tick
        │
        ▼
T0+~110s [10] UPDATE `runs.status` = 'success' | 'partial' | 'failed' | 'timeout' | 'missed' | 'skipped'
        │       + last_completed_step
        │
        ▼
T0+~120s decision_loop.run_once() returns
```

**Budget realistico (fix punto 12)**:

| Fase | Budget tipico | Hard timeout |
|------|---------------|--------------|
| Context orchestrator (fetch parallelo + persist) | 5-15s | 30s |
| Singolo agent: account state | 1-3s | 10s |
| Singolo agent: lettura context_snapshot | <1s | 5s (con retry) |
| Singolo agent: LLM invoke | 2-30s | **90s** |
| Singolo agent: guardrails + DB write | 1-2s | 10s |
| Singolo agent: HL execution (multipli ordini) | 5-30s | 60s |
| Singolo agent: outcome resolver | 1-10s | 30s |
| **Singolo agent: loop end-to-end** | **30-120s** | **180s** (hard) |

**Tutto il loop entro 180s** è ampiamente sotto il cron 15m (900s), permettendo margine per retry e jitter. Se il loop supera 180s, `runs.status='timeout'` e la run viene marcata come fallita.

**Configurazione APScheduler vincolata (fix punto 12)**:

```python
# in scheduler.py
job_defaults = {
    'coalesce': True,                # se più trigger pendenti, esegui solo l'ultimo
    'max_instances': 1,              # non lanciare run sovrapposte sullo stesso job
    'misfire_grace_time': 60,        # 60s di tolleranza prima di marcare missed
}
# job context-orchestrator: trigger CronTrigger ai minuti 0/15/30/45
# job agent: trigger CronTrigger ai minuti 0/15/30/45 con start_delay=30s
```

**Note critiche**:
- Step `[3]` può fallire se context-orchestrator è in errore: in quel caso `runs.status='missed'` (fix punto 13). Mai usare context di tick precedente, mai costruirsi un context "fallback locale" — perderebbe la parità cross-model.
- Step `[5]` è la prima fonte di non-determinismo. Tokens, latency, output JSON variano per chiamata.
- Step `[7]` è la transazione critica: tutto-o-niente. Se fallisce un'INSERT, rollback completo → run in `failed` con `failure_stage='persist'`.
- Step `[8]` è la seconda fonte di non-determinismo: prezzo di fill testnet.
- Una run può chiudersi in `status='partial'` se alcuni step opzionali falliscono (es. SL trigger HL fallisce ma entry è andata). Loggato in `errors` con context.

### 4.2 Flusso 2 — Funding & Outcome Resolution (asincrono)

Job parallelo APScheduler che gira ogni 8 ore (frequenza funding Hyperliquid):

```
ogni 8h:
  per ogni position OPEN:
    fetch funding rate da HL
    crea funding_event row

  per ogni position chiusa nell'ultimo intervallo:
    se outcome row non esiste:
      calcola pnl_net_fee, pnl_net_fee_funding
      verifica horizon_met (chiusura entro decision.time_horizon_min)
      crea outcome row
```

### 4.3 Flusso 3 — Tax Simulation (trimestrale / fine esperimento)

Job triggerabile esplicitamente via `scripts/compute_tax_sim.py`:

```
per ogni (model_id, quarter):
  somma realized_pnl_net_fee_funding di outcomes chiuse nel quarter
  applica compensazione perdite/profitti (semplificazione: somma algebrica)
  taxable_base = MAX(0, somma)
  tax_due = taxable_base × 0.26
  crea/aggiorna UNA SOLA row tax_sim_periods per (model_id, quarter)
```

**Importante**: nel DDL (§3.2.6) la tabella è `tax_sim_periods`, aggregata per `(model_id, quarter_label)`. NON viene creata una row per ogni position chiusa (questo era il design vecchio della v1, scartato dopo la review esterna — vedi note di disegno §3.3 e RESEARCH §3.3). Allocazioni pro-rata per position restano future work.

### 4.4 Flusso 4 — Baseline Computation (a posteriori)

Job una-tantum a fine esperimento via `scripts/compute_baselines.py`:

```
per ogni baseline in [BuyAndHold, Cash, NaiveMomentum]:
  per ogni tick_id nell'esperimento:
    leggi context_snapshot[tick_id] per i prezzi spot al tick
    computa equity ipotetica della baseline applicando le sue regole
    (parametri pre-registrati in RESEARCH §3.3 — vincolanti)
    persiste in tabella `baseline_equity_snapshots` (fix punto 11: DDL già pronto)
```

**Decisione di disegno (fix punto 11)**:
- Il DDL della tabella `baseline_equity_snapshots` è **già pronto in §3.2.8** e fa parte della migration di bootstrap, NON aggiunto a fine esperimento. Garanzia di riproducibilità.
- I baseline non vengono eseguiti in tempo reale durante l'esperimento: sono ricalcolabili a posteriori dai `context_snapshots` (che contengono i prezzi spot al tick) + parametri pre-registrati.
- I parametri del naive momentum (EMA(20)/EMA(50), SL 3%, TP 6%, leva 3×) sono **fissati prima** del run sperimentale per evitare ottimizzazione a posteriori — vedi RESEARCH §3.3.

Quindi: tabella esiste dal day-1, popolata a fine esperimento. Riproducibilità completa.

---

## 5. Vincoli architetturali invarianti

Questi vincoli sono **non negoziabili**, derivano da PRE_PRD, RESEARCH_DESIGN e dalla review esterna del PRD Round 1 v1:

1. **Isolamento cross-model**: nessun servizio agent legge mai dati di altri `model_id` durante il proprio decision loop. Tutte le query degli agent sono filtrate `WHERE model_id = $AIAT_MODEL_ID`. Verificato da `tests/e2e/test_isolation.py` (Round 2). Il context-orchestrator è eccezione esplicita: scrive un `context_snapshot` agnostico ai modelli, leggibile in sola lettura da tutti.

2. **Determinismo configurazione**: ogni `run` logga `git_commit_sha`, `prompt_template_hash`, `rendered_prompt_hash`, `context_snapshot_id`, `schema_version`, oltre alle nuisance variables LLM in `llm_invocations`. Permette di ricostruire qualsiasi decisione offline.

3. **Schema scientifico end-to-end DAVVERO presente** (fix punto 9 review): `experiment_id` + `model_id` + (dove sensato) `run_id` sono **denormalizzati esplicitamente** su tutte le tabelle operative: `decisions`, `decision_actions`, `outcomes`, `cost_events`, `fee_events`, `funding_events`, `orders`, `positions`, `account_snapshots`, `errors`. Non è solo dichiarato: è implementato nel DDL.

4. **Cost ledger persistito DOPO la decision row** (fix punto 1 review): `LLMClient.invoke()` NON scrive direttamente in `cost_events`. Restituisce un DTO `CostEventData`; la persistenza avviene nella stessa transazione DB che insert sulla `decisions` row, garantendo FK valida. Pattern: build DTO → start transaction → insert decisions → insert decision_actions → insert cost_events → insert llm_invocations → commit.

5. **Memoria 2 off di default**: `ContextBuilder` (lato agent) ha `inject_decision_history: bool = False` come default Pydantic Settings (`AIAT_INJECT_DECISION_HISTORY=false`). Per la tesi resta sempre off.

6. **Vocabolario controllato `key_signals`**: il prompt forza la scelta da lista chiusa (definita in `context/controlled_signals.py` + salvata in `prompt_templates.controlled_signals`). Validazione Pydantic post-LLM rigetta `signal_id` non in lista.

7. **Confidence sempre presente, action-level** (fix punto 2 review): per ogni `decision_action`, anche con `side` HOLD/FLAT, `confidence` e `time_horizon_min` sono `NOT NULL`. La confidence portfolio-level su `decisions` è opzionale.

8. **4 guardrail sempre attivi**: nessun flag può disabilitarli durante l'esperimento. Possono essere parametrizzati (size cap, leverage cap, min confidence) via env, ma mai disattivati. Validato all'avvio.

9. **No mainnet**: configurazione vincolata `AIAT_NETWORK=testnet`. Validation all'avvio che lancia `RuntimeError` se diverso, prima di qualsiasi connessione a HL.

10. **Logging strutturato**: nessun `print()` nel codice runtime. Tutto via `structlog`, output JSON. Validato da `ruff` rule (`T201` enabled).

11. **No raw SQL ad-hoc nel runtime** (fix punto 20 review, riformulato): tutto via SQLAlchemy ORM/Core nei moduli `db/repositories/`. Eccezioni esplicitamente consentite per: (a) migrations Alembic, (b) advisory locks Postgres se serviranno, (c) query analitiche incapsulate in repository dedicati, (d) script di export in `scripts/`. La regola si applica al **codice runtime**, non al codice di analisi.

12. **Decimal per soldi**: ogni calcolo monetario (size, price, fee, PnL) usa `decimal.Decimal`, mai `float`. SQLAlchemy column type `Numeric` (mappato a `NUMERIC` in Postgres).

13. **Parità di market context cross-model garantita dal Context Orchestrator** (fix punto 4 review-v1 + raffinamento B.1 review-v2): il `context_snapshot` per ogni tick è materializzato da **un solo servizio** (`context-orchestrator`), e tutti i 4 agent leggono lo **stesso identico** `context_snapshot.id`. Nessun agent fetcha mai sorgenti esterne durante la propria run. Garanzia: **market context byte-identico** ai 4 modelli per ogni tick. **Il prompt finale** ricevuto da ciascun modello **non** è byte-identico cross-model, perché include il `portfolio_state` isolato per wallet (che evolve indipendentemente dopo il primo tick di trading). Questa è una proprietà desiderata, non un bug: ogni modello opera sul proprio portfolio. La distinzione *market parity vs portfolio independence* va dichiarata esplicitamente nella tesi (`RESEARCH §3.2`). Verificato da `tests/e2e/test_context_parity.py` (Round 2).

14. **Moduli disaccoppiati senza dipendenze cicliche** (fix punto 19 review, riformulato): i sotto-package (`domain/`, `db/`, `context/`, `llm/`, `execution/`, `orchestration/`, `observability/`) dipendono solo da `domain/` e da interfacce esplicite tra di loro. `orchestration/` compone i moduli; sono vietate dipendenze cicliche e chiamate laterali tra moduli runtime. Verificato da `import-linter` configurato in CI.

15. **Tutti i tick hanno esito tracciato** (fix punto 13 review): ogni cron tick produce esattamente 4 `runs` rows (una per agent), con `status` ∈ `{running, success, partial, failed, timeout, missed, skipped}`. La copertura dei tick è una metrica di qualità della pre-registrazione: `n_tick_total - n_tick_success` è un KPI di affidabilità sperimentale.

---

*Fine PRD V2 Round 1 v3 (post 2 cicli di peer-review esterna, 26 fix totali integrati: 18 da review v1→v2 + 8 da review v2→v3). Blueprint tecnico pronto per commit. Prossimi round: contratti API + LLM provider abstraction + test plan (Round 2); deploy strategy + milestones + risk register + propagation map (Round 3).*
