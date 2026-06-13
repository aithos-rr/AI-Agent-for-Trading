# TASK MAP — derivazione M0→M5 dal PRD V2

> **Documento intermedio (Fase 1).** Mappa gerarchica dei task derivati da `docs/PRD_V2.md`,
> ordinati per dipendenza, tracciabili alla sezione PRD di origine, con tipo di `verify:` previsto
> e marcatori per i punti-stop che richiedono intervento umano.
>
> NON è ancora `TASKS.md`. È il blueprint da cui Claude Code formalizzerà `TASKS.md` (Fase 2).
> Obiettivo: copertura completa del PRD senza buchi + ordine di dipendenza corretto + confini del loop chiari.

---

## Legenda

- **ID**: `M<n>-T<nn>` — milestone + numero progressivo
- **🤖 LOOP**: eseguibile dal Ralph loop (Sonnet) in autonomia
- **🛑 STOP-UMANO**: richiede intervento manuale (credenziali, wallet, deploy) — il loop NON può chiuderlo
- **⚠️ ZONA-GRIGIA**: il codice è scrivibile/testabile dal loop con mock, ma la verifica "reale" richiede risorse esterne (da fare assistito dopo)
- **dep**: task prerequisito
- **verify**: tipo di comando di verifica (Claude Code lo renderà eseguibile e preciso in Fase 2)

---

## ⚠️ DISCREPANZE PRD DA RISOLVERE (annotazioni per Fase 2)

Durante la lettura ho trovato incongruenze che i task devono gestire esplicitamente, NON ereditare:

1. **Numero tabelle DDL**: §12 e §3.1 dicono "17 SQLAlchemy models", ma il DDL §3.2 contiene **20 `CREATE TABLE`**. Lista reale: experiments, models, prompt_templates, context_snapshots, context_build_runs, runs, llm_invocations, decisions, decision_actions, account_snapshots, positions, orders, fee_events, funding_events, cost_events, tax_sim_periods, outcomes, baseline_configs, baseline_equity_snapshots, errors. **→ I task M1 usano "20 tabelle" con elenco esplicito.** (Candidato ADR: documentare la correzione 17→20.)

2. **Struttura cartelle vs repository reali**: §2.2 elenca in `db/repositories/` solo 4 file (`decisions.py`, `positions.py`, `snapshots.py`, `ledger.py`), ma §7.6 + fix B.5 definiscono anche `runs.py`, `outcomes.py`, `context_build.py`, `baselines.py`, `tax_simulation.py`. **→ I task seguono §7.6 (più recente), non §2.2.** (Candidato ADR.)

3. **`tests/unit/` sottostruttura**: §2.2 mostra `tests/unit/` vuota, ma §9.2 specifica `tests/unit/domain/`, `tests/unit/execution/`, `tests/unit/llm/`. **→ I task creano la sottostruttura di §9.2.**

4. **`alembic/` posizione**: §2.2 mette `alembic/` a root-level (fuori da `src/`), `alembic.ini` a root. **→ Confermato così.**

5. **Confine HOLD/FLAT outcome (D2)**: bounded deferral, da chiudere in M4 (ADR obbligatorio). Il task M4 relativo è marcato.

---

# M0 — Setup repo + CI baseline 🤖 LOOP (interamente autonomo)

> Nessuna credenziale. Tutto verificabile offline. **Il loop può chiudere M0 al 100%.**
> Fonte: §12 M0 DoD, §1.2 stack, §2.2 struttura, §9.6 CI.

| ID | Task | dep | verify | note |
|----|------|-----|--------|------|
| M0-T01 | Crea `pyproject.toml` con `uv`, Python ≥3.12, tutte le dipendenze §1.2 + §1.3 (langchain-core/openai/anthropic, pydantic 2.x, pydantic-settings, sqlalchemy 2.x, asyncpg, alembic, apscheduler 3.x, hyperliquid-python-sdk, httpx, structlog, pandas/numpy/pandas-ta, tenacity, python-decimal stdlib) + dev deps (pytest, pytest-asyncio, pytest-cov, pytest-postgresql, vcrpy/pytest-vcr, ruff, mypy, import-linter) | — | `uv sync` esce 0; `uv tree` mostra le dep chiave | versioni pinnate (T7 risk register) |
| M0-T02 | Genera `uv.lock` e committalo | M0-T01 | file `uv.lock` esiste e non vuoto | |
| M0-T03 | Crea skeleton `src/aiat/` con tutti gli `__init__.py` dei sotto-package (config, domain, db, db/models, db/repositories, context, context/collectors, prompts, llm, execution, orchestration, observability) | M0-T01 | `python -c "import aiat"` esce 0 | layout src/ §2.2 |
| M0-T04 | Crea `src/aiat/__main__.py` con dispatcher minimale su `AIAT_SERVICE_ROLE` (stub che chiama `load_settings()` e logga il ruolo; logica completa a M5) | M0-T03 | `python -m aiat` con env fittizia non crasha sull'import; oppure stub che stampa il ruolo | §11.2 |
| M0-T05 | Configura `ruff` in `pyproject.toml`: linter+formatter, rule `T201` abilitata (no print, invariante #10), target py312 | M0-T01 | `uv run ruff check src tests` esce 0 su skeleton | inv #10 |
| M0-T06 | Configura `mypy` strict in `pyproject.toml` (strict su moduli core domain/llm/execution) | M0-T01 | `uv run mypy src` esce 0 su skeleton | §1.2 |
| M0-T07 | Configura `pytest` + `pytest-asyncio` + coverage in `pyproject.toml` (`--cov-fail-under=80`) + crea `.coveragerc` (§9.6: branch, omit __main__/logging_config, exclude_lines) | M0-T01 | `uv run pytest` (anche 0 test) esce 0 | §9.1, §9.6 |
| M0-T08 | Configura `import-linter` con ≥1 contratto: no cicli tra `domain/` e altri; layering `orchestration` → moduli → `domain` | M0-T03 | `uv run lint-imports` (o `import-linter`) esce 0 su skeleton | inv #14 |
| M0-T09 | Crea `docker/Dockerfile` multi-stage non-root (§11.3: stage builder uv, stage runtime python:3.12-slim, user aiat uid 10001, PYTHONPATH=/app/src) | M0-T03 | `docker build -f docker/Dockerfile .` esce 0 (image successful) | §11.3 — **richiede Docker nel container/devcontainer** |
| M0-T10 | Crea `.github/workflows/ci.yml` esatto come §9.6 (uv sync --frozen, ruff check, ruff format --check, mypy, pytest unit/integration/e2e con le 2 soglie coverage, import-linter) | M0-T07, M0-T08 | il file esiste, `yaml` valido; (CI green reale solo dopo push — verificabile a fine M0) | §9.6 |
| M0-T11 | Verifica che `.env.example` (già presente da commit 55f0727) copra tutti i nomi env var di §11.4; integra se mancano | — | grep dei nomi AIAT_* attesi presente | §11.4 — già fatto, solo verifica |
| M0-T12 | Crea `README.md` minimale (titolo, link a docs/, comando setup `uv sync`, comando run `python -m aiat`) | — | file esiste | §2.2 |

**DoD M0 (gate)**: `uv sync` + `ruff` + `mypy` + `pytest` + `import-linter` tutti verdi su skeleton; `docker build` ok; `ci.yml` presente. → **PR #2 dopo M0** (push triggera CI green).

---

# M1 — Domain + DB schema + migrations 🤖 LOOP (interamente autonomo)

> Tutto offline + `pytest-postgresql` effimero. **Il loop può chiudere M1 al 100%.**
> Fonte: §12 M1, §3.2 DDL (20 tabelle), §6 schemi Pydantic, §9.2-9.3 test.
> **NOTA**: M1 è prerequisito sia di M2 che di M3 (che possono poi parallelizzare).

| ID | Task | dep | verify | note |
|----|------|-----|--------|------|
| M1-T01 | `src/aiat/domain/enums.py`: tutti gli enum di §6.1 (Side, EntryType, Tier, GuardrailKind, RunStatus, ecc. — estrarre lista esatta da §6.1 righe 1153-1205) | M0 | `uv run pytest tests/unit/domain/test_enums.py` verde | §6.1 |
| M1-T02 | `src/aiat/domain/schemas.py`: `TradeDecision` (portfolio-level) + `ActionDecision` (action-level) con tutti i `model_validator` condizionali di §6.2 (HOLD/FLAT: size=0, leverage=0, entry_type=none, no SL/TP, **no limit_price**; LONG/SHORT: size>0, SL/TP obbligatori, limit_price se entry_type=limit; key_signals da `Literal` vocabolario controllato; confidence ∈[0,1]; max 3 action) | M1-T01 | `uv run pytest tests/unit/domain/test_schemas_trade_decision.py` verde (tutti gli 8 casi §9.2) | §6.2, inv #6 #7, Figma F1/F2/F3 |
| M1-T03 | `src/aiat/domain/schemas.py` (cont.): schemi contesto §6.3 (`ContextBundle` con docstring "market context byte-identico cross-model", PortfolioState, ecc.) + DTO runtime §6.4 (`CostEventData` con `n_attempts` ge=1, `LLMInvocationResult`, tutti i `Field(ge=0)`) | M1-T02 | `uv run pytest tests/unit/domain/test_pydantic_serialization.py` verde (roundtrip JSON) | §6.3, §6.4 |
| M1-T04 | `src/aiat/domain/exceptions.py`: gerarchia base eccezioni dominio | M1-T01 | import + `uv run mypy src` verde | §12 M1 |
| M1-T05 | `src/aiat/db/models/base.py`: DeclarativeBase SQLAlchemy 2.x async + mixin comuni (timestamps, ecc.) | M0 | import + mypy verde | §3.2, §1.2 |
| M1-T06 | `src/aiat/db/models/`: **20** modelli SQLAlchemy declarative (uno per tabella §3.2), tutti con `Mapped[]`, tipi `Numeric` per soldi (inv #12), colonne denormalizzate experiment_id/model_id/run_id dove §5 inv #3 lo richiede, tutti i CHECK constraint del DDL | M1-T05, M1-T01 | import di tutti i model + mypy verde | §3.2 (20 tabelle!), inv #3 #12 — vedi DISCREPANZA #1 |
| M1-T07 | `src/aiat/db/session.py`: async engine + AsyncSession factory (asyncpg) | M1-T05 | import + mypy verde | §1.2 |
| M1-T08 | Setup `alembic/` (env.py async-aware, script.py.mako) + `alembic.ini` a root | M1-T06, M1-T07 | `alembic check` o import config ok | §2.2 |
| M1-T09 | Genera migration `alembic/versions/001_initial_schema.py` (autogenerate dai 20 model, poi review manuale per CHECK/indici/composite FK/UNIQUE) | M1-T08 | `alembic upgrade head` su Postgres effimero crea 20 tabelle | §12 M1, §3.2 |
| M1-T10 | `tests/conftest.py`: fixture `pytest-postgresql` (postgresql_proc, postgresql, db_url che applica alembic upgrade head, db_session) come §9.3 | M1-T09 | la fixture si istanzia senza errori | §9.3 |
| M1-T11 | `tests/integration/test_db_migrations.py`: upgrade head from empty, tutti i CHECK applicati, tutti gli indici, downgrade base + upgrade idempotente | M1-T10 | `uv run pytest tests/integration/test_db_migrations.py` verde | §9.3 |
| M1-T12 | Coverage check: moduli `domain/` ≥95% | M1-T02, M1-T03 | `uv run pytest tests/unit/domain --cov=src/aiat/domain --cov-fail-under=95` verde | §9.1 |

**DoD M1 (gate)**: tutti i test domain+integration verdi; `alembic upgrade head` crea 20 tabelle con tutti i constraint; coverage domain ≥95%.

---

# M2 — LLM abstraction + StatsHandler 🤖 LOOP (autonomo, VCR no API reali)

> Testato con cassette VCR (HTTP registrato). **Nessuna API key reale serve al loop.**
> **MA**: le cassette VCR vanno *registrate* almeno una volta contro le API reali. Se non esistono già,
> la loro creazione è ⚠️ ZONA-GRIGIA (vedi M2-T10). Il loop può scrivere il codice e i test;
> il primo `record_mode=once` reale potrebbe richiederti le API key.
> Fonte: §12 M2, §7.3, §8, §9.2 (llm), §9.4.
> **Parallelizzabile con M3 dopo M1.**
> **Chiude bounded deferral D3** (exception class isinstance) → ADR.

| ID | Task | dep | verify | note |
|----|------|-----|--------|------|
| M2-T01 | `src/aiat/llm/exceptions.py`: 5 classi §8.2 (LLMError base, LLMTimeoutError, LLMRateLimitError, LLMAuthError, LLMParsingError, LLMUnrecoverableError con primary+fallback) | M1 | import + mypy verde | §8.2 |
| M2-T02 | `src/aiat/llm/base.py`: `BaseLLMClient` ABC completo §7.3 | M2-T01, M1-T03 | import + mypy verde | §7.3 |
| M2-T03 | `src/aiat/llm/structured.py`: `invoke_structured` (fallback selettivo solo parsing, NON timeout/rate/auth) + `_extract_json_balanced` (state machine NORMAL/IN_STRING/IN_STRING_ESCAPE) + `_is_parsing_error`/`_is_rate_limit_error`/`_is_auth_error` | M2-T01 | `uv run pytest tests/unit/llm/test_structured_parser.py` verde (well-formed, nested, prose-surrounding, fallback-after-failure, unrecoverable) | §8.2, inv via #6 |
| M2-T04 | **[D3]** Raffina `_is_rate_limit_error`/`_is_auth_error`: isinstance() primary su classi SDK ufficiali (openai.RateLimitError, anthropic.RateLimitError, ecc.) + string-match fallback. **Crea ADR docs/decisions/0002-exception-classification.md** | M2-T03 | test unit con eccezioni mockate dei 4 SDK; ADR file esiste | §8.2 fix B.18, **chiude D3** |
| M2-T05 | `src/aiat/llm/stats_handler.py`: `StatsCallbackHandler` con aggregazione multi-tentativo + `n_attempts` + `build_cost_event()` (Decimal precision) | M2-T01, M1-T03 | `uv run pytest tests/unit/llm/test_stats_handler.py` verde (OpenAI/Anthropic/DeepSeek-Qwen usage extraction, cost_usd Decimal) | §8.3, inv #12 |
| M2-T06 | `src/aiat/llm/openai_client.py` | M2-T02 | import + mypy | §8.1 |
| M2-T07 | `src/aiat/llm/anthropic_client.py` | M2-T02 | import + mypy | §8.1 |
| M2-T08 | `src/aiat/llm/openai_compatible_client.py` (DeepSeek + Qwen via base_url) | M2-T02 | import + mypy | §8.1 |
| M2-T09 | `src/aiat/llm/factory.py`: `load_llm(settings: AgentSettings) -> BaseLLMClient` (tipizzato su AgentSettings, NON BaseAIATSettings — least privilege) | M2-T06, M2-T07, M2-T08 | import + mypy; test dispatch per i 4 provider | §8.1 fix B.17 |
| M2-T10 | `src/aiat/config/model_pricing.yaml`: pricing USD/1M token per i 4 modelli (D1 sui nomi esatti è deferito a M7; qui struttura + placeholder/valori correnti) | M1 | YAML valido, parsabile | §8.4 |
| M2-T11 | ⚠️ Cassette VCR `tests/cassettes/`: le 15 cassette di §9.4 (4 structured success, fallback, unrecoverable, timeout, rate-limit, auth, cost-tracking ×2, cost-aggregation, 3 reasoning-trace). **Se non esistono, registrarle con `record_mode=once` richiede API key reali** | M2-T03, M2-T05, M2-T09 | `uv run pytest tests/integration/test_llm_providers.py` verde con `record_mode=none` | §9.4 — **⚠️ ZONA-GRIGIA: serve API key per il primo record** |
| M2-T12 | `tests/conftest.py` (cont.): config VCR (cassette_library_dir, record_mode none in CI, filter_headers authorization/x-api-key) | M2-T11 | la config si carica | §9.4 |
| M2-T13 | Coverage check: moduli `llm/` ≥95% | M2-T03, M2-T05 | `uv run pytest tests/unit/llm --cov=src/aiat/llm --cov-fail-under=95` verde | §9.1 |

**DoD M2 (gate)**: test unit llm + integration VCR verdi (record_mode=none); 4 client + factory + stats_handler completi; coverage llm ≥95%; ADR D3 creato.
**⚠️ Punto attenzione umano**: se le cassette VCR non esistono, la loro prima registrazione contro API reali richiede le tue API key (~$0.01 di costo). Da fare assistito.

---

# M3 — ContextOrchestrator + collectors 🤖 LOOP (codice+unit) / ⚠️ smoke reale

> Codice + unit test (httpx mock) + integration Postgres effimero: **il loop li scrive e verifica.**
> Lo smoke reale (§12 M3 verifica: "4 context_snapshots in 1 ora contro fonti reali") è ⚠️ ZONA-GRIGIA:
> richiede connettività a RSS/F&G/HL-info reali. Il loop chiude i test mockati; lo smoke reale è assistito.
> Fonte: §12 M3, §7.1, §7.2, §6.3.
> **Parallelizzabile con M2 dopo M1.**
> **Chiude bounded deferrals D4** (controlled_signals) **e D5** (RSS sources) → ADR.

| ID | Task | dep | verify | note |
|----|------|-----|--------|------|
| M3-T01 | `src/aiat/context/collectors/base.py`: `BaseCollector` ABC §7.2 (timeout esplicito, cache TTL dove applicabile) | M1 | import + mypy | §7.2 |
| M3-T02 | `src/aiat/context/collectors/technical.py`: indicatori (porting da V1 `legacy/v1/indicators.py`, pandas-ta) | M3-T01 | `uv run pytest tests/unit/context/test_collectors.py::technical` con httpx mock | §2.2, §1.3 |
| M3-T03 | `src/aiat/context/collectors/sentiment.py`: Fear&Greed | M3-T01 | unit test httpx mock | §2.2 |
| M3-T04 | **[D5]** `src/aiat/context/collectors/news.py`: RSS (CryptoPanic, CoinDesk). **Decidi numero items/tick + lista RSS finale → ADR docs/decisions/0003-rss-sources.md** | M3-T01 | unit test httpx mock; ADR esiste | §2.2, **chiude D5** |
| M3-T05 | `src/aiat/context/collectors/onchain.py`: funding, OI, liquidations (HL info endpoint pubblico, read-only) | M3-T01 | unit test httpx mock | §2.2 |
| M3-T06 | **[D4]** `src/aiat/context/controlled_signals.py`: vocabolario controllato finale. **→ ADR docs/decisions/0004-controlled-signals.md** | M1-T02 | i `Literal` in schemas.py combaciano con questa lista; ADR esiste | §6.2, inv #6, **chiude D4** |
| M3-T07 | `src/aiat/context/builder.py`: ContextBuilder che compone i collectors in `ContextBundle` | M3-T02..T06 | unit test con collectors mockati | §6.3 |
| M3-T08 | `src/aiat/db/repositories/context_build.py`: `ContextBuildRepository` (start_build, complete_build, fail_build, get_snapshot_for_tick) §7.6 | M1-T06 | integration test Postgres effimero | §7.6 fix B.5 |
| M3-T09 | `src/aiat/orchestration/context_orchestrator.py`: entrypoint 5° servizio (compone builder + repository, gestisce fallimenti → status) | M3-T07, M3-T08 | integration test Postgres effimero: scrive context_snapshots + context_build_runs anche su fallimenti parziali | §7.1 |
| M3-T10 | `tests/unit/context/`: unit test per ogni collector (httpx mock) | M3-T02..T05 | `uv run pytest tests/unit/context` verde | §9 (M3 DoD) |
| M3-T11 | ⚠️ Smoke reale orchestrator (verifica §12 M3): `python -m aiat` role=context_orchestrator contro fonti reali genera context_snapshot | M3-T09 | **manuale/assistito** — richiede connettività reale | §12 M3 — ⚠️ ZONA-GRIGIA |

**DoD M3 (gate, parte loop)**: collectors + builder + orchestrator + ContextBuildRepository completi; unit (mock) + integration (Postgres effimero) verdi; ADR D4 e D5 creati.
**⚠️ Smoke reale (M3-T11)**: assistito, richiede rete.

---

# M4 — ExecutionLayer + guardrails 🤖 LOOP (codice+unit) / 🛑 e2e testnet

> Guardrails + sizing + outcome_resolver: logica pura, **il loop scrive e testa al 100%** (unit + integration Postgres effimero).
> **MA**: §12 M4 verifica include "smoke su wallet testnet REALE apre LONG BTC con SL/TP". → **PRIMO STOP FISICO**: serve un wallet HL testnet reale fundato + chiave in `.env`.
> Fonte: §12 M4, §7.4, §7.5, §9.2 (execution).
> **Chiude bounded deferral D2** (HOLD/FLAT outcome labeling) → ADR.

| ID | Task | dep | verify | note |
|----|------|-----|--------|------|
| M4-T01 | `src/aiat/execution/sizing.py`: sizing posizioni Decimal (notional = price × size_units × leverage, no float) | M1 | `uv run pytest tests/unit/execution/test_sizing.py` verde (Decimal precision, no float) | §9.2, inv #12 |
| M4-T02 | `src/aiat/execution/guardrails.py`: 4 guardrail Strategia C+ §7.4 in ordine (SL→size→leverage→confidence), report con original_side se forced_hold | M1-T02 | `uv run pytest tests/unit/execution/test_guardrails.py` verde (tutti i casi §9.2: HOLD forced if no SL, size clamp 0.20, leverage clamp 1+conf×9, confidence<0.4→HOLD, ordine, report) | §7.4, inv #8, Figma F1/F3 |
| M4-T03 | `src/aiat/execution/hyperliquid_client.py`: refactor da V1 `legacy/v1/hyperliquid_trader.py`, interfaccia §7.5 (`execute_action(action, run_id, current_position)`), ABC con semantica LONG/SHORT/FLAT/HOLD | M1, M2-T01 | import + mypy; unit test con HL mockato | §7.5 |
| M4-T04 | **[D2]** `src/aiat/execution/outcome_resolver.py`: risoluzione outcomes. **Decidi regola labeling HOLD/FLAT (controfattuale) → ADR docs/decisions/0005-holdflat-outcome.md** | M1-T06 | unit test; ADR esiste | §4.2, **chiude D2** (critico: prima dell'analisi confidence) |
| M4-T05 | `src/aiat/db/repositories/positions.py`: `PositionsRepository` (open_position crea positions+orders+fee_events in transazione; close_position → outcomes con FK; opening_action_id UNIQUE) §7.6 | M1-T06 | `uv run pytest tests/integration/test_db_repositories_positions.py` verde | §7.6 |
| M4-T06 | `tests/unit/execution/`: unit test completi guardrails + sizing | M4-T01, M4-T02 | coverage execution ≥95% | §9.2 |
| M4-T07 | `tests/integration/test_db_repositories_positions.py`: open→close→outcomes con Postgres effimero | M4-T05 | verde | §9.3 |
| M4-T08 | 🛑 e2e testnet reale (§12 M4 verifica): smoke wallet testnet apre LONG BTC con SL/TP, chiude, verifica `outcomes.pnl_net_fee_funding_usd` | M4-T03, M4-T05 | **STOP-UMANO**: crea wallet testnet, funda via faucet, chiave in `.env` | §12 M4 — 🛑 **PRIMO STOP FISICO** |
| M4-T09 | Coverage check: `execution/` ≥95% | M4-T06 | `--cov=src/aiat/execution --cov-fail-under=95` verde | §9.1 |

**DoD M4 (gate, parte loop)**: guardrails+sizing+hyperliquid_client(ABC+mock)+outcome_resolver+PositionsRepository completi; unit+integration verdi; coverage execution ≥95%; ADR D2 creato.
**🛑 e2e testnet (M4-T08)**: STOP fisico — richiede wallet testnet reale. Da qui in poi serve il tuo intervento.

---

# M5 — Decision loop e2e + isolation/parity 🤖 LOOP (codice+e2e mock) / 🛑 chiusura reale

> Il loop scrive decision_loop, scheduler, lifecycle, DecisionsRepository e i test e2e **mockati** (LLM cassette + HL mock + Postgres effimero).
> Lo "smoke locale 4 tick" di §12 M5 con tutto mockato è fattibile dal loop. La chiusura "verde davvero" sfuma verso l'integrazione reale.
> Fonte: §12 M5, §4.1, §7.6, §10.1, §9.5.

| ID | Task | dep | verify | note |
|----|------|-----|--------|------|
| M5-T01 | `src/aiat/db/repositories/decisions.py`: `DecisionsRepository` con transazione atomica (decision+actions+cost_events+llm_invocations in 1 commit, inv #4; flush ma no commit interno, inv transaction policy) §7.6 | M1-T06, M2-T05 | `uv run pytest tests/integration/test_db_repositories_decisions.py` verde (atomica, rollback su action invalida, CHECK, composite FK) | §7.6, inv #4 #11 |
| M5-T02 | Repository residui §7.6: `snapshots.py`, `runs.py`, `outcomes.py`, `baselines.py`, `tax_simulation.py` (+ `ledger.py` se previsto) | M1-T06 | integration test per ciascuno | §7.6 — vedi DISCREPANZA #2 |
| M5-T03 | `src/aiat/config/settings.py`: `BaseAIATSettings` + `AgentSettings` + `ContextOrchestratorSettings` (least privilege, discriminator service_role, validate_api_key_matches_provider) §10.3 | M1 | `uv run pytest` su test settings (validator, least privilege); mypy | §10.3 fix B.13 |
| M5-T04 | `src/aiat/orchestration/lifecycle.py`: `startup_checks` dispatcher role-specific (10 check agent A1-A10 + 4 orchestrator O1-O4, incl. env-var leak detection) §10.1 | M5-T03 | unit test con settings mockate per i check critici (network testnet inv #9, memory off inv #5, no-mainnet, baseline fatal) | §10.1 |
| M5-T05 | `src/aiat/orchestration/scheduler.py`: APScheduler config §4.1 (CronTrigger 0/15/30/45, coalesce, max_instances=1, misfire_grace_time, start_delay 30s agent) | M1 | unit test config scheduler | §4.1 |
| M5-T06 | `src/aiat/orchestration/decision_loop.py`: 1 tick completo §4.1 (read context_snapshot → render prompt → invoke LLM → guardrails → execute → persist atomico). Budget 180s hard timeout, no fallback tick precedente | M5-T01, M3-T09, M4-T02, M4-T03, M2-T09 | parte di test_decision_loop_smoke | §4.1 |
| M5-T07 | Completa `src/aiat/__main__.py`: dispatcher reale che build scheduler per ruolo (agent vs orchestrator) §11.2 | M5-T04, M5-T05 | `python -m aiat` con env valida avvia (mockato) | §11.2 |
| M5-T08 | `tests/e2e/test_decision_loop_smoke.py`: run_once con LLM cassette + HL mock + Postgres effimero; verifica runs.status=success, 1 decision, 3 actions, 1 cost_event, 1 llm_invocation, account_snapshot; se LONG → 3 orders | M5-T06 | `uv run pytest tests/e2e/test_decision_loop_smoke.py` verde | §9.5 |
| M5-T09 | `tests/e2e/test_isolation.py` (inv #1): RepositorySpy (primario) + DB-level trap (secondario); seed 2 model_id, lancia agent model_1, fallisce se legge model_2 | M5-T01, M5-T02 | `uv run pytest tests/e2e/test_isolation.py` verde | §9.5, inv #1 |
| M5-T10 | `tests/e2e/test_context_parity.py` (inv #13): orchestrator → 1 snapshot, 4 agent stesso tick_id, verifica context_snapshot_id identico + context_hash byte-identico (market), portfolio_state_hash diverge ok | M5-T06, M3-T09 | `uv run pytest tests/e2e/test_context_parity.py` verde | §9.5, inv #13 |
| M5-T11 | `tests/e2e/test_guardrail_e2e.py`: LLM mock propone size 0.99/lev 30/conf 0.95, verifica clamp a 0.20 / ≤10, flag clamped=true | M5-T06, M4-T02 | verde | §9.5 |
| M5-T12 | Invariant coverage matrix §9.7: verifica che tutti i 15 invarianti abbiano un test gating; marker `@pytest.mark.invariant("N")` | M5-T08..T11 | `uv run pytest -m invariant` copre #1-#15; nessuna cella vuota | §9.7 |
| M5-T13 | `src/aiat/observability/logging_config.py` + `metrics.py`: structlog JSON config (inv #10) | M0 | import; nessun print nel runtime (ruff T201 già attivo) | §2.2, inv #10 |
| M5-T14 | 🛑 Smoke locale "vero" multi-tick (§12 M5 verifica): 1 orchestrator + 4 agent fittizi 4 tick su Postgres locale | M5-T07..T11 | **assistito** — anche se mockato LLM, è integrazione di sistema da osservare | §12 M5 — 🛑/⚠️ |

**DoD M5 (gate, parte loop)**: decision_loop + scheduler + lifecycle + tutti i repository + settings completi; tutti gli e2e mockati verdi; invariant matrix #1-#15 verde.
**🛑 Da M5 in poi**: la chiusura reale e M6/M7 sono interamente umane (deploy Railway, 4 wallet, 4 API key, osservazione 48h + 4 settimane).

---

# CONFINE LOOP / UMANO (sintesi)

```
M0  🤖 ████████████████████  100% loop
M1  🤖 ████████████████████  100% loop
M2  🤖 ██████████████████░░  ~90% loop (⚠️ cassette VCR: 1 record reale assistito)
M3  🤖 ████████████████░░░░  ~85% loop (codice+test mock; ⚠️ smoke reale assistito)
M4  🤖 ██████████████░░░░░░  ~75% loop (codice+test mock; 🛑 e2e testnet = STOP fisico)
M5  🤖 ████████████░░░░░░░░  ~70% loop (codice+e2e mock; 🛑 chiusura reale assistita)
M6  🛑 ░░░░░░░░░░░░░░░░░░░░  0% loop — deploy Railway + 4 wallet + 4 API key (umano)
M7  🛑 ░░░░░░░░░░░░░░░░░░░░  0% loop — esperimento 4 settimane (umano + osservazione)
```

**Il primo STOP fisico inderogabile è M4-T08** (e2e su wallet testnet reale).
**Tutto il codice M0→M5 è scrivibile dal loop**; le verifiche "reali" (cassette VCR record, smoke orchestrator, e2e testnet, smoke multi-tick) sono assistite.

**Strategia consigliata per il loop**: `TASKS.md` copre **M0→M5** con i task 🛑 e ⚠️ marcati esplicitamente affinché il loop, arrivato a un task che richiede credenziali/rete reali, NON inventi un finto verde ma scriva `RALPH_BLOCKED` (o spunti solo la parte mock e segnali il blocco nel log). In pratica: i task 🛑/⚠️ vanno scritti in `TASKS.md` con `verify:` che il loop **non può** soddisfare senza risorse esterne, e una nota "HUMAN-GATED" che istruisce l'agente a fermarsi lì.

---

# ADR previsti durante M0→M5

| ADR | Titolo | Milestone | Chiude |
|-----|--------|-----------|--------|
| 0002 | Exception classification (isinstance + string fallback) | M2 | D3 |
| 0003 | RSS sources & news items count | M3 | D5 |
| 0004 | Controlled signals vocabulary | M3 | D4 |
| 0005 | HOLD/FLAT outcome labeling rule | M4 | D2 |
| (0006?) | Correzione conteggio tabelle 17→20 (DISCREPANZA #1) | M1 | — |
| (0007?) | Repository set completo §7.6 vs §2.2 (DISCREPANZA #2) | M1/M5 | — |

(D1 — nomi finali 4 modelli LLM — si chiude a M7 al seed, fuori dallo scope loop.)
