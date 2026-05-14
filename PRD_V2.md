# PRD V2 — AI Trading Agent (Thesis Edition)

> Documento tecnico-implementativo per l'agente V2. Traduce le 18 decisioni del `PRE_PRD.md` (commit `73e3d02`) e la cornice scientifica del `RESEARCH_DESIGN.md` (commit `2e1df14`) in specifiche eseguibili: architettura moduli, schema DB completo (DDL), contratti API, flussi dati, strategia di deploy, test plan, milestones, risk register.
>
> **Stato**: ROUND 3 v2/3 — completo, post peer-review esterna (verdetto 9.3/10 APPROVATO). Integra Round 1 v3 (commit `669ced9`) + Round 2 v3 (commit `e80c16e`) + Round 3 v2 (§11-§15). Patch post-review: §15.4 trasformata da *deferred decisions indefinite* a *bounded deferrals* con milestone vincolante di chiusura.
>
> Branch: `prd/v2-design`. Documento pronto come blueprint completo per implementazione Phase 5.

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
- **Decoupling con regola netta sulla parità sperimentale**: se il context-orchestrator fallisce per un tick, gli agent del medesimo tick chiudono la propria run in `status='missed'`. **Mai usare un `context_snapshot` di tick precedente come fallback**: l'interpolazione di un contesto rotto romperebbe la parità sperimentale e produrrebbe decisioni su input "vecchi" non comparabili con quelle degli altri tick. Regola unica e categorica: *se il `context_snapshot` del tick corrente non esiste, il tick è missed*.
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
    n_attempts          INT NOT NULL DEFAULT 1 CHECK (n_attempts >= 1),  -- numero tentativi LLM aggregati (fix B.16 review-r2-v2): 1=solo primary, 2=primary+fallback freetext
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

**Perché `cost_events.n_attempts`?**
Fix B.16 review-r2-v2: il `StatsCallbackHandler` aggrega tokens su primary + eventuale fallback freetext (vedi §8.3). Senza colonna dedicata, l'informazione "questa decisione è costata 2 tentativi LLM" sarebbe persa nello schema. Per l'analisi scientifica (RESEARCH §3.3: robustezza cross-model) sapere quante volte un modello richiede il fallback è una metrica di affidabilità del provider/configurazione. Range tipico: `n_attempts ∈ {1, 2}`; `>2` indicherebbe un bug nell'`invoke_structured` (no retry oltre fallback per design).

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

---

## 6. Schemi Pydantic (dominio applicativo)

Tutti gli schemi del dominio applicativo vivono in `src/aiat/domain/schemas.py`. Sono **Pydantic v2** (validazione strict, type hints rigorosi, esempi nei `model_config`).

### 6.1 Enums

```python
# src/aiat/domain/enums.py
from enum import StrEnum

class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"
    HOLD = "HOLD"

class EntryType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    NONE = "none"

class Tier(StrEnum):
    PREMIUM = "premium"
    CHEAP_ALT = "cheap_alt"

class Geography(StrEnum):
    USA = "USA"
    CN = "CN"

class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMEOUT = "timeout"
    MISSED = "missed"
    SKIPPED = "skipped"

class ExecutionStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"

class OrderKind(StrEnum):
    ENTRY = "entry"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    CLOSE = "close"

class CloseReason(StrEnum):
    MANUAL = "manual"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    LIQUIDATED = "liquidated"
    MODEL_CLOSE = "model_close"
```

### 6.2 Schema decisione (output LLM)

Lo schema **vincolante** che l'LLM deve produrre. Usato con `with_structured_output` di langchain. Validazione Pydantic rigetta automaticamente output malformati (→ fallback freetext).

```python
# src/aiat/domain/schemas.py
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from aiat.domain.enums import Side, EntryType

# Vocabolario controllato dei key_signals (RESEARCH §3.3, PRD §3.2.1 controlled_signals)
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

    # Action-level scientific outputs (RESEARCH §1.0 + §2.1)
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
                raise ValueError("HOLD/FLAT must not specify limit_price")  # fix A.1 review-r2
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
    portfolio_confidence: Annotated[Decimal | None, Field(default=None, ge=0, le=1, decimal_places=4)]
    actions: Annotated[list[ActionDecision], Field(min_length=3, max_length=3)] = Field(
        description="Exactly 3 actions, one per symbol (BTC, ETH, SOL), in any order."
    )

    @model_validator(mode="after")
    def validate_all_symbols_covered(self) -> "TradeDecision":
        symbols = {a.symbol for a in self.actions}
        if symbols != {"BTC", "ETH", "SOL"}:
            raise ValueError(f"actions must cover exactly BTC/ETH/SOL, got {symbols}")
        return self
```

**Note di disegno**:
- `extra="forbid"`: campi extra rigettati. Impedisce all'LLM di inventare campi non previsti.
- `Decimal` ovunque per valori monetari/percentuali (invariante #12).
- `ControlledSignal` come `Literal[...]` di stringhe: il vocabolario è enforcato a livello type-check (rifiuta `"RSI overbought"` se non in lista).
- `model_validator(mode="after")` per i vincoli condizionali → questi sono il riflesso Pydantic dei CHECK constraint DDL `chk_hold_flat_no_sizing` e `chk_open_close_has_sizing`.

### 6.3 Schemi di contesto (input ContextBuilder/Renderer)

```python
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


class PortfolioState(BaseModel):
    """Stato model-specific. Diverge cross-model dopo il primo tick (RESEARCH §3.2)."""
    model_config = ConfigDict(extra="forbid")
    equity_usd: Decimal
    available_usd: Decimal
    margin_used_usd: Decimal
    n_open_positions: int
    unrealized_pnl_usd: Decimal
    open_positions: list["OpenPositionSummary"]


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
    source_timestamps: dict[str, str]  # {"technical": "2026-...", "sentiment": "...", ...}
```

### 6.4 DTO interni runtime

```python
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
    n_attempts: Annotated[int, Field(ge=1)] = 1  # 1 = solo primary; 2 = primary + fallback


class LLMInvocationResult(BaseModel):
    """Output completo di LLMClient.invoke()."""
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    decision: TradeDecision
    cost: CostEventData
    latency_ms: Annotated[int, Field(ge=0)]
    raw_response_id: str | None = None
    raw_payload: dict
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
    final_action: ActionDecision  # post-clamping
```

---

## 7. Contratti API tra moduli

Tutti i moduli runtime espongono **interfacce esplicite** (ABC o Protocol). Le implementazioni concrete possono cambiare senza rompere i chiamanti. Verificato in CI da `import-linter` (invariante #14).

### 7.1 ContextOrchestrator (5° servizio Railway)

```python
# src/aiat/orchestration/context_orchestrator.py
from typing import Protocol
from aiat.domain.schemas import ContextBundle

class ContextOrchestrator(Protocol):
    """Materializza UN context_snapshot per tick. Eseguito sul 5° servizio Railway."""

    async def build_tick_context(self, tick_id: str, experiment_id: str) -> ContextBundle:
        """
        Atomicità: scrive UN solo context_snapshot per (experiment_id, tick_id).
        Su fallimento, scrive comunque una context_build_run row con status='failed'.

        Hard timeout: 30s totali (vedi §4.1 timeline).
        Raises:
            ContextBuildError: timeout, source unavailable, persist failure.
        """
        ...
```

### 7.2 BaseCollector (collectors interni al ContextOrchestrator)

```python
# src/aiat/context/collectors/base.py
from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class BaseCollector(ABC, Generic[T]):
    """Base per collectors: technical, sentiment, news, onchain."""

    timeout_seconds: int  # per-source timeout (vedi §4.1)
    cache_ttl_seconds: int  # 0 = no cache

    @abstractmethod
    async def collect(self) -> T:
        """
        Returns:
            Pydantic model con i dati raccolti.
        Raises:
            CollectorTimeoutError: se supera self.timeout_seconds.
            CollectorSourceError: se la source remota fallisce.
        """
        ...
```

### 7.3 BaseLLMClient (interfaccia uniforme per i 4 provider)

```python
# src/aiat/llm/base.py
from abc import ABC, abstractmethod
from aiat.domain.schemas import LLMInvocationResult, TradeDecision

class BaseLLMClient(ABC):
    """
    Interfaccia uniforme per OpenAI, Anthropic, OpenAICompatible (DeepSeek/Qwen).

    Implementa:
      1. Primary: with_structured_output(TradeDecision) via langchain
      2. Fallback: freetext + regex JSON extraction
      3. Cost tracking: ritorna CostEventData (persistito DOPO da chiamante,
         invariante #4)
      4. Nuisance snapshot: provider/model/temperature/top_p/seed nei result
    """

    provider: str
    model_name_api: str

    @abstractmethod
    async def invoke(
        self,
        prompt: str,
        *,
        timeout_seconds: int = 90,  # hard timeout (vedi §4.1)
    ) -> LLMInvocationResult:
        """
        Returns:
            LLMInvocationResult con decision validata Pydantic + cost + nuisance.
        Raises:
            LLMTimeoutError: superato timeout_seconds.
            LLMUnrecoverableError: anche il fallback freetext ha fallito parsing.
        """
        ...
```

### 7.4 Guardrails (Protocol con 4 strategie componibili)

```python
# src/aiat/execution/guardrails.py
from typing import Protocol
from aiat.domain.schemas import TradeDecision, GuardrailReport

class GuardrailStrategy(Protocol):
    """4 guardrail Strategia C+ (PRE_PRD §13.3, mai disattivabili — invariante #8)."""

    def apply(
        self,
        decision: TradeDecision,
        *,
        max_size_pct: Decimal,           # AIAT_MAX_SIZE_PCT (default 0.20)
        hard_max_leverage: Decimal,      # AIAT_HARD_MAX_LEVERAGE (default 10)
        min_open_confidence: Decimal,    # AIAT_MIN_OPEN_CONFIDENCE (default 0.4)
    ) -> tuple[TradeDecision, list[GuardrailReport]]:
        """
        Applica i 4 guardrail in ordine:
          1. SL/TP mandatory check (Figma F1) — rifiuta o downgrade a HOLD se mancanti
          2. size_pct ≤ max_size_pct (clamp)
          3. leverage ≤ min(1 + confidence × 9, hard_max_leverage) (clamp)
          4. if confidence < min_open_confidence → force HOLD

        Returns:
            (decision_post_clamp, reports) — `reports` ha una row per action con
            flag leverage_clamped/size_pct_clamped/forced_hold/original_side.
        """
        ...
```

### 7.5 HyperliquidClient (execution layer)

```python
# src/aiat/execution/hyperliquid_client.py
from abc import ABC, abstractmethod
from aiat.domain.schemas import ActionDecision, PortfolioState

class HyperliquidClient(ABC):
    """Wrapper testnet del Hyperliquid SDK."""

    @abstractmethod
    async def fetch_portfolio_state(self) -> PortfolioState:
        """Snapshot dello stato wallet. Letto a inizio di ogni decision_loop."""
        ...

    @abstractmethod
    async def execute_action(
        self,
        action: ActionDecision,
        run_id: str,
        current_position: OpenPositionSummary | None,
    ) -> list["OrderResult"]:
        """
        Esegue una action conoscendo lo stato corrente della posizione.

        Args:
            action: la action post-guardrail da eseguire.
            run_id: per audit, FK in `orders.run_id`.
            current_position: posizione attualmente aperta per `action.symbol`, o None
                se nessuna posizione aperta. Necessaria perché FLAT è semanticamente
                una chiusura/riduzione (non un ordine autonomo): senza conoscere lo
                stato corrente non è possibile decidere se FLAT richiede un close order,
                nessun order, o reduce-only sizing. Fix A.2/B.4 review-r2.

        Semantica per `action.side`:
            LONG/SHORT: se current_position è None, apre nuova; se esiste della
                stessa side, ignora (no reverse implicito, no add-to-position in v2);
                se esiste della opposite side, prima close, poi open (2 fasi distinte).
            FLAT: se current_position è None, no-op (returns []); altrimenti
                close-only con reduce_only order.
            HOLD: no-op (returns []).

        Returns:
            Lista di OrderResult, una per ordine elementare submesso (entry + SL + TP,
            oppure close-only).

        Raises:
            ExecutionRejectedError: ordine rifiutato da HL (margin, size limits, ...).
            ExecutionTimeoutError: timeout 60s superato.
        """
        ...

    @abstractmethod
    async def check_position_closure(
        self,
        hl_position_id: str,
    ) -> "PositionClosureInfo | None":
        """Ritorna None se la posizione è ancora aperta."""
        ...


class OrderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hl_order_id: str
    client_order_id: str
    order_kind: OrderKind
    status: Literal["pending", "filled", "partial", "cancelled", "rejected", "triggered"]
    requested_price: Decimal | None
    filled_price: Decimal | None
    requested_size_units: Decimal
    filled_size_units: Decimal | None
    slippage_bps: Decimal | None
    fee_usd: Decimal | None
    raw_response: dict


class PositionClosureInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    closed_at: str
    exit_price: Decimal
    close_reason: CloseReason
    realized_pnl_usd: Decimal
```

### 7.6 Repository pattern (aggregato per bounded context)

Repository "stretti", uno per **bounded context transazionale**, non uno per tabella. Tutti async, ricevono `AsyncSession` esterna (gestita dall'orchestrator).

```python
# src/aiat/db/repositories/decisions.py
from sqlalchemy.ext.asyncio import AsyncSession
from aiat.domain.schemas import TradeDecision, CostEventData, LLMInvocationResult
from aiat.db.models import Decision, DecisionAction, CostEvent, LLMInvocation

class DecisionsRepository:
    """
    Gestisce TUTTO il bounded context di una decisione in UNA transazione:
      decisions + decision_actions + cost_events + llm_invocations (invariante #4).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def persist_decision(
        self,
        *,
        run_id: str,
        experiment_id: str,
        model_id: str,
        invocation: LLMInvocationResult,
        post_guardrail_actions: list[ActionDecision],
        guardrail_reports: list[GuardrailReport],
    ) -> str:
        """
        Persiste in UNA SOLA transazione:
          1. INSERT decisions
          2. INSERT decision_actions (3 rows: BTC/ETH/SOL)
          3. INSERT cost_events
          4. INSERT llm_invocations

        Returns:
            decision_id appena creato.
        Raises:
            IntegrityError se FK o CHECK falliscono → rollback completo.
        """
        ...

    async def get_by_run(self, run_id: str) -> Decision | None: ...
    async def get_action_history(
        self,
        model_id: str,
        symbol: str,
        since: str,
    ) -> list[DecisionAction]: ...


# src/aiat/db/repositories/positions.py
class PositionsRepository:
    """
    Bounded context: positions + orders + fee_events + funding_events.
    Tutto ciò che concerne lo stato di una posizione e i suoi costi di esecuzione.
    """

    async def open_position(
        self,
        action_id: str,
        order_results: list[OrderResult],
        run_id: str,
    ) -> str: ...

    async def close_position(
        self,
        position_id: str,
        closure: PositionClosureInfo,
        closing_run_id: str,
    ) -> None: ...

    async def list_open_for_model(self, model_id: str) -> list[Position]: ...


# src/aiat/db/repositories/snapshots.py
class SnapshotsRepository:
    """account_snapshots (con portfolio_state_hash) + context_snapshots."""

    async def persist_account_snapshot(
        self,
        run_id: str,
        portfolio_state: PortfolioState,
    ) -> str: ...

    async def get_context_snapshot(
        self,
        experiment_id: str,
        tick_id: str,
    ) -> ContextSnapshot | None: ...


# src/aiat/db/repositories/runs.py
class RunsRepository:
    """runs + errors. Lifecycle della run."""

    async def create_run(self, ...) -> str: ...
    async def update_status(self, run_id: str, status: RunStatus, failure_stage: str | None = None) -> None: ...
    async def log_error(self, ...) -> None: ...


# src/aiat/db/repositories/outcomes.py
class OutcomesRepository:
    """outcomes (per analisi scientifica)."""

    async def persist_outcome(self, ...) -> str: ...
    async def list_for_model_in_window(self, model_id: str, start: str, end: str) -> list[Outcome]: ...


# src/aiat/db/repositories/context_build.py
# Fix B.5 review-r2: repository per context_snapshots + context_build_runs.
# Vive nel servizio context-orchestrator (5° Railway service), NON negli agent.
class ContextBuildRepository:
    """
    Bounded context: context_snapshots + context_build_runs.
    Usato esclusivamente dal context-orchestrator.
    """

    async def start_build(
        self,
        experiment_id: str,
        tick_id: str,
        tick_at: str,
    ) -> str:
        """Crea context_build_runs row con status='running'. Returns build_run_id."""
        ...

    async def complete_build(
        self,
        build_run_id: str,
        status: Literal["success", "partial"],
        context_bundle: ContextBundle,
        build_duration_ms: int,
    ) -> str:
        """
        Persiste context_snapshots + aggiorna context_build_runs.context_snapshot_id
        in singola transazione. Returns context_snapshot_id.
        """
        ...

    async def fail_build(
        self,
        build_run_id: str,
        failure_stage: str,
        error_context: dict,
        status: Literal["failed", "timeout"] = "failed",
    ) -> None:
        """Aggiorna context_build_runs senza creare context_snapshot."""
        ...

    async def get_snapshot_for_tick(
        self,
        experiment_id: str,
        tick_id: str,
    ) -> ContextSnapshot | None:
        """Usata dagli agent per leggere il context del tick corrente (read-only)."""
        ...


# src/aiat/db/repositories/baselines.py
# Fix B.5 review-r2: repository per baseline_configs + baseline_equity_snapshots.
# Usato da scripts/seed_experiment.py (write baseline_configs) e
# scripts/compute_baselines.py (write baseline_equity_snapshots a fine esperimento).
class BaselineRepository:
    """
    Bounded context: baseline_configs + baseline_equity_snapshots.
    Le configs sono pre-registrate al seed; le equity snapshots a posteriori.
    """

    async def register_baseline_config(
        self,
        experiment_id: str,
        baseline_name: str,
        config_json: dict,
    ) -> str:
        """Calcola config_hash da canonical_json(config_json). Returns baseline_config_id."""
        ...

    async def get_baseline_config(
        self,
        experiment_id: str,
        baseline_name: str,
    ) -> BaselineConfig | None: ...

    async def persist_equity_snapshot(
        self,
        baseline_config_id: str,
        tick_id: str,
        tick_at: str,
        equity_usd: Decimal,
        pnl_usd_cumulative: Decimal,
        raw_state: dict,
    ) -> str: ...

    async def list_equity_history(
        self,
        experiment_id: str,
        baseline_name: str,
    ) -> list[BaselineEquitySnapshot]: ...


# src/aiat/db/repositories/tax_simulation.py
# Fix B.5 review-r2: repository per tax_sim_periods (aggregato trimestrale).
# Usato da scripts/compute_tax_sim.py a fine esperimento (o trimestralmente).
class TaxSimulationRepository:
    """Bounded context: tax_sim_periods (aggregato per model_id × quarter)."""

    async def compute_and_persist_period(
        self,
        experiment_id: str,
        model_id: str,
        quarter_label: str,
        period_start: str,
        period_end: str,
        outcomes_in_period: list[Outcome],
        tax_rate_pct: Decimal = Decimal("0.26"),
    ) -> str:
        """
        Aggrega gli outcomes del periodo, calcola taxable_base con compensazione
        algebrica, persiste tax_sim_periods row. Returns tax_sim_period_id.
        """
        ...

    async def list_for_model(
        self,
        model_id: str,
    ) -> list[TaxSimPeriod]: ...
```

**Regole di repository**:
- **Transaction policy (fix B.6 review-r2)**: i metodi repository possono fare `flush()` se serve un ID generato, ma **NON fanno mai `commit()` o `rollback()` autonomi**. Il `commit()`/`rollback()` finale appartiene esclusivamente al layer di orchestrazione (`orchestration/decision_loop.py` o `orchestration/context_orchestrator.py`), che gestisce la UnitOfWork. Questo garantisce che operazioni multi-repository in uno stesso "use case" siano atomicamente all-or-nothing. Violare questa regola spezza l'invariante #4 (cost ledger atomico con decision).
- Nessun `commit()` interno: la transazione è gestita dall'orchestrator (UnitOfWork pattern leggero).
- Tutti i metodi sono `async` (SQLAlchemy 2.x async).
- Nessuna query SQL raw (invariante #11), eccetto su query analitiche complesse incapsulate qui.
- Type hints rigorosi su return: nessun `dict[str, Any]`.

---

## 8. LLM Provider Abstraction

### 8.1 Factory pattern + dispatcher

```python
# src/aiat/llm/factory.py
from aiat.config.settings import AgentSettings
from aiat.llm.base import BaseLLMClient
from aiat.llm.openai_client import OpenAIClient
from aiat.llm.anthropic_client import AnthropicClient
from aiat.llm.openai_compatible_client import OpenAICompatibleClient

def load_llm(settings: AgentSettings) -> BaseLLMClient:
    """
    Dispatcher basato su settings.llm_provider. Letto all'avvio del servizio agent.

    Type contract (fix B.17 review-r2-v2): la firma accetta SOLO AgentSettings,
    NON BaseAIATSettings né ContextOrchestratorSettings. Il context-orchestrator
    NON deve mai chiamare load_llm() — non possiede credenziali LLM (least
    privilege, vedi §10.3). Il type checker (mypy strict) rifiuta a compile-time
    qualsiasi tentativo di chiamare load_llm() da ContextOrchestratorSettings.

    Mapping provider → client:
      provider="openai"     → OpenAIClient
      provider="anthropic"  → AnthropicClient
      provider="deepseek"   → OpenAICompatibleClient(base_url=DEEPSEEK_URL)
      provider="qwen"       → OpenAICompatibleClient(base_url=QWEN_URL)
    """
    match settings.llm_provider:
        case "openai":
            return OpenAIClient(
                api_key=settings.openai_api_key,
                model_name=settings.model_name_api,
                temperature=settings.temperature,
                top_p=settings.top_p,
                max_tokens=settings.max_tokens,
                seed=settings.seed,
            )
        case "anthropic":
            return AnthropicClient(
                api_key=settings.anthropic_api_key,
                model_name=settings.model_name_api,
                temperature=settings.temperature,
                max_tokens=settings.max_tokens,
            )
        case "deepseek":
            return OpenAICompatibleClient(
                api_key=settings.deepseek_api_key,
                model_name=settings.model_name_api,
                base_url="https://api.deepseek.com/v1",
                # ...
            )
        case "qwen":
            return OpenAICompatibleClient(
                api_key=settings.qwen_api_key,
                model_name=settings.model_name_api,
                base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                # ...
            )
        case _:
            raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")
```

### 8.2 Strategia structured output (with_structured_output + fallback)

#### Exception hierarchy (fix B.7 review-r2)

```python
# src/aiat/llm/exceptions.py
class LLMError(Exception):
    """Base exception per tutti gli errori LLM."""
    pass

class LLMTimeoutError(LLMError):
    """Timeout superato. NON triggera fallback freetext.

    Razionale: timeout indica problema infrastrutturale (provider lento, network
    issue, prompt troppo lungo). Un secondo tentativo freetext probabilmente
    fallirebbe ancora e brucerebbe quota API. Propagato come errore infrastrutturale,
    la run si chiude con runs.status='timeout'.
    """
    pass

class LLMRateLimitError(LLMError):
    """Rate limit del provider. NON triggera fallback freetext.

    Razionale: secondo tentativo aggraverebbe il rate limit. Propagato.
    """
    pass

class LLMAuthError(LLMError):
    """Auth fallita (chiave invalida, scaduta, permessi insufficienti).
    NON triggera fallback freetext. Fatale: la run finisce in 'failed' con
    failure_stage='llm_auth'.
    """
    pass

class LLMParsingError(LLMError):
    """Output LLM non parsabile come TradeDecision valida.

    QUESTO è l'unico caso che triggera fallback freetext: il modello ha risposto
    ma con struttura JSON malformata o valori che falliscono Pydantic validation.
    """
    pass

class LLMUnrecoverableError(LLMError):
    """Anche il fallback freetext ha fallito parsing. La run termina in 'failed'
    con failure_stage='llm_parse'.
    """
    def __init__(self, primary_error: Exception, fallback_error: Exception):
        self.primary_error = primary_error
        self.fallback_error = fallback_error
        super().__init__(f"primary={primary_error!r}; fallback={fallback_error!r}")
```

#### invoke_structured con fallback selettivo

```python
# src/aiat/llm/structured.py
import asyncio
import json
from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError
from aiat.domain.schemas import TradeDecision, CostEventData
from aiat.llm.exceptions import (
    LLMTimeoutError, LLMRateLimitError, LLMAuthError,
    LLMParsingError, LLMUnrecoverableError,
)
from aiat.llm.stats_handler import StatsCallbackHandler

async def invoke_structured(
    llm: BaseChatModel,
    prompt: str,
    *,
    timeout_seconds: int,
    stats_handler: StatsCallbackHandler,
) -> tuple[TradeDecision, bool]:
    """
    Returns:
        (TradeDecision validato, fallback_used: bool).

    Fix B.7 review-r2: fallback freetext SOLO per parsing failure (ValidationError,
    JSONDecodeError, output malformato). Timeout, rate limit, auth error vanno
    propagati come errori dedicati.

    Fix B.8 review-r2: stats_handler accumula tokens di TUTTI i tentativi (primary
    + eventuale fallback), così il CostEventData finale rappresenta il costo totale
    sostenuto per produrre questa decisione.

    Cost ledger:
        stats_handler è passato come callback a langchain. Accumula tokens su:
          - tentativo primary (with_structured_output)
          - tentativo fallback (freetext) se eseguito
        Il chiamante invoca poi stats_handler.build_cost_event() per ottenere il
        CostEventData aggregato (con n_attempts ∈ {1, 2}).

    Path 1 (primary): llm.with_structured_output(TradeDecision)
        Eccezioni catturate per fallback:
          - ValidationError (Pydantic)
          - json.JSONDecodeError
          - langchain.OutputParserException
        Eccezioni propagate (NON fallback):
          - asyncio.TimeoutError → LLMTimeoutError
          - provider-specific rate limit → LLMRateLimitError
          - 401/403 → LLMAuthError

    Path 2 (fallback): re-invoke con FALLBACK_SUFFIX, regex JSON balanced
        Se anche questo fallisce con ValidationError/JSONDecodeError →
        LLMUnrecoverableError.
    """
    # PATH 1: primary attempt
    try:
        structured_llm = llm.with_structured_output(TradeDecision).with_config(
            {"callbacks": [stats_handler]}
        )
        result = await asyncio.wait_for(
            structured_llm.ainvoke(prompt),
            timeout=timeout_seconds,
        )
        return (result, False)
    except asyncio.TimeoutError as e:
        raise LLMTimeoutError(f"primary attempt timed out after {timeout_seconds}s") from e
    except Exception as e:
        # Classificazione errori provider-specific
        if _is_rate_limit_error(e):
            raise LLMRateLimitError(str(e)) from e
        if _is_auth_error(e):
            raise LLMAuthError(str(e)) from e
        # Solo parsing-like errors triggerano fallback (fix B.7)
        if not _is_parsing_error(e):
            raise LLMError(f"unexpected primary error: {e!r}") from e
        primary_error = e
        # cade nel PATH 2

    # PATH 2: fallback freetext (solo per parsing failure)
    try:
        raw_llm = llm.with_config({"callbacks": [stats_handler]})
        raw_response = await asyncio.wait_for(
            raw_llm.ainvoke(prompt + FALLBACK_SUFFIX),
            timeout=timeout_seconds,
        )
        extracted_json = _extract_json_balanced(raw_response.content)
        decision = TradeDecision.model_validate(json.loads(extracted_json))
        return (decision, True)
    except asyncio.TimeoutError as e:
        raise LLMTimeoutError(f"fallback attempt timed out after {timeout_seconds}s") from e
    except (ValidationError, json.JSONDecodeError, ValueError) as fallback_error:
        raise LLMUnrecoverableError(
            primary_error=primary_error,
            fallback_error=fallback_error,
        )


FALLBACK_SUFFIX = """

IMPORTANT: Your previous response could not be parsed. Respond NOW with ONLY a
valid JSON object matching the TradeDecision schema. No markdown fences, no
explanation, just the JSON.
"""


def _is_parsing_error(e: Exception) -> bool:
    """True se l'eccezione è dovuta a output malformato (NON timeout/rate/auth)."""
    from pydantic import ValidationError
    from langchain_core.exceptions import OutputParserException
    return isinstance(e, (ValidationError, json.JSONDecodeError, OutputParserException))


def _is_rate_limit_error(e: Exception) -> bool:
    """
    Strategia attesa in implementazione (fix B.18 review-r2-v2):

    PRIMARY: isinstance() check su exception class ufficiali dei 4 SDK.
      - openai.RateLimitError (per OpenAI nativo)
      - anthropic.RateLimitError (per Anthropic)
      - Per DeepSeek/Qwen via OpenAI-compatible: spesso le exception class del
        SDK OpenAI vengono propagate; testare specificamente per ogni provider
        durante l'implementazione (può variare nel tempo).

    FALLBACK: string matching su messaggio per coprire provider OpenAI-compatible
    che NON sempre rispettano le exception class del SDK OpenAI ufficiale.

    Lista finale delle exception class da implementare in PRD Round 3 →
    implementazione concreta dopo verifica versioni SDK al momento dello sviluppo.
    """
    # Implementazione concreta (placeholder fragile, da rafforzare con isinstance
    # in produzione):
    err_str = str(e).lower()
    return any(token in err_str for token in [
        "rate limit", "429", "too many requests", "quota exceeded"
    ])


def _is_auth_error(e: Exception) -> bool:
    """Stessa strategia di _is_rate_limit_error: isinstance() primary, string fallback.

    Exception class attese:
      - openai.AuthenticationError, openai.PermissionDeniedError
      - anthropic.AuthenticationError, anthropic.PermissionDeniedError
      - HTTP 401/403 wrapped exception per provider compatibili
    """
    err_str = str(e).lower()
    return any(token in err_str for token in [
        "401", "403", "unauthorized", "invalid api key", "authentication"
    ])
```

#### Estrazione JSON robusta (fix B.9 review-r2)

```python
def _extract_json_balanced(text: str) -> str:
    """
    Estrae il primo blocco JSON bilanciato {...} dal testo, gestendo correttamente
    graffe DENTRO stringhe JSON (es. nel campo `portfolio_reasoning` che può
    contenere "{...}" testualmente).

    Fix B.9 review-r2: tracciamento state machine in_string / escape per evitare
    di contare graffe letterali dentro string literals come delimitatori strutturali.

    Stati: NORMAL, IN_STRING, IN_STRING_ESCAPE.
    """
    NORMAL, IN_STRING, IN_STRING_ESCAPE = 0, 1, 2
    state = NORMAL
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if state == NORMAL:
            if ch == '"':
                state = IN_STRING
            elif ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    return text[start:i+1]
                if depth < 0:
                    raise ValueError(f"Unbalanced '}}' at position {i}")
        elif state == IN_STRING:
            if ch == '\\':
                state = IN_STRING_ESCAPE
            elif ch == '"':
                state = NORMAL
        elif state == IN_STRING_ESCAPE:
            state = IN_STRING  # ignora il char escapato
    raise ValueError("No balanced JSON object found in text")
```

### 8.3 Cost tracking (StatsCallbackHandler)

Adottato il pattern di TradingAgents (vedi ANALYSIS §3.4):

```python
# src/aiat/llm/stats_handler.py
from decimal import Decimal
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from aiat.domain.schemas import CostEventData

class StatsCallbackHandler(AsyncCallbackHandler):
    """
    Callback langchain che cattura usage tokens da OGNI provider in modo uniforme.
    Restituisce CostEventData che il chiamante persiste DOPO la decisione
    (invariante #4 — NO writes diretti al DB qui).

    Fix B.8 review-r2: aggrega tokens su MULTIPLI tentativi (primary + fallback
    freetext eventuale). `n_attempts` traccia quante chiamate LLM sono state
    eseguite; `cost_usd` finale = somma di tutte. Questo assicura che il cost
    ledger rifletta il COSTO REALE sostenuto per produrre una decisione, non
    solo il costo dell'ultimo tentativo.
    """

    def __init__(self, pricing: dict[str, Decimal]) -> None:
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.reasoning_tokens: int = 0
        self.n_attempts: int = 0
        self._pricing = pricing

    async def on_llm_end(self, response, **kwargs) -> None:
        usage = self._extract_usage(response)
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.reasoning_tokens += usage.get("reasoning_tokens", 0)
        self.n_attempts += 1

    def build_cost_event(self) -> CostEventData:
        cost_usd = (
            Decimal(self.input_tokens) * self._pricing["input"] / Decimal("1000000")
            + Decimal(self.output_tokens) * self._pricing["output"] / Decimal("1000000")
            + Decimal(self.reasoning_tokens) * self._pricing["reasoning"] / Decimal("1000000")
        )
        return CostEventData(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
            cost_usd=cost_usd,
            pricing_snapshot=self._pricing,
            n_attempts=max(1, self.n_attempts),  # almeno 1 per coerenza Pydantic
        )

    def _extract_usage(self, response) -> dict[str, int]:
        """
        OpenAI: response_metadata['token_usage']['prompt_tokens'/'completion_tokens']
                + response_metadata['token_usage']['completion_tokens_details']['reasoning_tokens']
        Anthropic: response.usage.input_tokens/output_tokens
                   (thinking mode esponi reasoning trace ma billing è incluso in output;
                    se in futuro Anthropic esponesse reasoning_tokens separati, qui)
        OpenAI-compatible (DeepSeek R1): response.usage include reasoning_tokens per R1
                                         e completion_tokens_details per il chain-of-thought
        Qwen: response.usage standard OpenAI-compatible, reasoning_tokens=0 di default
        """
        ...
```

### 8.4 Pricing config (cost ledger source of truth)

```yaml
# src/aiat/config/model_pricing.yaml
# USD per 1M tokens. Aggiornato al committed_at; pricing_snapshot in DB conserva
# il valore usato per ogni invocazione (PRE_PRD §11.7).
models:
  openai-gpt-5.1:
    input: 1.25
    output: 10.00
    reasoning: 10.00
  anthropic-sonnet-4.7:
    input: 3.00
    output: 15.00
    reasoning: 0.00
  deepseek-r1-2026:
    input: 0.55
    output: 2.19
    reasoning: 2.19
  qwen-3-flagship:
    input: 0.80
    output: 3.20
    reasoning: 0.00
```

I 4 modelli concreti del 2×2 vengono confermati nel `seed_experiment.py` script all'avvio dell'esperimento. Pricing letto da YAML e copiato su `models.pricing_*` rows al seed.

---

## 9. Test plan

### 9.1 Strategia generale

| Layer | Framework | DB | Esecuzione |
|-------|-----------|----|-----------:|
| Unit | pytest, pytest-asyncio | none (puro domain) | locale + CI |
| Integration | pytest, pytest-postgresql | Postgres ephemeral (container) | locale + CI |
| LLM | pytest-vcr (cassette HTTP) | none o Postgres ephemeral | CI (no $) |
| E2E | pytest, pytest-postgresql | Postgres ephemeral | locale + CI |

**Coverage target**: **80% globale** + **95% sui moduli core** (`domain/`, `llm/`, `execution/`). Configurato in `pyproject.toml` con `pytest --cov-fail-under=80`. Per i moduli core, soglia per-modulo in `.coveragerc`.

### 9.2 Unit tests (puro domain logic)

```
tests/unit/domain/
  test_schemas_trade_decision.py
    - validates 3 actions covering BTC/ETH/SOL
    - rejects extra=4 actions
    - rejects HOLD with size_pct > 0
    - rejects LONG without SL/TP (Figma F1)
    - rejects entry_type='limit' without limit_price
    - rejects key_signals not in controlled vocabulary
    - accepts confidence at boundary [0, 1]
    - rejects confidence > 1 or < 0
  test_enums.py
  test_pydantic_serialization.py
    - roundtrip JSON: TradeDecision → dict → TradeDecision

tests/unit/execution/
  test_guardrails.py
    - guardrail 1: HOLD forced if SL missing on LONG
    - guardrail 2: size_pct=0.50 clamped to AIAT_MAX_SIZE_PCT=0.20
    - guardrail 3: leverage=20 clamped to 1 + confidence*9
    - guardrail 4: confidence=0.3 → forced HOLD (AIAT_MIN_OPEN_CONFIDENCE=0.4)
    - 4 guardrail in ordine: SL → size → leverage → confidence
    - reports logs original_side when forced_hold=true
  test_sizing.py
    - Decimal precision: no float arithmetic anywhere
    - notional_value_usd = price * size_units * leverage

tests/unit/llm/
  test_structured_parser.py
    - extract_json_balanced: well-formed, nested, with prose surrounding
    - fallback_suffix invocato solo dopo primary failure
    - LLMUnrecoverableError quando entrambi i path falliscono
  test_stats_handler.py
    - OpenAI usage extraction
    - Anthropic usage extraction (reasoning=0)
    - DeepSeek/Qwen via OpenAI-compatible
    - cost_usd calculation con Decimal precision
```

### 9.3 Integration tests (DB layer + repository)

```
tests/integration/
  test_db_repositories_decisions.py
    - persist_decision: transazione atomica decision + actions + cost + llm_invocation
    - rollback se action[1] fails validation
    - get_action_history filtra correttamente per model_id e symbol
    - test CHECK constraints (HOLD con size_pct>0 → IntegrityError)
    - test composite FK (runs.context_snapshot_id mismatch tick_id → IntegrityError)

  test_db_repositories_positions.py
    - open_position crea positions + orders + fee_events in transazione
    - close_position aggiorna position + crea outcomes con FK opening_run_id/closing_run_id
    - opening_action_id UNIQUE: 2 positions stessa action → IntegrityError

  test_db_repositories_snapshots.py
    - persist_account_snapshot con portfolio_state_hash
    - get_context_snapshot ritorna None se non esiste

  test_db_migrations.py
    - alembic upgrade head from empty
    - tutti i CHECK constraint applicati
    - tutti gli indici creati
    - downgrade base + upgrade head è idempotente
```

Fixture `pytest_postgresql`:

```python
# tests/conftest.py
import pytest
from pytest_postgresql import factories
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from alembic.config import Config
from alembic import command

postgresql_proc = factories.postgresql_proc(port=None)
postgresql = factories.postgresql("postgresql_proc")

@pytest.fixture(scope="session")
async def db_url(postgresql):
    """Avvia Postgres ephemeral, applica migrations Alembic, yield URL."""
    url = f"postgresql+asyncpg://{postgresql.info.user}@{postgresql.info.host}:{postgresql.info.port}/{postgresql.info.dbname}"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    yield url

@pytest.fixture
async def db_session(db_url):
    engine = create_async_engine(db_url)
    async with AsyncSession(engine) as session:
        yield session
        await session.rollback()  # cleanup
```

### 9.4 LLM provider tests (VCR cassette)

```
tests/integration/test_llm_providers.py
  - test_openai_invoke_structured (cassette: openai_structured_btc_long.yaml)
  - test_anthropic_invoke_structured (cassette: anthropic_structured_hold.yaml)
  - test_deepseek_invoke_structured_via_compatible (cassette: deepseek_structured.yaml)
  - test_qwen_invoke_structured_via_compatible (cassette: qwen_structured.yaml)
  - test_openai_fallback_freetext (cassette con response malformata + retry)
  - test_llm_unrecoverable_error (cassette con due response invalide)
  - test_cost_tracking_openai (verifica cost_usd da usage)
  - test_cost_tracking_anthropic
  - test_timeout_handling (cassette con delay simulato > 90s → LLMTimeoutError)
  - test_rate_limit_propagation (cassette 429 → LLMRateLimitError, NO fallback)
  - test_auth_error_propagation (cassette 401 → LLMAuthError, NO fallback)
  - test_cost_aggregation_primary_plus_fallback
      (cassette primary malformato + fallback valido → CostEventData.n_attempts=2)

  # Reasoning trace coverage (fix B.10 review-r2):
  # Cassette dedicate per modelli con reasoning/thinking esposto, indispensabili
  # per validare cost_event.reasoning_tokens > 0 e relativo pricing.
  - test_openai_reasoning_tokens
      (cassette: openai_reasoning_tokens.yaml; valida completion_tokens_details.
       reasoning_tokens > 0 per modelli o1-style/gpt-5.x con thinking)
  - test_anthropic_thinking_usage
      (cassette: anthropic_thinking_usage.yaml; valida thinking_tokens contati
       correttamente per modelli sonnet thinking mode)
  - test_deepseek_r1_reasoning_usage
      (cassette: deepseek_r1_reasoning_usage.yaml; valida R1 reasoning_tokens
       da completion_tokens_details)
```

Configurazione VCR (gitignore le API key):

```python
# tests/conftest.py
import vcr

aiat_vcr = vcr.VCR(
    cassette_library_dir="tests/cassettes",
    record_mode="none",  # CI: solo replay. Locale per record: "once".
    filter_headers=["authorization", "x-api-key"],
    filter_post_data_parameters=["api_key"],
    match_on=["method", "scheme", "host", "path", "query", "body"],
)
```

### 9.5 E2E tests (full decision loop + isolation + parity)

```
tests/e2e/
  test_decision_loop_smoke.py
    - lancia decision_loop.run_once() con: LLM mockato (cassette), HL mockato,
      DB Postgres ephemeral
    - verifica: runs.status='success', 1 decision, 3 decision_actions,
      1 cost_event, 1 llm_invocation, account_snapshot con portfolio_state_hash
    - se action[BTC].side='LONG': verifica 3 orders (entry + SL + TP) creati

  test_isolation.py (invariante #1)
    - seed 2 model_id in DB con decisioni/posizioni dummy
    - lancia agent con AIAT_MODEL_ID=model_1
    - verifica che nessuna query effettuata legga rows con model_id='model_2'
    - **Doppia strategia di verifica (fix B.11 review-r2)**:
      1. *Spy applicativo (primario, robusto)*: subclass dei repository con
         RepositorySpy che intercetta ogni metodo. Ogni row ritornata viene
         verificata: se `row.model_id != settings.model_id`, il test fallisce
         con `LeakDetected`. Non dipende da formato log o livello logging.
      2. *DB-level trap (secondario, additional safety)*: trigger Postgres su
         tabelle critiche che alza eccezione se vede `SELECT ... WHERE model_id`
         con valore diverso da quello configurato per la sessione (via
         `SET LOCAL aiat.expected_model_id`). Robusto a query parametrizzate.
      3. *Log parsing*: mantenuto come check terziario solo per debug, NON
         come gating del test.

  test_context_parity.py (invariante #13)
    - lancia context-orchestrator → 1 context_snapshot
    - lancia 4 agent in parallelo con stesso tick_id
    - verifica che tutti e 4 runs.context_snapshot_id sia identico
    - verifica context_hash byte-identico sui 4 prompt rendered (solo parte
      market context; il portfolio_state_hash diverge correttamente)

  test_guardrail_e2e.py
    - LLM mockato che propone size_pct=0.99, leverage=30, confidence=0.95
    - verifica decision_actions.size_pct_executed=0.20 (clamped)
    - verifica decision_actions.leverage_executed≤10 (hard cap)
    - flag size_pct_clamped=true, leverage_clamped=true
```

### 9.6 CI matrix (.github/workflows/ci.yml)

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
      - run: uv run ruff check src tests
      - run: uv run ruff format --check src tests
      - run: uv run mypy src
      # Coverage globale 80%
      - run: uv run pytest tests/unit -v --cov=src/aiat --cov-fail-under=80
      # Coverage core 95% (fix B.19 review-r2-v2): step esplicito per moduli critici.
      # Soglia separata gating: il blueprint dichiara 95% sui moduli che, se buggy,
      # invaliderebbero l'esperimento (domain schemas, LLM, execution).
      - run: |
          uv run pytest tests/unit/domain tests/unit/llm tests/unit/execution \
            --cov=src/aiat/domain --cov=src/aiat/llm --cov=src/aiat/execution \
            --cov-fail-under=95
      - run: uv run pytest tests/integration -v
      - run: uv run pytest tests/e2e -v
      - run: uv run import-linter
```

Configurazione `.coveragerc` complementare:

```ini
# .coveragerc
[run]
source = src/aiat
branch = True
omit =
    src/aiat/__main__.py
    src/aiat/observability/logging_config.py

[report]
exclude_lines =
    pragma: no cover
    raise NotImplementedError
    if TYPE_CHECKING:
    @abstractmethod

# Soglie per-modulo non sono native in coverage.py; il gating è applicato
# da step CI separati (vedi ci.yml sopra). Per report locale leggibile:
show_missing = True
precision = 2
```

### 9.7 Invariant coverage matrix (fix B.12 review-r2)

Mappa esplicita dei 15 invarianti §5 ai test che li verificano. Ogni invariante DEVE avere almeno un test gating in CI; il PR check fallisce se questa matrice ha celle vuote.

| Invariante § 5 | Test gating | Layer |
|----------------|-------------|-------|
| #1 isolation cross-model | `test_isolation.py` (repository spy + DB trap) | E2E |
| #2 determinismo configurazione | `test_run_logs_git_sha_and_hashes` | Integration |
| #3 schema scientifico end-to-end | `test_db_migrations.py` (verifica colonne `experiment_id`/`model_id`/`run_id` presenti su tabelle operative) | Integration |
| #4 cost ledger post-decision atomico | `test_db_repositories_decisions.py::test_persist_decision_atomic_rollback` | Integration |
| #5 memoria 2 off default | `test_startup_memory_off_locked` (Settings field default = False, override RuntimeError se config tenta True per esperimento) | Unit |
| #6 vocabolario controllato | `test_schemas_trade_decision.py::test_rejects_unknown_signal` | Unit |
| #7 confidence sempre presente action-level | `test_schemas_trade_decision.py::test_confidence_required_even_for_hold` | Unit |
| #8 4 guardrail sempre attivi | `test_guardrails_cannot_disable` (verifica startup checks rifiutano config che disattiva) | Integration |
| #9 no mainnet | `test_startup_rejects_mainnet` | Integration |
| #10 no print() runtime | ruff rule T201 enabled in CI | Lint |
| #11 no raw SQL ad-hoc runtime | grep AST check in CI per `.execute(text(...))` fuori da `repositories/` e `scripts/` | Lint |
| #12 Decimal per soldi | `test_no_float_in_money_fields` (AST check su `domain/schemas.py`) | Unit |
| #13 parità market context cross-model | `test_context_parity.py` | E2E |
| #14 no cicli moduli | `import-linter` config in CI | Lint |
| #15 tick coverage tracking | `test_tick_coverage_kpi` (query SQL su runs per verificare ogni scheduled tick ha 4 rows) | E2E |

Il file `tests/invariant_coverage.py` aggrega questi test come marker `@pytest.mark.invariant("N")` per generare report:

```
$ uv run pytest -m "invariant" --invariant-report=report.md
```

---

## 10. Validation runtime

Validazioni eseguite all'avvio del servizio (PRIMA che APScheduler parta), per fallire fast in caso di config invalide.

### 10.1 Startup checks (in `orchestration/lifecycle.py`)

Esegue controlli di sanità all'avvio. Lancia `RuntimeError` fatale su qualsiasi failure → il servizio non parte. Lo stesso modulo gestisce **due profili di check** discriminati su `service_role` (fix B.14 review-r2).

```python
async def startup_checks(settings: AgentSettings | ContextOrchestratorSettings) -> None:
    """Dispatcher: applica check comuni + check specifici per ruolo."""
    # Check comuni a entrambi i ruoli
    await _check_network_testnet(settings)
    await _check_db_connectivity_and_schema(settings)
    await _check_active_experiment(settings)

    # Check role-specific
    if isinstance(settings, AgentSettings):
        await _agent_startup_checks(settings)
    elif isinstance(settings, ContextOrchestratorSettings):
        await _orchestrator_startup_checks(settings)
    else:
        raise RuntimeError(f"Unknown service_role: {settings.service_role}")


async def _check_network_testnet(settings: BaseAIATSettings) -> None:
    """Invariante #9."""
    if settings.network != "testnet":
        raise RuntimeError(f"AIAT_NETWORK must be 'testnet', got '{settings.network}'")


async def _check_db_connectivity_and_schema(settings: BaseAIATSettings) -> None:
    """Verifica DB raggiungibile e versione schema attesa."""
    async with get_db_session(settings) as session:
        version = await session.scalar(text("SELECT version_num FROM alembic_version"))
        if version != EXPECTED_ALEMBIC_VERSION:
            raise RuntimeError(
                f"DB schema version mismatch: expected {EXPECTED_ALEMBIC_VERSION}, "
                f"got {version}. Run 'alembic upgrade head'."
            )


async def _check_active_experiment(settings: BaseAIATSettings) -> None:
    """Esperimento esiste e non è terminato."""
    async with get_db_session(settings) as session:
        experiment = await session.get(Experiment, settings.experiment_id)
        if experiment is None:
            raise RuntimeError(f"Experiment '{settings.experiment_id}' not found in DB")
        if experiment.ended_at is not None:
            raise RuntimeError(f"Experiment ended at {experiment.ended_at}, cannot start")
        if experiment.git_commit_sha != settings.git_commit_sha:
            # Warning, non fatale: permette restart del servizio dopo deploy patch.
            logger.warning(
                "git_commit_sha mismatch with experiment seed",
                experiment_sha=experiment.git_commit_sha,
                runtime_sha=settings.git_commit_sha,
            )


async def _agent_startup_checks(settings: AgentSettings) -> None:
    """Check specifici per servizio agent (4 servizi Railway)."""

    # [A1] Model anagrafica registrata
    async with get_db_session(settings) as session:
        model = await session.get(Model, settings.model_id)
        if model is None:
            raise RuntimeError(
                f"Model '{settings.model_id}' not registered in DB. "
                f"Run 'python scripts/seed_experiment.py' first."
            )

    # [A2] Provider coerente con quanto registrato per il model_id
    if model.provider != settings.llm_provider:
        raise RuntimeError(
            f"Provider mismatch: settings={settings.llm_provider}, "
            f"models.provider={model.provider}"
        )

    # [A3] Wallet address coerente con quello registrato per il modello (fix B.14)
    if model.wallet_address != settings.hl_wallet_address:
        raise RuntimeError(
            f"Wallet mismatch for model '{settings.model_id}': "
            f"models.wallet_address={model.wallet_address}, "
            f"AIAT_HL_WALLET_ADDRESS={settings.hl_wallet_address}"
        )

    # [A4] Pricing presente in YAML per questo model_id (fix B.14)
    pricing = load_pricing_for_model(settings.model_id)
    if pricing is None:
        raise RuntimeError(f"No pricing config for model '{settings.model_id}' in model_pricing.yaml")

    # [A5] Prompt template hash registrato
    async with get_db_session(settings) as session:
        template = await session.get(PromptTemplate, settings.prompt_template_hash)
        if template is None:
            raise RuntimeError(
                f"Prompt template '{settings.prompt_template_hash}' not registered. "
                f"Run 'python scripts/register_prompt_template.py' first."
            )

    # [A6] Hyperliquid testnet reachability + wallet funded
    hl = HyperliquidClient(
        private_key=settings.hl_wallet_private_key.get_secret_value(),
        wallet_address=settings.hl_wallet_address,
        network=settings.network,
    )
    state = await hl.fetch_portfolio_state()
    if state.equity_usd <= 0:
        raise RuntimeError(
            f"Wallet equity=0 for model '{settings.model_id}'. Fund testnet wallet first."
        )

    # [A7] LLM provider credentials valid (smoke call ~$0.001)
    llm = load_llm(settings)
    try:
        await asyncio.wait_for(
            llm._llm.ainvoke("Reply with exactly: pong"),
            timeout=15,
        )
    except (LLMTimeoutError, LLMAuthError, Exception) as e:
        raise RuntimeError(f"LLM provider credentials invalid or unreachable: {e!r}")

    # [A8] Guardrail config validity + invariante #8 (sempre attivi)
    if not (0 < settings.max_size_pct <= 1):
        raise RuntimeError("AIAT_MAX_SIZE_PCT must be in (0, 1]")
    if settings.hard_max_leverage < 1:
        raise RuntimeError("AIAT_HARD_MAX_LEVERAGE must be >= 1")
    if not (0 <= settings.min_open_confidence <= 1):
        raise RuntimeError("AIAT_MIN_OPEN_CONFIDENCE must be in [0, 1]")

    # [A9] inject_decision_history == False (invariante #5, fix B.14)
    if settings.inject_decision_history is not False:
        raise RuntimeError(
            "AIAT_INJECT_DECISION_HISTORY must be False for thesis run "
            "(Memoria 2 OFF, RESEARCH §3.2 + invariante #5)"
        )

    # [A10] Baseline configs registrate per l'esperimento (fix B.14 review-r2,
    # rafforzato in B.14-followup review-r2-v2: ora fatal, non più warning).
    # Pre-registrazione tecnica obbligatoria: senza baseline preregistrati al seed,
    # l'esperimento è scientificamente compromesso (impossibile dimostrare che i
    # parametri del naive momentum non siano stati ottimizzati a posteriori).
    EXPECTED_BASELINES = {"buy_and_hold", "cash", "naive_momentum_ema_20_50"}
    async with get_db_session(settings) as session:
        registered_baselines = set(await session.scalars(
            select(BaselineConfig.baseline_name).where(
                BaselineConfig.experiment_id == settings.experiment_id
            )
        ))
        missing = EXPECTED_BASELINES - registered_baselines
        if missing:
            raise RuntimeError(
                f"Missing baseline_configs for experiment '{settings.experiment_id}': "
                f"{sorted(missing)}. "
                f"Run 'python scripts/seed_experiment.py' to register baselines "
                f"BEFORE starting the experiment. Pre-registration is mandatory for "
                f"scientific validity (RESEARCH §3.3)."
            )


async def _orchestrator_startup_checks(settings: ContextOrchestratorSettings) -> None:
    """Check specifici per il 5° servizio context-orchestrator."""

    # [O1] NO credenziali LLM presenti (least privilege check, fix B.13/B.14)
    # Verifica difensiva: anche se Pydantic Settings esclude i campi LLM, una
    # env var leftover potrebbe essere stata definita. Errore esplicito.
    suspicious_envs = ["AIAT_OPENAI_API_KEY", "AIAT_ANTHROPIC_API_KEY",
                       "AIAT_DEEPSEEK_API_KEY", "AIAT_QWEN_API_KEY",
                       "AIAT_HL_WALLET_PRIVATE_KEY", "AIAT_MODEL_ID"]
    leaked = [v for v in suspicious_envs if os.environ.get(v)]
    if leaked:
        raise RuntimeError(
            f"context-orchestrator service has unexpected env vars set: {leaked}. "
            f"Least privilege violation. Remove these from Railway config."
        )

    # [O2] HL info endpoint raggiungibile (read-only, no wallet credentials)
    from aiat.context.collectors.onchain import HLPublicInfoClient
    hl_info = HLPublicInfoClient(network=settings.network)
    try:
        meta = await asyncio.wait_for(hl_info.fetch_meta(), timeout=10)
        if not meta or "universe" not in meta:
            raise RuntimeError("HL info endpoint returned empty meta")
    except Exception as e:
        raise RuntimeError(f"HL info endpoint unreachable: {e!r}")

    # [O3] RSS sources raggiungibili (almeno una)
    from aiat.context.collectors.news import NewsCollector
    news = NewsCollector(timeout_seconds=10)
    reachable = await news.check_sources_reachability()
    if reachable == 0:
        raise RuntimeError("No RSS news source reachable")

    # [O4] Fear&Greed API raggiungibile
    from aiat.context.collectors.sentiment import SentimentCollector
    sent = SentimentCollector(timeout_seconds=5)
    try:
        await sent.collect()
    except Exception as e:
        raise RuntimeError(f"Fear&Greed API unreachable: {e!r}")
```

### 10.2 Runtime validation (per ogni decision)

Validazioni applicate DOPO `LLMClient.invoke()` ma PRIMA della persistenza:

1. **Pydantic validation** (automatica): se `TradeDecision.model_validate()` fallisce → fallback freetext, e se fallisce di nuovo → `LLMUnrecoverableError` → `runs.status='failed'` con `failure_stage='llm_parse'`.
2. **Vocabolario controllato `key_signals`** (in `Literal[...]` type): rigetto automatico se il modello cita signal non in lista.
3. **Vincoli condizionali HOLD/FLAT vs LONG/SHORT** (model_validator in `ActionDecision`): rigetto automatico.
4. **Guardrails post-clamping** (sempre attivi, invariante #8): produzione `GuardrailReport` per ogni action.
5. **Decimal precision** ovunque (invariante #12).

### 10.3 Pydantic Settings (separate per ruolo, least privilege)

**Fix B.13 review-r2**: il context-orchestrator (5° Railway service) NON deve avere credenziali LLM né chiave privata del wallet HL. Sono segreti inutili per quel ruolo e violano il principio di *least privilege*. Settings unica per agent + orchestrator esporrebbe l'attacco superficiale (compromesso del context-orchestrator → accesso a wallet privati). Soluzione: tre Settings, discriminate da `service_role`.

```python
# src/aiat/config/settings.py
from decimal import Decimal
from typing import Literal
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseAIATSettings(BaseSettings):
    """Campi comuni a tutti i ruoli (agent + context-orchestrator)."""
    model_config = SettingsConfigDict(
        env_prefix="AIAT_",
        env_file=".env",
        case_sensitive=False,
        extra="forbid",
    )

    # Identity dell'esperimento (comune)
    experiment_id: str
    git_commit_sha: str  # iniettato a build-time da CI

    # Database (entrambi i ruoli accedono al DB condiviso)
    database_url: SecretStr

    # Network locked (invariante #9, comune)
    network: Literal["testnet"] = "testnet"

    # Observability
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Service role discriminator
    service_role: Literal["agent", "context_orchestrator"]


class AgentSettings(BaseAIATSettings):
    """
    Settings per i 4 servizi agent (uno per modello LLM).
    Possiede credenziali LLM + wallet HL specifici del modello assegnato.
    """
    service_role: Literal["agent"] = "agent"

    # Model identity (uno dei 4)
    model_id: str
    prompt_template_hash: str
    schema_version: Literal["v1"] = "v1"

    # LLM provider
    llm_provider: Literal["openai", "anthropic", "deepseek", "qwen"]
    model_name_api: str
    temperature: Decimal | None = None
    top_p: Decimal | None = None
    max_tokens: int | None = None
    seed: int | None = None

    # LLM API keys: solo UNA è attesa (corrispondente a llm_provider)
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    qwen_api_key: SecretStr | None = None

    # Hyperliquid wallet (uno per modello)
    hl_wallet_private_key: SecretStr
    hl_wallet_address: str

    # Guardrails (Strategia C+, PRE_PRD §13.3 — sempre attivi, invariante #8)
    max_size_pct: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    hard_max_leverage: Decimal = Field(default=Decimal("10"), ge=1)
    min_open_confidence: Decimal = Field(default=Decimal("0.4"), ge=0, le=1)

    # Context (invariante #5)
    inject_decision_history: bool = False  # Memoria 2: OFF per tesi

    # Scheduling
    agent_start_delay_seconds: int = 30
    hard_timeout_seconds: int = 180

    @model_validator(mode="after")
    def validate_api_key_matches_provider(self) -> "AgentSettings":
        """Garantisce che la API key fornita corrisponda al provider scelto."""
        mapping = {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "deepseek": self.deepseek_api_key,
            "qwen": self.qwen_api_key,
        }
        if mapping[self.llm_provider] is None:
            raise ValueError(
                f"llm_provider='{self.llm_provider}' requires "
                f"AIAT_{self.llm_provider.upper()}_API_KEY"
            )
        return self


class ContextOrchestratorSettings(BaseAIATSettings):
    """
    Settings per il 5° servizio (context-orchestrator).
    NON possiede credenziali LLM né wallet privato: least privilege.
    Accede solo a sorgenti pubbliche/free (HL info endpoint, RSS, F&G).
    """
    service_role: Literal["context_orchestrator"] = "context_orchestrator"

    # Cron offsets (HH:00/15/30/45)
    cron_minute_offsets: list[int] = [0, 15, 30, 45]
    hard_timeout_seconds: int = 30  # più stretto: deve completare prima dei 4 agent

    # Eventuali API key di sorgenti esterne (es. premium news feed)
    # NESSUNA chiave LLM. NESSUNA chiave wallet.
    newsfeed_api_key: SecretStr | None = None  # opzionale, free tier per default


def load_settings() -> AgentSettings | ContextOrchestratorSettings:
    """Dispatcher: legge AIAT_SERVICE_ROLE e ritorna la subclass corretta."""
    import os
    role = os.environ.get("AIAT_SERVICE_ROLE")
    if role == "agent":
        return AgentSettings()  # type: ignore[call-arg]
    elif role == "context_orchestrator":
        return ContextOrchestratorSettings()  # type: ignore[call-arg]
    else:
        raise RuntimeError(
            f"AIAT_SERVICE_ROLE must be 'agent' or 'context_orchestrator', got '{role}'"
        )
```

**Implicazioni operative**:
- I 4 servizi agent Railway hanno env vars `AIAT_SERVICE_ROLE=agent` + tutte le credenziali del proprio modello + il proprio wallet HL.
- Il 5° servizio context-orchestrator ha `AIAT_SERVICE_ROLE=context_orchestrator` + DB url + eventuale newsfeed key.
- Se il context-orchestrator viene compromesso, l'attaccante NON ha accesso ai wallet di trading né alle API key LLM.
- Lo `startup_checks` (vedi §10.1) usa runtime type checking (`isinstance(settings, AgentSettings)`) per applicare check diversi ai due ruoli.

---

---

## 11. Deploy strategy

### 11.1 Topologia operativa

Il progetto deploya **5 servizi Railway** + **1 database Postgres condiviso** da **un'unica repo git**:

| Servizio Railway | Ruolo | env: `AIAT_SERVICE_ROLE` | env: `AIAT_MODEL_ID` | Wallet HL |
|------------------|-------|-------------------------|---------------------|-----------|
| `context-orchestrator` | Materializza context_snapshot per tick | `context_orchestrator` | — | — |
| `agent-openai` | Decision loop per OpenAI | `agent` | `openai-<model>` | wallet_1 |
| `agent-anthropic` | Decision loop per Anthropic | `agent` | `anthropic-<model>` | wallet_2 |
| `agent-deepseek` | Decision loop per DeepSeek | `agent` | `deepseek-<model>` | wallet_3 |
| `agent-qwen` | Decision loop per Qwen | `agent` | `qwen-<model>` | wallet_4 |

**Postgres**: 1 database condiviso, schema unico (vedi §3). Scrittura distinta per `model_id` (invariante #1). Read-only su `context_snapshots` da parte degli agent; write-only su `context_snapshots` + `context_build_runs` da parte del context-orchestrator.

### 11.2 Strategia monorepo + dispatcher

**Decisione**: monorepo singolo. Tutti e 5 i servizi puntano alla stessa repo GitHub (`aithos-rr/AI-Agent-for-Trading`); il ruolo è discriminato da `AIAT_SERVICE_ROLE` env var.

Razionale (vedi conversazione di design):
- ~80% del codice è condiviso (domain, db, llm clients, observability, collectors)
- 2 repo significherebbe duplicare Pydantic schemas, DB models, collectors → drift inevitabile
- Railway supporta multi-service deploy da una stessa repo
- La separazione least-privilege è a livello `Settings` runtime (vedi §10.3), NON a livello repo
- Pattern moderno: monorepo con multiple deploy targets

#### Entrypoint dispatcher

```python
# src/aiat/__main__.py
import asyncio
import structlog
from aiat.config.settings import load_settings, AgentSettings, ContextOrchestratorSettings
from aiat.orchestration.lifecycle import startup_checks
from aiat.orchestration.scheduler import build_scheduler_for_agent, build_scheduler_for_orchestrator
from aiat.observability.logging_config import configure_logging

async def main() -> None:
    settings = load_settings()  # dispatch su AIAT_SERVICE_ROLE
    configure_logging(settings.log_level)
    logger = structlog.get_logger()

    logger.info("startup", service_role=settings.service_role)

    # Startup checks role-specific (§10.1)
    await startup_checks(settings)

    # Build scheduler per ruolo
    if isinstance(settings, AgentSettings):
        scheduler = await build_scheduler_for_agent(settings)
    elif isinstance(settings, ContextOrchestratorSettings):
        scheduler = await build_scheduler_for_orchestrator(settings)
    else:
        raise RuntimeError(f"Unknown service_role: {settings.service_role}")

    scheduler.start()
    logger.info("scheduler started, awaiting cron")
    # Mantieni l'evento loop attivo
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
```

Comando di avvio (lo stesso per tutti e 5 i servizi):
```bash
python -m aiat
```

### 11.3 Dockerfile multi-stage (non-root)

Pattern adottato da TradingAgents (vedi ANALYSIS §6), `Dockerfile` unico per tutti i servizi:

```dockerfile
# docker/Dockerfile
# Stage 1: build deps con uv
FROM python:3.12-slim AS builder
WORKDIR /build
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Stage 2: runtime minimal
FROM python:3.12-slim AS runtime
RUN useradd -u 10001 -m -s /bin/bash aiat
WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
COPY --chown=aiat:aiat src /app/src
COPY --chown=aiat:aiat alembic /app/alembic
COPY --chown=aiat:aiat alembic.ini /app/alembic.ini
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1
USER aiat
CMD ["python", "-m", "aiat"]
```

**Note**:
- Multi-stage: builder image scartata, runtime contiene solo deps installate, no toolchain
- Non-root: utente `aiat` (uid 10001) per security
- `PYTHONUNBUFFERED=1`: log strutturati JSON via structlog scrivono direttamente su stdout senza buffering (Railway raccoglie e mostra in dashboard)
- Stesso immagine per tutti i 5 servizi; cambia solo env var

### 11.4 Variabili ambiente per servizio

Configurazione su Railway dashboard per ogni servizio. Tutte le variabili usano prefix `AIAT_` (vedi `BaseAIATSettings`).

#### Comuni a tutti i 5 servizi

```
AIAT_EXPERIMENT_ID=<UUID>
AIAT_GIT_COMMIT_SHA=<sha7-injected-at-build>
AIAT_DATABASE_URL=postgresql+asyncpg://...railway.app:5432/...
AIAT_NETWORK=testnet
AIAT_LOG_LEVEL=INFO
```

#### Solo `context-orchestrator`

```
AIAT_SERVICE_ROLE=context_orchestrator
AIAT_HARD_TIMEOUT_SECONDS=30
# Opzionale: API key per news premium (free tier altrimenti)
AIAT_NEWSFEED_API_KEY=<secret-or-empty>
```

**Verifica least-privilege** (gli startup checks O1 falliscono se queste env sono presenti):
- ❌ `AIAT_MODEL_ID` (deve essere assente)
- ❌ `AIAT_LLM_PROVIDER` (assente)
- ❌ `AIAT_OPENAI_API_KEY` / `AIAT_ANTHROPIC_API_KEY` / `AIAT_DEEPSEEK_API_KEY` / `AIAT_QWEN_API_KEY` (assenti)
- ❌ `AIAT_HL_WALLET_PRIVATE_KEY` (assente)

#### Solo `agent-<provider>` (esempio agent-openai)

```
AIAT_SERVICE_ROLE=agent
AIAT_MODEL_ID=openai-<model-name>
AIAT_PROMPT_TEMPLATE_HASH=<sha256-from-prompt_templates-table>
AIAT_SCHEMA_VERSION=v1

AIAT_LLM_PROVIDER=openai
AIAT_MODEL_NAME_API=<exact-api-string>
AIAT_TEMPERATURE=0
AIAT_SEED=42
AIAT_MAX_TOKENS=4096

AIAT_OPENAI_API_KEY=<secret>

AIAT_HL_WALLET_PRIVATE_KEY=<secret>
AIAT_HL_WALLET_ADDRESS=0x...

AIAT_MAX_SIZE_PCT=0.20
AIAT_HARD_MAX_LEVERAGE=10
AIAT_MIN_OPEN_CONFIDENCE=0.4
AIAT_INJECT_DECISION_HISTORY=false
AIAT_AGENT_START_DELAY_SECONDS=30
AIAT_HARD_TIMEOUT_SECONDS=180
```

Per `agent-anthropic`, `agent-deepseek`, `agent-qwen`: identico ma con `AIAT_LLM_PROVIDER` e relativa API key + wallet diverso.

#### Secret management Railway

- Tutte le variabili che terminano in `_KEY` o contengono `PRIVATE_KEY` sono configurate come **Sealed Variables** su Railway (criptate at-rest, non visibili nel dashboard dopo l'inserimento)
- API keys MAI committed in git (vedi `.gitignore`)
- `.env.example` nel repo contiene i nomi delle env vars con valori placeholder, mai i valori reali

### 11.5 Health checks

Ogni servizio espone un health endpoint via `httpx` server minimale separato dall'event loop principale (porta `$PORT` su Railway, di default 8080):

```python
# src/aiat/observability/health.py
# Servito da uvicorn in lifecycle.py durante run()
# GET /health → 200 OK se:
#   - DB raggiungibile (SELECT 1 entro 2s)
#   - APScheduler running (.state == STATE_RUNNING)
#   - Per AGENT: ultima run nelle ultime 20 min ha status ∈ {success, partial}
#                (più di 2 run consecutive in failed/timeout → unhealthy)
#   - Per ORCHESTRATOR: ultima context_build_runs nelle ultime 20 min ha status ∈ {success, partial}
```

Railway esegue automaticamente health check su `$PORT/health` ogni 30s. Su 3 failure consecutive il servizio viene riavviato.

### 11.6 Observability shipping

- **Logs**: structlog JSON → stdout → Railway dashboard (built-in). Per drill-down: Railway permette export logs.
- **Metriche**: nessuna soluzione esterna (Datadog, Grafana) per scope tesi. Le metriche operative sono ricavate ex-post via query SQL su DB (es. `runs.status` aggregato, `cost_events.cost_usd` cumulato per modello, `outcomes` per analisi).
- **Alerting**: nessuno automatico. Monitoraggio manuale durante l'esperimento via dashboard Railway + query SQL pianificate.

### 11.7 Procedura di bootstrap esperimento

Sequenza operativa per avviare l'esperimento dopo che il codice è pronto e i 5 servizi Railway sono configurati ma non avviati.

```
[step 1] Postgres ready
  └─ Crea Postgres su Railway, copia DATABASE_URL
  └─ Imposta AIAT_DATABASE_URL in tutti i 5 servizi

[step 2] Migrations schema
  $ AIAT_DATABASE_URL=... uv run alembic upgrade head
  └─ Verifica: SELECT version_num FROM alembic_version = EXPECTED_ALEMBIC_VERSION

[step 3] Hyperliquid testnet wallets
  └─ Crea 4 wallet HL testnet distinti
  └─ Funda ciascuno con 1000$ testnet via faucet
  └─ Salva (private_key, address) per ognuno
  └─ Imposta env AIAT_HL_WALLET_* sui 4 servizi agent

[step 4] Seed experiment
  $ uv run python scripts/seed_experiment.py \
      --name "thesis-v2-comparative-2026Q2" \
      --git-sha $(git rev-parse HEAD) \
      --config configs/experiment_baseline.yaml
  └─ Inserisce: experiment row + 4 model rows (con wallet_address!) + 3 baseline_configs

[step 5] Register prompt template
  $ uv run python scripts/register_prompt_template.py \
      --template src/aiat/prompts/templates/v1_baseline.md \
      --label v1_baseline
  └─ Calcola sha256 + insert prompt_templates row
  └─ Copia il SHA su env AIAT_PROMPT_TEMPLATE_HASH dei 4 servizi agent

[step 6] Verifica wallets
  $ uv run python scripts/verify_wallets.py
  └─ Per ogni wallet: connect HL testnet, verifica equity > 0, verifica account_state

[step 7] Smoke deploy context-orchestrator
  └─ Deploy il solo context-orchestrator su Railway
  └─ Verifica startup_checks OK (logs)
  └─ Aspetta 1 tick (15 min), verifica 1 row in context_snapshots
  └─ Stop il servizio

[step 8] Smoke deploy agent (uno solo, es. agent-openai)
  └─ Riavvia context-orchestrator
  └─ Deploy agent-openai
  └─ Verifica startup_checks OK
  └─ Aspetta 1 tick (15 min)
  └─ Verifica: 1 run row, 1 decision, 3 decision_actions, eventualmente order/positions
  └─ Stop entrambi i servizi

[step 9] Full deploy (5 servizi simultaneamente)
  └─ Deploy tutti i 5 servizi
  └─ Verifica tutti gli startup_checks OK
  └─ Aspetta 4 tick (1 ora)
  └─ Verifica: per ogni tick, 1 context_snapshot + 4 runs + 4 decisions + 12 decision_actions

[step 10] Mark esperimento attivo
  └─ Aggiorna experiments.started_at = now() (manuale via SQL o script)
  └─ L'esperimento è ufficialmente in corso. Da qui +4 settimane = ended_at.
```

---

## 12. Milestones (sequenza implementativa)

Sequenza ordinata di milestone con criteri di completamento espliciti (`Definition of Done`). **Non vengono assegnate date**: ogni milestone è considerata completa solo quando soddisfa il DoD; lo studente avanza secondo la propria disponibilità.

### M0 — Setup repo + CI baseline

**Definition of Done**:
- `pyproject.toml` configurato con `uv` (Python 3.12+, dipendenze §1.2)
- `uv.lock` committed
- Dockerfile multi-stage funzionante (`docker build .` → image successful)
- `ruff` + `mypy strict` + `pytest` configurati e passanti su scheletro vuoto
- `import-linter` config con almeno 1 rule (no cicli tra `domain/` e altri moduli)
- `.github/workflows/ci.yml` esegue lint + type-check + pytest in CI
- `.env.example` con tutti i nomi env var di §11.4

**Verifica**: pushare un PR su main triggera CI green senza skip.

---

### M1 — Domain + DB schema + migrations

**Definition of Done**:
- `src/aiat/domain/enums.py` + `schemas.py` completi (§6.1, §6.2, §6.3, §6.4)
- `src/aiat/domain/exceptions.py` con gerarchia base
- `src/aiat/db/models/` con tutti i 17 SQLAlchemy models (§3.2)
- Alembic migration `001_initial_schema.py` generata + applicata su Postgres test
- Test unit `tests/unit/domain/test_schemas_*.py` (Pydantic validator coverage 95%)
- Test integration `tests/integration/test_db_migrations.py` (upgrade head + verifica CHECK constraints)
- Test unit `tests/unit/domain/test_pydantic_serialization.py` (roundtrip JSON)

**Verifica**: tutti i test passano; `alembic upgrade head` su Postgres pulito crea 17 tabelle con tutti i constraint dichiarati in §3.2.

---

### M2 — LLM abstraction + StatsHandler

**Definition of Done**:
- `src/aiat/llm/base.py` ABC completo (§7.3)
- `src/aiat/llm/exceptions.py` con 5 classi (§8.2)
- `src/aiat/llm/structured.py` con `invoke_structured` + `_extract_json_balanced` (§8.2)
- `src/aiat/llm/stats_handler.py` con `StatsCallbackHandler` (§8.3) inclusa aggregazione `n_attempts`
- `openai_client.py`, `anthropic_client.py`, `openai_compatible_client.py` (Deepseek + Qwen)
- `factory.py` con `load_llm(settings: AgentSettings)`
- `model_pricing.yaml` con i 4 modelli scelti
- Test unit: `test_structured_parser.py`, `test_stats_handler.py` (>95% coverage)
- Test integration con cassette VCR: 4 provider × scenario {structured success, fallback, unrecoverable, timeout, rate-limit, auth, reasoning-trace}

**Verifica**: una chiamata smoke a ciascuno dei 4 provider produce `LLMInvocationResult` valido con `CostEventData` corretto; CI esegue cassette VCR senza chiamate API reali.

---

### M3 — ContextOrchestrator + collectors

**Definition of Done**:
- `src/aiat/context/collectors/` completo: `technical.py`, `sentiment.py`, `news.py`, `onchain.py`
- Ogni collector implementa `BaseCollector` con timeout esplicito e (dove applicabile) cache TTL
- `src/aiat/context/controlled_signals.py` con lista controllata (§3.3 v2)
- `src/aiat/orchestration/context_orchestrator.py` entrypoint del 5° servizio
- `ContextBuildRepository` in `src/aiat/db/repositories/context_build.py`
- Test unit per ogni collector (con httpx mock)
- Test integration su Postgres ephemeral: orchestrator scrive `context_snapshots` + `context_build_runs` correttamente, anche su fallimenti

**Verifica**: `python -m aiat` con `AIAT_SERVICE_ROLE=context_orchestrator` su DB pulito + Postgres ephemeral genera 4 `context_snapshots` in 1 ora (1 per tick).

---

### M4 — ExecutionLayer + guardrails

**Definition of Done**:
- `src/aiat/execution/hyperliquid_client.py` con interfaccia §7.5 (include `current_position` param)
- `src/aiat/execution/guardrails.py` con i 4 guardrail Strategia C+ (§7.4)
- `src/aiat/execution/sizing.py` (Decimal precision)
- `src/aiat/execution/outcome_resolver.py`
- `PositionsRepository` in `src/aiat/db/repositories/positions.py` (con `orders` + `fee_events`)
- Test unit guardrails (4 scenari × edge cases)
- Test integration con Postgres ephemeral: `open_position` → `close_position` → `outcomes` row corretto
- Test e2e con HL testnet reale (1 wallet smoke): submit market order, verify fill, verify SL/TP triggers

**Verifica**: smoke su wallet testnet di sviluppo apre LONG BTC con SL/TP, lo chiude manualmente, verifica `outcomes.pnl_net_fee_funding_usd` corretto.

---

### M5 — Decision loop end-to-end + test isolation/parity

**Definition of Done**:
- `src/aiat/orchestration/decision_loop.py` completo (§4.1 timeline)
- `src/aiat/orchestration/scheduler.py` con APScheduler config (§4.1)
- `src/aiat/orchestration/lifecycle.py` con `startup_checks` role-specific (§10.1)
- `DecisionsRepository` completo con transazione atomica (§7.6 invariante #4)
- Tutti i 4 repository transactional pattern verificati
- `tests/e2e/test_decision_loop_smoke.py` passa (LLM mockato + HL mockato + Postgres ephemeral)
- `tests/e2e/test_isolation.py` passa (invariante #1) con RepositorySpy + DB trap
- `tests/e2e/test_context_parity.py` passa (invariante #13)
- `tests/e2e/test_guardrail_e2e.py` passa
- Invariant coverage matrix (§9.7) verde: tutti i 15 invarianti hanno un test gating

**Verifica**: CI green su tutti gli e2e. Smoke test locale: 1 context-orchestrator + 4 agent fittizi (LLM mockato) su Postgres locale girano per 4 tick consecutivi, generano dataset coerente.

---

### M6 — Smoke test multi-day testnet

**Definition of Done**:
- I 5 servizi Railway deployati (vedi §11.7)
- Tutti gli startup checks OK
- 48 ore di run continuativo su testnet senza intervento manuale
- Almeno 192 tick completati (4 settimane × 4 tick/h × 2 giorni = 192)
- Per ogni tick: 1 context_snapshot + 4 runs (status='success' o 'partial' tollerato)
- Almeno 4 trade chiusi (entry + exit) verificati con outcomes row corrette
- Nessuna `errors` row di severity HIGH (timeout/auth/unrecoverable)
- Dashboard manuale: query SQL aggregate funzionanti per ogni metrica §3.3

**Verifica**: dopo 48h, eseguire `scripts/export_dataset.py` produce CSV/Parquet completi senza errori; query `SELECT count(*) FROM runs WHERE status='success'` ≥ 90% degli scheduled tick.

---

### M7 — Esperimento di 4 settimane

**Definition of Done**:
- M6 completata con KPI verdi
- `experiments.started_at` aggiornato a now() ufficiale
- 4 settimane di run continuativo (28 giorni × 4 tick/h × 24h = 2688 scheduled tick per modello)
- Tick coverage finale ≥ 95% (invariante #15)
- `scripts/compute_baselines.py` eseguito a fine esperimento: 3 baseline (B&H, Cash, Naive Momentum) producono `baseline_equity_snapshots` per ogni tick
- `scripts/compute_tax_sim.py` eseguito: `tax_sim_periods` per ogni `(model_id, quarter)`
- `scripts/export_dataset.py` produce CSV/Parquet finale per analisi tesi
- `experiments.ended_at` aggiornato

**Verifica**: dataset finale validato (count outcomes per modello, distribution confidence, k-fold isolation check via repository spy applicato retroattivamente sui dati raccolti).

---

### Note sulla sequenza

- **M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7**: dipendenze stretta. Non si può iniziare Mn senza completare Mn-1.
- **Parallelismo possibile**: M2 (LLM) e M3 (Context) possono essere sviluppate in parallelo dopo M1 — sono moduli indipendenti.
- **M6 è obbligatoria**: nessuno avvia M7 senza 48h di smoke test verde. Tentazione di "saltare" il smoke per fretta è il rischio più alto della Phase 5.
- **Nessuna data nominale**: lo studente avanza secondo università + lavoro paralleli. Il vincolo scientifico è "M7 deve durare ≥ 4 settimane consecutive con tick coverage ≥ 95%".

---

## 13. Risk register

Tabella sistematica dei rischi che possono compromettere il progetto. Severity: **Probabilità × Impatto**.

### 13.1 Rischi tecnici

| ID | Rischio | Probabilità | Impatto | Mitigazione |
|----|---------|-------------|---------|-------------|
| T1 | Hyperliquid testnet downtime durante esperimento | Media | Alto | Tick `missed` non interpolato (regola unica §2.1). KPI coverage ≥ 95%. Se downtime > 24h cumulativo, estendere esperimento. |
| T2 | LLM provider rate limit / pricing change | Bassa-Media | Medio | Cost ledger pre-experiment (smoke 48h) stima costo reale. Budget API allocato con margine ×2. Se rate limit colpisce, run in `timeout`/`failed`, no fallback freetext (vedi §8.2). |
| T3 | Postgres Railway full / restart durante esperimento | Bassa | Alto | Backup giornaliero automatico Railway. Snapshot manuale settimanale. `runs` con composite UNIQUE su `(experiment_id, model_id, scheduled_for)` previene duplicati post-restart. |
| T4 | Drift di pricing API tra `model_pricing.yaml` e fatturazione reale | Media | Basso | `cost_event.pricing_snapshot` JSONB conserva pricing usato al momento. Riconciliazione manuale a fine esperimento se discrepanza. |
| T5 | Bug in `invoke_structured` che genera loop di fallback | Bassa | Alto | Hard timeout 90s/LLM + max 2 attempts (struttura by-design). Test unit copre LLMUnrecoverableError. Monitoring `cost_events.n_attempts > 2` come alert ex-post. |
| T6 | Slippage HL testnet vs mainnet sistematicamente diverso | Alta | Basso | Dichiarato come limitazione esplicita in `RESEARCH §7` (#1). Risultato comunque valido per studio comparativo. |
| T7 | Hyperliquid SDK breaking change durante esperimento | Bassa | Alto | `pyproject.toml` con version pinning stretto. `uv.lock` committed. No `uv sync` durante run sperimentale. |
| T8 | LLM hallucina `key_signals` non in vocabolario controllato | Alta | Basso | Pydantic `Literal[...]` type rigetta in validation, fallback freetext attivato. Se anche fallback fallisce, `LLMUnrecoverableError` → run in `failed`. Misurabile via `runs.status` aggregato. |

### 13.2 Rischi scientifici

| ID | Rischio | Probabilità | Impatto | Mitigazione |
|----|---------|-------------|---------|-------------|
| S1 | Periodo 4 settimane è bull/bear estremo, distorce risultati | Alta | Medio | Dichiarato come limitazione esplicita in RESEARCH §7 #9. Riportare regime di mercato del periodo nella discussione. |
| S2 | Tutti i 4 modelli HOLD spesso, dataset sbilanciato | Media | Alto | Aspettativa: 30-50% HOLD (RESEARCH §6.1). Se > 70%, ridurre `AIAT_MIN_OPEN_CONFIDENCE` per i tick rimanenti (ma documentare cambio). |
| S3 | 4 modelli funzionalmente intercambiabili (kappa > 0.7) | Bassa | Basso | È un risultato scientifico legittimo (RESEARCH §2.2 nota): conferma H0_RQ3. Non è un fallimento. |
| S4 | Hyperliquid liquidazione precoce di una posizione | Media | Basso | Guardrail Strategia C+ limita leva e size. Liquidations loggati come `close_reason='liquidated'`. Misurati come metrica comportamentale (RESEARCH §3.3). |
| S5 | DeepSeek/Qwen rifiutano richieste finanziarie per filtro safety | Media | Medio | Cassette VCR pre-experiment validano comportamento. Se rifiuto sistematico, escalare a `LLMUnrecoverableError` documentato come comportamento del modello (è un dato scientifico, non un bug). |
| S6 | Confidence sempre alta (over-confidence) → Brier score saturo | Alta | Basso | Aspettativa nota (RESEARCH §6.1). Brier score è il *risultato* della misurazione, non l'obiettivo. |
| S7 | Cosmetic prompt drift (template modificato durante esperimento) | Bassa | Catastrofico | `prompt_template_hash` immutabile in `runs`. Startup check rigetta hash non registrato. `git_commit_sha` loggato in ogni run. Restart richiede stesso template hash. |
| S8 | Dataset insufficiente per analisi statistica per cella simbolo×regime | Media | Medio | Limitazione dichiarata in RESEARCH §6.1. Analisi a livello (timestamp, model_id) resta sempre fattibile (10.752 osservazioni). |

### 13.3 Rischi operativi

| ID | Rischio | Probabilità | Impatto | Mitigazione |
|----|---------|-------------|---------|-------------|
| O1 | Costi API LLM superano budget studente | Media | Alto | Smoke 48h proietta costo 4 settimane × 4 modelli. Modelli `cheap_alt` (DeepSeek/Qwen) costano ~10× meno di premium. Budget allocato ex-ante. Hard cap automatico: `AIAT_HARD_TIMEOUT_SECONDS=180` limita anche cost (ridotto LLM input). |
| O2 | Railway plan limits raggiunti (CPU/RAM/network) | Bassa | Medio | 5 servizi su Hobby plan dovrebbero rientrare. Upgrade a Pro plan se necessario (~$20/mese). Monitoring manuale via Railway dashboard. |
| O3 | Wallet HL testnet drenato (4 wallet × 1000$ → liquidazione cascata) | Bassa | Alto | Guardrail size cap 20%/trade. Refund testnet faucet se necessario. Worst case: pause esperimento, refund, restart con tick `skipped` documentati. |
| O4 | Studente non disponibile per monitoring durante 4 settimane | Alta | Medio | Health check Railway auto-restart. Email alert su downtime via Railway integration (opzionale). Check manuale ogni 24h sufficiente. |
| O5 | Tesi scadenza vs implementazione che slitta | Alta | Catastrofico | Sequenza milestones senza date forza disciplina M-per-M (no "skip M6 per fretta"). Smoke test 48h come gating obbligatorio. Se M5 in ritardo > 2 settimane, valutare riduzione scope (es. 3 modelli invece di 4). |
| O6 | API key leak in git per errore | Bassa | Catastrofico | `.gitignore` include `.env`. Pre-commit hook `git-secrets` o `gitleaks`. `pre-commit` framework configurato in M0. |
| O7 | Database condiviso permette cross-model contamination accidentale | Bassa | Catastrofico | Invariante #1 + RepositorySpy test (§9.5). Tutti gli agent filtrano `WHERE model_id`. Inserts marcano `experiment_id`+`model_id` denormalizzati. |

### 13.4 Rischi metodologici (peer-review tesi)

| ID | Rischio | Probabilità | Impatto | Mitigazione |
|----|---------|-------------|---------|-------------|
| M1 | Relatore contesta validità statistica (test t su PnL non-i.i.d.) | Alta | Medio | Documentato in RESEARCH §6.2: bootstrap a blocchi temporali come metodo primario. Test t solo descrittivo secondario. |
| M2 | Relatore contesta "model-attribuibilità" | Media | Alto | Sostituito ovunque con "model-associated under controlled prompt and context conditions" (RESEARCH §0 + §1 RQ3). Linguaggio prudente preregistrato. |
| M3 | Relatore contesta confidence calibration (Brier score senza definizione esplicita) | Bassa | Medio | Definizione operativa vincolante nel prompt (RESEARCH §2.1). Documentata. |
| M4 | Esperimento "non riproducibile" senza dataset/codice | Bassa | Alto | Repo pubblica + dataset CSV/Parquet condivisibili (RESEARCH §8.2). Pre-registrazione via git commit (questo PRD + RESEARCH committed PRE-experiment). |
| M5 | Discussione "ipotesi causali speculative" troppo forte | Bassa | Medio | RQ3.5 esplicitamente etichettata "speculative interpretative" (RESEARCH §1 RQ3). Va in capitolo discussione, non risultati. |

---

## 14. Propagation map (tracciabilità PRE_PRD ↔ PRD ↔ implementazione)

Mappa esplicita che garantisce che **nessuna decisione strategica del PRE_PRD sia "persa"** nell'implementazione. Per ogni decisione del PRE_PRD: dove vive nel PRD, quale modulo Python la implementa, quale tabella DB la persiste, quale test la verifica.

### 14.1 Mapping decisioni strategiche

| PRE_PRD § | Decisione | PRD § | Modulo Python | Tabella DB | Test gating |
|-----------|-----------|-------|---------------|------------|-------------|
| §1.1 | 4 modelli design 2×2 USA/CN × premium/cheap_alt | §11.1 (deploy 4 agent) + §3.2.1 (models) | `config/settings.py::AgentSettings.llm_provider` | `models` | `tests/integration/test_seed_experiment.py` |
| §1.2 | 4 servizi Railway separati + 1 context-orchestrator | §11.1, §11.2 | `__main__.py` dispatcher + `orchestration/` | — | `tests/e2e/test_isolation.py` |
| §1.4 | Esperimento comparativo singola condizione | §6.2 (TradeDecision unico schema) | `domain/schemas.py` | `decisions`, `decision_actions` | `tests/unit/domain/test_schemas_trade_decision.py` |
| §1.5 | Setup ottimizzato baseline (no ablation) | §6.3 (ContextBundle unico) | `context/builder.py` | `context_snapshots` | `tests/integration/test_context_orchestrator.py` |
| §1.6 | ContextBuilder modulare | §2.2 (struttura collectors/) + §7.2 (BaseCollector) | `context/collectors/*.py` | `context_snapshots.context_json` | `tests/unit/context/test_collectors.py` |
| §11.1 | LLM stack: langchain-core minimal | §1.2 stack table | `pyproject.toml` deps + `llm/structured.py` | — | `test_llm_dependencies_pinned` |
| §11.2 | Pydantic + with_structured_output + freetext fallback | §8.2 (invoke_structured) | `llm/structured.py` | — | `tests/unit/llm/test_structured_parser.py` |
| §11.3 | Memoria 1 sì, Memoria 2 off default | §10.3 (`inject_decision_history: bool = False`) + invariante #5 | `config/settings.py::AgentSettings` | (env-driven, no DB) | `test_startup_memory_off_locked` |
| §11.4 | 4 sezioni del prompt (Technical/Sentiment/News/On-chain) | §6.3 (ContextBundle structure) | `prompts/templates/v1_baseline.md` | `prompt_templates.template_text` | `test_prompt_renders_all_sections` |
| §11.5 | Quadrupletta PnL + benchmark spot + 3 baseline | §3.3 RESEARCH note + §3.2.8 DDL | `db/repositories/baselines.py` + `scripts/compute_baselines.py` | `baseline_configs`, `baseline_equity_snapshots` | `tests/integration/test_baselines_pre_registered` |
| §11.6 | Prompt versioning SHA256 | §3.2.3 + §10.1 startup check A5 | `prompts/renderer.py` | `prompt_templates.sha256_hash`, `runs.prompt_template_hash`, `runs.rendered_prompt_hash` | `test_prompt_hash_immutable_after_seed` |
| §11.7 | Cost ledger per chiamata LLM | §8.3 + §3.2.6 | `llm/stats_handler.py` + `db/repositories/decisions.py` | `cost_events` | `tests/integration/test_cost_ledger_persisted_atomically` |
| §11.8 | Test isolamento cross-model | §9.5 | `tests/e2e/test_isolation.py` | — | (test stesso) |
| §11.9 | TradeDecision schema canonico | §6.2 | `domain/schemas.py::TradeDecision` + `ActionDecision` | `decisions`, `decision_actions` | `tests/unit/domain/test_schemas_*.py` |
| §13.3 | 4 guardrail Strategia C+ | §7.4 + §10.3 default + §8 LLM | `execution/guardrails.py` | `decision_actions.{leverage_clamped, size_pct_clamped, forced_hold}` | `tests/unit/execution/test_guardrails.py` + `tests/e2e/test_guardrail_e2e.py` |

### 14.2 Mapping requisiti Figma (F1/F2/F3)

| Figma Req | Descrizione | PRD § | Implementazione | Test gating |
|-----------|-------------|-------|-----------------|-------------|
| F1 | SL/TP mandatory per LONG/SHORT | §6.2 (model_validator) + §7.4 guardrail #1 | `domain/schemas.py::ActionDecision.validate_side_consistency` + `execution/guardrails.py` step 1 | `test_schemas_long_requires_sl_tp` + `test_guardrails_force_hold_if_no_sl_tp` |
| F2 | Confidence sempre presente (anche HOLD/FLAT) | §6.2 + invariante #7 | `domain/schemas.py::ActionDecision.confidence` (NOT NULL) | `test_confidence_required_even_for_hold` |
| F3 | Leverage con risk cap dinamico | §7.4 guardrail #3 (1 + confidence×9, hard cap 10) | `execution/guardrails.py` step 3 | `test_leverage_clamp_by_confidence` |

### 14.3 Mapping invarianti §5 ai test (riassuntivo)

Vedi §9.7 (Invariant coverage matrix) per la tabella completa. Tutti i 15 invarianti hanno un test gating in CI. Nessun invariante è "documentato ma non testato".

---

## 15. Handoff a Phase 5 (implementazione)

### 15.1 Pre-requisiti per iniziare Phase 5

Prima di aprire VSCode + Claude Code per scrivere la prima riga di codice, verificare:

- [x] `PRE_PRD.md` v3 committato (`73e3d02`)
- [x] `RESEARCH_DESIGN.md` v3 committato (`2e1df14`)
- [x] `PRD_V2.md` Round 1 v3 committato (`669ced9`)
- [x] `PRD_V2.md` Round 2 v3 committato (`e80c16e`)
- [ ] `PRD_V2.md` Round 3 v1 committato (questo Round, prossimo step)
- [ ] Branch `prd/v2-design` merge in `main` (via PR formale)
- [ ] Tag `prd-v2-frozen` su `main` per marcare il "blueprint freeze"
- [ ] Claude Code installato + configurato (`claude` CLI in PATH)
- [ ] `CLAUDE.md` project-level creato (vedi §15.2 bozza)

### 15.2 Bozza `CLAUDE.md` project-level

File da creare in root del repo come prima azione di Phase 5. Indirizza Claude Code (Sonnet/Opus) durante l'implementazione.

```markdown
# CLAUDE.md — AI Trading Agent V2 (Thesis Edition)

## Ground truth documenti

Tutti i documenti tecnici-scientifici sono in `docs/`:
- `PRE_PRD.md` — 18 decisioni strategiche
- `RESEARCH_DESIGN.md` — cornice scientifica (3 RQ, ipotesi, baseline)
- `PRD_V2.md` — blueprint tecnico completo (architettura + DDL + API + test + deploy)

**Regola d'oro**: se hai dubbi su una decisione di design, **NON inventare**.
Leggi prima il PRD V2. Se la risposta non c'è, fermati e chiedi all'utente.

## Architettura in 1 minuto

5 servizi Python su Railway condividono UN database Postgres:
- 1× `context-orchestrator` (5° servizio): materializza UN context_snapshot per
  tick di 15 minuti. Letto dai 4 agent.
- 4× `agent-<provider>` (OpenAI, Anthropic, DeepSeek, Qwen): ciascuno con
  proprio wallet HL testnet, legge context_snapshot del tick, invoca LLM,
  applica guardrail, esegue ordini.

Dispatch via `AIAT_SERVICE_ROLE`: stesso codice, ruoli diversi (vedi PRD §11.2).

## Stack vincolante

- Python 3.12+, `uv` package manager (NO pip, NO poetry)
- Pydantic v2 strict everywhere (NO `dict[str, Any]` nei contratti)
- SQLAlchemy 2.x async (`Mapped`/`DeclarativeBase`), `asyncpg` driver
- Alembic per migrations (NO modifiche manuali al DB)
- APScheduler per cron 15m (NO Railway cron native)
- `langchain-core` + `langchain-openai` + `langchain-anthropic` MINIMAL
  (NO LangGraph, NO LangChain high-level)
- structlog JSON logs (NO print() runtime — ruff T201 enforced)
- pytest + pytest-asyncio + pytest-postgresql + VCR.py per test
- ruff + mypy strict + import-linter in CI

## Invarianti non negoziabili (PRD §5)

15 invarianti. Particolarmente critici per il workflow di sviluppo:

1. **Isolation cross-model**: ogni query agent filtra `WHERE model_id = $AIAT_MODEL_ID`.
   Test gating: `tests/e2e/test_isolation.py` con RepositorySpy.

4. **Cost ledger atomico**: `LLMClient.invoke()` ritorna `CostEventData` DTO,
   persistito DOPO `decisions` nella stessa transazione. MAI scrivere
   `cost_events` direttamente in `invoke()`.

9. **No mainnet**: `AIAT_NETWORK=testnet` validato all'avvio. RuntimeError fatal
   se diverso.

12. **Decimal per soldi**: `decimal.Decimal` ovunque, MAI `float` per
    size/price/fee/PnL. SQLAlchemy `Numeric` columns.

13. **Parità market context**: il `context_snapshot` è scritto SOLO dal
    `context-orchestrator`. Gli agent NON fetchano sorgenti esterne durante
    la run.

14. **No dipendenze cicliche tra moduli runtime**: enforce by `import-linter`.

## Workflow di sviluppo

### Regola TDD obbligatoria per moduli core

I moduli in `domain/`, `llm/`, `execution/` richiedono **TDD**:
1. Scrivi il test PRIMA dell'implementazione
2. Verifica che il test fallisca
3. Implementa il minimo per far passare il test
4. Refactor

Coverage target: **95%** su questi moduli (CI gating).

### No commit senza test

Per ogni PR:
- [ ] Tutti i test passano (`uv run pytest`)
- [ ] Coverage globale ≥ 80%, core ≥ 95%
- [ ] `uv run ruff check src tests` clean
- [ ] `uv run mypy src` clean
- [ ] `uv run import-linter` clean
- [ ] Se è una migration: `alembic upgrade head` + `alembic downgrade base`
  testati su Postgres pulito

### Conventional commits

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`.

Esempio:
```
feat(llm): implement OpenAICompatibleClient for DeepSeek + Qwen

Unified client via base_url override. Reuses with_structured_output
path from langchain-openai. Reasoning tokens extracted from
completion_tokens_details.

Refs PRD §8.1 (factory dispatcher).
```

## Stile codice

- Type hints **ovunque** (mypy strict mode)
- Docstring **stile Google** per funzioni pubbliche
- `async def` di default; `def` solo per pure function senza I/O
- NO `from X import *`
- Path imports espliciti: `from aiat.domain.schemas import TradeDecision`

## Sequenza milestones (PRD §12)

Sviluppo IN ORDINE: M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7.

Eccezione: M2 e M3 possono procedere in parallelo dopo M1.

**Non avviare Mn senza completare Mn-1.** Non avviare M7 (esperimento ufficiale) senza M6 verde (smoke 48h).

## Quando chiedere all'utente

Chiedi conferma esplicita PRIMA di:
- Modificare lo schema DDL (sempre via Alembic migration nuova)
- Aggiungere una dipendenza al `pyproject.toml`
- Cambiare un guardrail default
- Toccare i 4 model_id registrati nel seed
- Skip / soft-skip di un test

Procedi autonomamente per:
- Implementare un modulo seguendo il PRD
- Aggiungere test (sempre incoraggiati)
- Refactoring interno che NON cambia API esposta
- Fix di bug isolati con test che riproduce il bug
```

### 15.3 Setup iniziale Claude Code

Comandi raccomandati per primo bootstrap di Phase 5:

```bash
cd ~/projects/AI-Agent-for-Trading

# 1. Crea CLAUDE.md (vedi bozza §15.2 sopra)
$EDITOR CLAUDE.md

# 2. Verifica integrazione tools
claude --version
claude /init  # se non già fatto, scansiona il repo

# 3. Inizia M0 in sessione Claude Code
claude
# > "Leggi PRD V2 §11.2 e §12 (M0). Crea pyproject.toml, Dockerfile, ci.yml,
#    import-linter config seguendo §1.2 dipendenze e §2.2 struttura cartelle."
```

### 15.4 Decisioni deferite con milestone vincolante di chiusura

Le seguenti decisioni sono **esplicitamente deferite** alla fase di implementazione, ma **non sono debito indefinito**: ciascuna ha una milestone entro cui DEVE essere chiusa, con un razionale per la deferenza. Patch post peer-review AI_B su Round 3: trattamento rigoroso come *bounded deferrals*, non *open issues*.

| ID | Decisione deferita | Razionale per la deferenza | Milestone di chiusura | Vincolante perché |
|----|---------------------|-----------------------------|------------------------|--------------------|
| D1 | Selezione finale dei 4 modelli LLM concreti (es. `gpt-5.1-2026-Q1` esatto vs altre versioni) | Le release dei provider cambiano; voglio i modelli più recenti al momento del seed, non quelli noti oggi | **M7 step 4** (`scripts/seed_experiment.py`) | I 4 model_id finiscono nella tabella `models` al seed; da quel momento sono immutabili per tutto l'esperimento |
| D2 | HOLD/FLAT outcome labeling rule (definizione operativa controfattuale) | Richiede di osservare in smoke test la distribuzione effettiva di HOLD/FLAT prima di scegliere la regola | **M4** (ExecutionLayer + OutcomeResolver) | Necessaria PRIMA dell'analisi di calibrazione confidence (Brier score richiede outcome binary definito); senza chiusura M4 non può chiudersi |
| D3 | Lista finale exception class per `_is_rate_limit_error` / `_is_auth_error` (isinstance() primary) | Dipende dalle versioni esatte dei SDK provider al momento dello sviluppo | **M2** (LLM abstraction) | Il modulo `llm/structured.py` non può raggiungere coverage 95% senza isinstance() checks puntuali e test che li validano |
| D4 | Lista finale `controlled_signals` (vocabolario `key_signals`) | La lista preliminare di 18 valori in §6.2 può richiedere raffinamento dopo aver osservato cosa generano i 4 LLM in smoke test | **M3** (ContextOrchestrator + smoke prompt) | Versione finale committed in `prompt_templates.controlled_signals` al seed; prompt_template_hash dipende da questa lista |
| D5 | Numero esatto di news items per tick + lista RSS sources definitiva | Trade-off tra rilevanza informativa e token budget del prompt; calibrare in M3 sul contesto reale | **M3** (collectors/news.py) | Il prompt template hash include questi parametri, deve essere stabile dal seed in poi |

**Regola operativa**: lo studente NON può marcare una milestone come "DONE" se la decisione deferita ad essa associata non è chiusa e documentata. Esempio: M4 non può chiudersi se D2 (HOLD/FLAT outcome labeling rule) non è stata fissata, implementata in `OutcomesRepository`, e testata.

**Tracciabilità in git**: ogni chiusura di deferred decision viene committed con messaggio dedicato che cita l'ID (es. `feat(outcomes): close D2 — HOLD/FLAT outcome labeling rule`). Permette di verificare a posteriori che nessuna decisione sia sfuggita.

---

*Fine PRD V2 Round 3 v2 (post peer-review esterna AI_B, verdetto 9.3/10 APPROVATO, 1 patch puntuale integrata su §15.4 — bounded deferrals con milestone vincolante). Documento completo: §0-§15. PRONTO per commit definitivo. Dopo il commit, la Fase 4.2 è completa, branch `prd/v2-design` può essere mergeato in `main` con tag `prd-v2-frozen`, e inizia la Phase 5 (implementazione).*
