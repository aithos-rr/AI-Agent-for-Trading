# TASKS — AI Trading Agent V2 (Thesis Edition) — M0→M5

> **Scopo**: checklist eseguibile che governa il Ralph loop autonomo (modello Sonnet)
> per l'implementazione delle milestone **M0→M5**. Ogni task è atomico (1 context
> window), ha un `verify:` shell oggettivo, e una condizione `done-when` osservabile.
>
> **Ground truth**: `docs/PRD_V2.md` (blueprint tecnico, frozen, §0-§15). In caso di
> dubbio tra questo file e il PRD, **il PRD vince**. Derivato da `docs/TASK_MAP.md`
> (mappa gerarchica Fase 1). Vedi anche `docs/RESEARCH_DESIGN.md` e `docs/decisions/`.
>
> **Regola del loop** (vedi `PROMPT.md`): prendi UN solo task non spuntato (il primo in
> ordine di file con dipendenze soddisfatte), implementalo seguendo `CLAUDE.md`
> (TDD su `domain/`/`llm/`/`execution/`), **esegui `verify:`**, e spunta SOLO se passa.
> Non spuntare mai lavoro non verificato.
>
> **Data**: 2026-06-13 · M6/M7 sono fuori dallo scope del loop (interamente umane).

---

## Legenda marcatori

| Marcatore | Significato |
|-----------|-------------|
| 🤖 LOOP | eseguibile dal loop in autonomia, verify soddisfacibile offline/mock |
| ⚠️ ZONA-GRIGIA | codice+test mock scrivibili dal loop, ma la verifica "reale" richiede risorse esterne |
| 🛑 **[HUMAN-GATED]** | richiede credenziali/wallet/rete reali; il loop NON può chiuderlo |
| **[Dn]** | chiude una bounded deferral PRD §15.4 → **ADR obbligatorio** in `docs/decisions/` |
| 🐘 PG | il `verify:` richiede un server PostgreSQL locale (vedi Prerequisiti d'ambiente) |

---

## Prerequisiti d'ambiente (verificare PRIMA di lanciare il loop)

> Questi fatti sono stati **verificati sul filesystem reale** (host + `.devcontainer/`).
> Determinano quali `verify:` il loop può davvero eseguire. Leggere prima di partire.

1. **🐘 Server PostgreSQL assente** — `pytest-postgresql` (mandato da PRD §9.3) avvia un
   cluster Postgres effimero e richiede i binari **server** `initdb`/`pg_ctl`/`postgres`.
   Sul sistema corrente è presente **solo `postgresql-client`** (psql, pg_dump); il
   devcontainer `node:20` non ha alcun Postgres. **Tutti i task marcati 🐘 PG falliranno
   al setup della fixture (non per bug di codice) finché Postgres server non è
   provisionato.**
   **Fix consigliato (umano, una tantum)**: aggiungere `postgresql` alla lista
   `apt-get install` in `.devcontainer/Dockerfile` e ricostruire l'immagine (fornisce
   `initdb`/`pg_ctl` in `/usr/lib/postgresql/*/bin`). Alternativa: approvare l'aggiunta
   della dev-dep `pgserver` (binari Postgres bundled in wheel, no root) — richiede
   conferma utente + ADR (CLAUDE.md "Aggiungere una dipendenza").

2. **Docker daemon assente nel loop** — il devcontainer non monta il socket Docker e non
   ha il CLI `docker`. Il `verify:` di **M0-T09** è perciò **strutturale** (grep sul
   Dockerfile, eseguibile senza daemon). Il `docker build` reale va eseguito dall'umano
   sull'host (Docker 28.x presente) o in CI.

3. **Usare sempre `uv run python`** — non esiste un `python` nudo nel PATH; `uv` fornisce
   CPython 3.12. Tutti i `verify:` Python usano `uv run python ...`.

4. **Firewall egress del devcontainer** (`init-firewall.sh`): consentiti PyPI,
   `files.pythonhosted.org`, `astral.sh` (→ `uv sync` funziona), GitHub, Railway,
   `api.anthropic.com` (serve al loop stesso). **Bloccati**: `api.openai.com`,
   `api.deepseek.com`, Qwen/dashscope, Hyperliquid (testnet+info), RSS (CryptoPanic/
   CoinDesk), Fear&Greed. Questo è il motivo tecnico per cui il record delle cassette VCR
   (M2-T12), lo smoke orchestrator reale (M3-T11) e l'e2e testnet (M4-T08) sono gated.

---

## Confine loop / umano (sintesi da TASK_MAP)

```
M0  🤖 ████████████████████  setup repo + CI — loop completo (eccetto docker build reale)
M1  🤖 ████████████████████  domain + DB schema + migrations — loop completo (🐘 integration)
M2  🤖 ██████████████████░░  LLM abstraction — loop ~90% (🛑 record cassette VCR = M2-T12)
M3  🤖 ████████████████░░░░  Context + collectors — loop ~85% (⚠️ smoke reale = M3-T11)
M4  🤖 ██████████████░░░░░░  Execution + guardrails — loop ~75% (🛑 e2e testnet = M4-T08)
M5  🤖 ████████████░░░░░░░░  Decision loop e2e — loop ~70% (🛑 smoke multi-tick = M5-T14)
M6  🛑 ░░░░░░░░░░░░░░░░░░░░  deploy Railway + 4 wallet + 4 API key — fuori scope loop
M7  🛑 ░░░░░░░░░░░░░░░░░░░░  esperimento 4 settimane — fuori scope loop
```

**Primo STOP fisico inderogabile: `M4-T08`** (e2e su wallet HL testnet reale). Tutto il
codice M0→M5 è scrivibile dal loop; le verifiche "reali" (record cassette, smoke
orchestrator, e2e testnet, smoke multi-tick) sono assistite.

---

## M0 — Setup repo + CI baseline 🤖 LOOP

> Nessuna credenziale. Tutto verificabile offline (eccetto `docker build` reale).
> Fonte: PRD §1.2-§1.3 (stack), §2.2 (struttura), §9.6 (CI), §11.3 (Docker), §12 M0.

- [x] **M0-T01** — `pyproject.toml` con `uv` + dipendenze
  - **what**: Creare `pyproject.toml` (`[project]` Python ≥3.12, `[build-system]`
    hatchling con package src-layout `packages=["src/aiat"]`, `[tool.uv]`). Dipendenze
    runtime §1.2-§1.3 **pinnate**: `langchain-core`, `langchain-openai`,
    `langchain-anthropic`, `pydantic>=2`, `pydantic-settings`, `sqlalchemy[asyncio]>=2`,
    `asyncpg`, `alembic`, `apscheduler<4`, `hyperliquid-python-sdk`, `httpx`, `structlog`,
    `pandas`, `numpy`, `pandas-ta`, `tenacity`, `pyyaml`. Dev-deps: `pytest`,
    `pytest-asyncio`, `pytest-cov`, `pytest-postgresql`, `pytest-vcr`/`vcrpy`, `ruff`,
    `mypy`, `import-linter`. (`decimal` è stdlib, non va aggiunto.)
  - **prd**: §1.2, §1.3
  - **dep**: —
  - **files**: `pyproject.toml`
  - **verify**: `uv sync && uv run python -c "import langchain_core, pydantic, sqlalchemy, asyncpg, alembic, apscheduler, httpx, structlog, pandas, numpy, tenacity, yaml; print('deps ok')"`
  - **done-when**: `uv sync` esce 0 e tutte le dipendenze chiave sono importabili.

- [x] **M0-T02** — Genera e committa `uv.lock`
  - **what**: Generare il lockfile da `pyproject.toml` e committarlo (no `uv sync`
    durante run sperimentale — risk T7).
  - **prd**: §1.1
  - **dep**: M0-T01
  - **files**: `uv.lock`
  - **verify**: `test -s uv.lock && uv sync --frozen && echo "frozen ok"`
  - **done-when**: `uv.lock` esiste non vuoto e `uv sync --frozen` riesce.

- [x] **M0-T03** — Skeleton `src/aiat/` + `tests/`
  - **what**: Creare il layout `src/aiat/` con tutti gli `__init__.py` dei sotto-package
    (`config`, `domain`, `db`, `db/models`, `db/repositories`, `context`,
    `context/collectors`, `prompts`, `llm`, `execution`, `orchestration`,
    `observability`) e lo scheletro `tests/` (`tests/__init__.py`, `tests/conftest.py`
    minimale, `tests/unit/{domain,llm,execution,context,orchestration}/`,
    `tests/integration/`, `tests/e2e/` ciascuna con `__init__.py` dove utile). (DISCREPANZA
    #3: sottostruttura `tests/unit/` da §9.2, non da §2.2.)
  - **prd**: §2.2, §9.2
  - **dep**: M0-T01
  - **files**: `src/aiat/**/__init__.py`, `tests/**` (skeleton)
  - **verify**: `uv run python -c "import aiat" && uv run python -c "import aiat.domain, aiat.db.models, aiat.llm, aiat.execution, aiat.context.collectors, aiat.orchestration"`
  - **done-when**: il package `aiat` e tutti i sotto-package si importano senza errori.

- [x] **M0-T04** — `__main__.py` dispatcher minimale (stub)
  - **what**: Creare `src/aiat/__main__.py` con uno stub che legge `AIAT_SERVICE_ROLE`,
    chiama un `load_settings()` placeholder (o legge l'env direttamente) e **logga** il
    ruolo via `structlog` (MAI `print()` — inv #10). Logica completa rimandata a M5-T07.
  - **prd**: §11.2
  - **dep**: M0-T03
  - **files**: `src/aiat/__main__.py`
  - **verify**: `uv run ruff check src/aiat/__main__.py && uv run python -c "import aiat.__main__; print('main importable')"`
  - **done-when**: il modulo si importa, supera ruff (nessun `print`), non crasha all'import.

- [x] **M0-T05** — Config `ruff` (linter+formatter, T201)
  - **what**: In `pyproject.toml` `[tool.ruff]`: target `py312`, abilitare regola `T201`
    (no `print`, inv #10) + set base (E/F/I/UP/B). Eseguire `ruff format` sullo skeleton.
  - **prd**: §1.2 (inv #10)
  - **dep**: M0-T03
  - **files**: `pyproject.toml`
  - **verify**: `uv run ruff check src tests && uv run ruff format --check src tests`
  - **done-when**: `ruff check` e `ruff format --check` escono 0 su `src`+`tests`.

- [x] **M0-T06** — Config `mypy` strict
  - **what**: In `pyproject.toml` `[tool.mypy]`: `python_version=3.12`, `strict=true`
    (override per-modulo se serve allentare su test). Garantire che lo skeleton tipizzi.
  - **prd**: §1.2
  - **dep**: M0-T03
  - **files**: `pyproject.toml`
  - **verify**: `uv run mypy src`
  - **done-when**: `mypy src` esce 0 (clean) sullo skeleton.

- [x] **M0-T07** — Config `pytest` + coverage + `.coveragerc` + smoke test
  - **what**: In `pyproject.toml` `[tool.pytest.ini_options]`: `asyncio_mode="auto"`,
    `testpaths=["tests"]`, registrare il marker `invariant`. **NON** mettere
    `--cov-fail-under` negli `addopts` (il gating coverage vive nei comandi CI, §9.6, non
    nel default — altrimenti `pytest` fallirebbe sullo skeleton vuoto). Creare
    `.coveragerc` come §9.6 (`branch=True`, omit `__main__.py` + `logging_config.py`,
    `exclude_lines`). Aggiungere `tests/test_smoke.py::test_package_imports` (importa
    `aiat`) così la collection non è vuota (pytest esce 5 con 0 test).
  - **prd**: §9.1, §9.6
  - **dep**: M0-T03
  - **files**: `pyproject.toml`, `.coveragerc`, `tests/test_smoke.py`
  - **verify**: `uv run pytest -q`
  - **done-when**: `pytest` colleziona ≥1 test, passa, esce 0.

- [x] **M0-T08** — Config `import-linter`
  - **what**: In `pyproject.toml` `[importlinter]` + `[[importlinter.contracts]]`:
    `root_package=aiat`, ≥1 contratto — es. `domain` indipendente (forbidden:
    `aiat.domain -> aiat.db|llm|context|execution|orchestration`) + layering
    (`orchestration` in cima, `domain` in fondo). (inv #14.)
    **NOTA**: il comando CLI è `lint-imports`, NON `import-linter` (vedi Contraddizioni).
  - **prd**: §2.2 (inv #14)
  - **dep**: M0-T03
  - **files**: `pyproject.toml`
  - **verify**: `uv run lint-imports`
  - **done-when**: `lint-imports` esce 0 (contratti rispettati) sullo skeleton.

- [x] **M0-T09** — `docker/Dockerfile` multi-stage non-root
  - **what**: Creare `docker/Dockerfile` esatto come §11.3: stage `builder`
    (`python:3.12-slim`, `uv sync --frozen --no-install-project --no-dev`), stage
    `runtime` (`useradd -u 10001 aiat`, copia `.venv`+`src`+`alembic`, `PYTHONPATH=/app/src`,
    `USER aiat`, `CMD ["python","-m","aiat"]`).
  - **prd**: §11.3
  - **dep**: M0-T03
  - **files**: `docker/Dockerfile`
  - **verify**: `test -f docker/Dockerfile && grep -qE 'FROM .*AS builder' docker/Dockerfile && grep -qE 'FROM .*AS runtime' docker/Dockerfile && grep -q 'useradd -u 10001' docker/Dockerfile && grep -q 'USER aiat' docker/Dockerfile && grep -q 'PYTHONPATH="/app/src"' docker/Dockerfile`
  - **done-when**: il Dockerfile esiste con multi-stage + utente non-root uid 10001 +
    `PYTHONPATH`. **Nota**: il `docker build` reale (daemon assente nel loop) va eseguito
    da umano/CI: `docker build -f docker/Dockerfile -t aiat:test .`.

- [x] **M0-T10** — `.github/workflows/ci.yml`
  - **what**: Creare `.github/workflows/ci.yml` come §9.6 (uv sync --frozen, ruff check,
    ruff format --check, mypy, pytest unit con `--cov-fail-under=80`, step core 95% su
    `tests/unit/{domain,llm,execution}`, pytest integration, pytest e2e, import contracts).
    **Correggere** l'ultimo step in `uv run lint-imports` (§9.6 scrive `import-linter`, che
    non è un console script valido — vedi Contraddizioni).
  - **prd**: §9.6
  - **dep**: M0-T07, M0-T08
  - **files**: `.github/workflows/ci.yml`
  - **verify**: `test -f .github/workflows/ci.yml && uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')" && grep -q 'lint-imports' .github/workflows/ci.yml`
  - **done-when**: il file YAML è valido, contiene gli step lint/type/test e usa
    `lint-imports`. (CI green reale verificabile solo dopo push.)

- [x] **M0-T11** — Verifica `.env.example` copre §11.4
  - **what**: Verificare che `.env.example` (già presente, commit 55f0727) contenga tutti
    i nomi env var di §11.4; integrare se mancano. (Già fatto: solo verifica.)
  - **prd**: §11.4
  - **dep**: —
  - **files**: `.env.example`
  - **verify**: `for v in AIAT_EXPERIMENT_ID AIAT_GIT_COMMIT_SHA AIAT_DATABASE_URL AIAT_NETWORK AIAT_LOG_LEVEL AIAT_SERVICE_ROLE AIAT_MODEL_ID AIAT_PROMPT_TEMPLATE_HASH AIAT_SCHEMA_VERSION AIAT_LLM_PROVIDER AIAT_MODEL_NAME_API AIAT_TEMPERATURE AIAT_SEED AIAT_MAX_TOKENS AIAT_OPENAI_API_KEY AIAT_ANTHROPIC_API_KEY AIAT_DEEPSEEK_API_KEY AIAT_QWEN_API_KEY AIAT_HL_WALLET_PRIVATE_KEY AIAT_HL_WALLET_ADDRESS AIAT_MAX_SIZE_PCT AIAT_HARD_MAX_LEVERAGE AIAT_MIN_OPEN_CONFIDENCE AIAT_INJECT_DECISION_HISTORY AIAT_AGENT_START_DELAY_SECONDS AIAT_HARD_TIMEOUT_SECONDS; do grep -q "$v" .env.example || { echo "MISSING $v"; exit 1; }; done && echo "env vars ok"`
  - **done-when**: tutti i nomi `AIAT_*` attesi (§11.4) sono presenti in `.env.example`.

- [x] **M0-T12** — `README.md` minimale
  - **what**: Creare `README.md` (titolo progetto, link a `docs/`, comando setup
    `uv sync`, comando run `uv run python -m aiat`).
  - **prd**: §2.2
  - **dep**: —
  - **files**: `README.md`
  - **verify**: `test -f README.md && grep -q 'uv sync' README.md`
  - **done-when**: `README.md` esiste e cita il comando di setup.

> **DoD M0** (gate): `uv sync` + `ruff` + `mypy` + `pytest` + `lint-imports` verdi su
> skeleton; Dockerfile strutturalmente corretto; `ci.yml` presente e valido. → PR #2
> dopo M0 (push triggera CI; il `docker build` reale è confermato da CI/umano).

---

## M1 — Domain + DB schema + migrations 🤖 LOOP

> Tutto offline; integration su Postgres effimero (🐘 — vedi Prerequisiti). Prerequisito
> sia di M2 che di M3 (che poi parallelizzano). Fonte: §3.2 (DDL, **20 tabelle**), §6
> (schemi Pydantic), §9.2-§9.3 (test), §12 M1. TDD obbligatorio (CLAUDE.md).

- [x] **M1-T01** — `domain/enums.py`
  - **what**: TDD. Creare gli **8 enum di §6.1** (`StrEnum`): `Side`, `EntryType`, `Tier`,
    `Geography`, `RunStatus`, `ExecutionStatus`, `OrderKind`, `CloseReason` con i valori
    esatti del PRD. (NB: la TASK_MAP cita "GuardrailKind" ma **§6.1 NON lo definisce** →
    NON inventarlo; vedi Contraddizioni.)
  - **prd**: §6.1
  - **dep**: M0
  - **files**: `src/aiat/domain/enums.py`, `tests/unit/domain/test_enums.py`
  - **verify**: `uv run pytest tests/unit/domain/test_enums.py -q && uv run mypy src`
  - **done-when**: gli 8 enum esistono coi valori di §6.1 e il test passa.

- [x] **M1-T02** — `domain/schemas.py`: `TradeDecision` + `ActionDecision`
  - **what**: TDD. In `schemas.py`: `ControlledSignal = Literal[...]` (i 18 valori di §6.2,
    preliminari — finalizzati in M3-T06/D4), `ActionDecision` e `TradeDecision` con tutti
    i `model_validator(mode="after")` di §6.2: HOLD/FLAT → size_pct=0, leverage=0,
    entry_type=none, no SL/TP, **no limit_price** (fix A.1); LONG/SHORT → size_pct>0,
    leverage≥1, SL+TP obbligatori (Figma F1), limit_price sse entry_type=limit e assente se
    market; `confidence`∈[0,1] e `time_horizon_min` sempre presenti (inv #7); esattamente 3
    action BTC/ETH/SOL; `extra="forbid"`. Decimal ovunque (inv #12). Coprire gli 8 casi di
    §9.2 (`test_schemas_trade_decision.py`).
  - **prd**: §6.2 (inv #6, #7, #12; Figma F1/F2/F3)
  - **dep**: M1-T01
  - **files**: `src/aiat/domain/schemas.py`, `tests/unit/domain/test_schemas_trade_decision.py`
  - **verify**: `uv run pytest tests/unit/domain/test_schemas_trade_decision.py -q && uv run mypy src`
  - **done-when**: tutti gli 8 casi §9.2 passano (3 action BTC/ETH/SOL, rifiuta 4 action,
    HOLD+size>0, LONG senza SL/TP, limit senza limit_price, signal fuori vocabolario,
    confidence al bordo, confidence fuori [0,1]).

- [x] **M1-T03** — `domain/schemas.py` (cont.): schemi contesto + DTO runtime
  - **what**: TDD. Aggiungere schemi contesto §6.3 (`TechnicalIndicators`,
    `SentimentSnapshot`, `NewsItem`, `OnChainSnapshot`, `PortfolioState`,
    `OpenPositionSummary`, `ContextBundle` con docstring "market context byte-identico
    cross-model", inv #13) + DTO runtime §6.4 (`CostEventData` con `n_attempts` ge=1,
    `LLMInvocationResult`, `GuardrailReport`, tutti i `Field(ge=0)`). Decimal ovunque.
  - **prd**: §6.3, §6.4
  - **dep**: M1-T02
  - **files**: `src/aiat/domain/schemas.py`, `tests/unit/domain/test_pydantic_serialization.py`
  - **verify**: `uv run pytest tests/unit/domain/test_pydantic_serialization.py -q && uv run mypy src`
  - **done-when**: roundtrip JSON `TradeDecision`/`ContextBundle`/`CostEventData` →
    dict → modello è byte-stabile; il test passa.

- [x] **M1-T04** — `domain/exceptions.py`
  - **what**: Gerarchia base delle eccezioni di dominio (es. `AIATError` base +
    `ContextBuildError`, `ExecutionRejectedError`/`ExecutionTimeoutError` se usate dai
    contratti §7.1/§7.5). Type hints completi.
  - **prd**: §7.1, §7.5, §12 M1
  - **dep**: M1-T01
  - **files**: `src/aiat/domain/exceptions.py`
  - **verify**: `uv run python -c "import aiat.domain.exceptions" && uv run mypy src`
  - **done-when**: il modulo importa e mypy è clean.

- [x] **M1-T05** — `db/models/base.py`
  - **what**: `DeclarativeBase` SQLAlchemy 2.x (`class Base(DeclarativeBase): ...`) +
    mixin comuni (es. `TimestampMixin` con `created_at` server_default `now()`). Tipi
    `Mapped[]`. Predisporre `Numeric` per i soldi (inv #12).
  - **prd**: §3.2, §1.2
  - **dep**: M0
  - **files**: `src/aiat/db/models/base.py`
  - **verify**: `uv run python -c "from aiat.db.models.base import Base; print(Base)" && uv run mypy src`
  - **done-when**: `Base` importabile, mypy clean.

> **M1-T06a..T06i** — i **20 modelli SQLAlchemy** di §3.2 (DISCREPANZA #1: §3.2 ha 20
> `CREATE TABLE`, non 17). Split per sezione DDL per restare in 1 context window. Ogni
> sub-task: definisce i modelli con `Mapped[]`, colonne `Numeric` per soldi (inv #12),
> denormalizzazione `experiment_id`/`model_id`/`run_id` dove §5 inv #3 lo richiede, tutti i
> CHECK/UNIQUE/INDEX del DDL; **aggiorna `db/models/__init__.py`** esportando le classi.

- [x] **M1-T06a** — Models §3.2.1: anagrafica/config (3 tabelle)
  - **what**: `experiment.py` (`Experiment`), `model.py` (`Model`, CHECK tier/geography,
    wallet_address UNIQUE, pricing Numeric≥0), `prompt_template.py` (`PromptTemplate`,
    PK sha256_hash, label UNIQUE, controlled_signals JSONB).
  - **prd**: §3.2.1 (inv #3, #12)
  - **dep**: M1-T05, M1-T01
  - **files**: `src/aiat/db/models/{experiment,model,prompt_template}.py`, `src/aiat/db/models/__init__.py`
  - **verify**: `uv run python -c "from aiat.db.models import Experiment, Model, PromptTemplate" && uv run mypy src`
  - **done-when**: i 3 modelli importano e mypy è clean.

- [x] **M1-T06b** — Models §3.2.2: context (2 tabelle)
  - **what**: `context_snapshot.py` (`ContextSnapshot`, UNIQUE `(experiment_id,tick_id)` +
    UNIQUE `(id,experiment_id,tick_id)` per la FK composita, index `tick_at DESC`),
    `context_build_run.py` (`ContextBuildRun`, CHECK status, index parziale su
    failed/timeout/partial).
  - **prd**: §3.2.2
  - **dep**: M1-T06a
  - **files**: `src/aiat/db/models/{context_snapshot,context_build_run}.py`, `__init__.py`
  - **verify**: `uv run python -c "from aiat.db.models import ContextSnapshot, ContextBuildRun" && uv run mypy src`
  - **done-when**: i 2 modelli importano e mypy è clean.

- [x] **M1-T06c** — Models §3.2.3: run + decisioni (4 tabelle)
  - **what**: `run.py` (`Run`, UNIQUE `(experiment_id,model_id,scheduled_for)`, **FK
    composita** `(context_snapshot_id,experiment_id,tick_id)`→context_snapshots, CHECK
    status 7-valori), `llm_invocation.py` (`LLMInvocation`, run_id UNIQUE, Numeric(4,3)
    temperature/top_p), `decision.py` (`Decision`, run_id UNIQUE), `action.py`
    (`DecisionAction`, tutti i CHECK `chk_hold_flat_no_sizing`, `chk_open_close_has_sizing`,
    `chk_limit_requires_price`, `chk_market_no_limit_price`, UNIQUE `(decision_id,symbol)`).
  - **prd**: §3.2.3 (inv #3, #12)
  - **dep**: M1-T06b
  - **files**: `src/aiat/db/models/{run,llm_invocation,decision,action}.py`, `__init__.py`
  - **verify**: `uv run python -c "from aiat.db.models import Run, LLMInvocation, Decision, DecisionAction" && uv run mypy src`
  - **done-when**: i 4 modelli importano (FK composita inclusa) e mypy è clean.

- [x] **M1-T06d** — Models §3.2.4: wallet/posizioni (2 tabelle)
  - **what**: `account_snapshot.py` (`AccountSnapshot`, run_id UNIQUE,
    `portfolio_state_hash`), `position.py` (`Position`, side CHECK LONG/SHORT,
    `opening_action_id` UNIQUE index, `chk_position_closed_consistency`, index parziale
    aperte).
  - **prd**: §3.2.4 (inv #3, #12)
  - **dep**: M1-T06c
  - **files**: `src/aiat/db/models/{account_snapshot,position}.py`, `__init__.py`
  - **verify**: `uv run python -c "from aiat.db.models import AccountSnapshot, Position" && uv run mypy src`
  - **done-when**: i 2 modelli importano e mypy è clean.

- [x] **M1-T06e** — Models §3.2.5: orders (1 tabella)
  - **what**: `order.py` (`Order`, CHECK `order_kind` entry/stop_loss/take_profit/close,
    CHECK status 6-valori, Numeric prezzi/size, index su action/model/status parziale).
  - **prd**: §3.2.5
  - **dep**: M1-T06d
  - **files**: `src/aiat/db/models/order.py`, `__init__.py`
  - **verify**: `uv run python -c "from aiat.db.models import Order" && uv run mypy src`
  - **done-when**: `Order` importa e mypy è clean.

- [x] **M1-T06f** — Models §3.2.6: ledger costi (4 tabelle)
  - **what**: `fee_event.py` (`FeeEvent`, FK order_id+position_id+run_id), `funding_event.py`
    (`FundingEvent`, **niente run_id** — vedi §3.3, CHECK period_end>start),
    `cost_event.py` (`CostEvent`, decision_id UNIQUE, `n_attempts`≥1, Numeric(12,8)),
    `tax_sim.py` (`TaxSimPeriod`, UNIQUE `(experiment_id,model_id,quarter_label)`).
  - **prd**: §3.2.6 (inv #3, #4, #12)
  - **dep**: M1-T06e
  - **files**: `src/aiat/db/models/{fee_event,funding_event,cost_event,tax_sim}.py`, `__init__.py`
  - **verify**: `uv run python -c "from aiat.db.models import FeeEvent, FundingEvent, CostEvent, TaxSimPeriod" && uv run mypy src`
  - **done-when**: i 4 modelli importano e mypy è clean.

- [x] **M1-T06g** — Models §3.2.7: outcomes (1 tabella)
  - **what**: `outcome.py` (`Outcome`, position_id UNIQUE, `opening_action_id`,
    `opening_run_id`+`closing_run_id`, `was_profitable_net`, `decision_action_confidence`,
    `horizon_met`, indici model_time/confidence/action).
  - **prd**: §3.2.7
  - **dep**: M1-T06f
  - **files**: `src/aiat/db/models/outcome.py`, `__init__.py`
  - **verify**: `uv run python -c "from aiat.db.models import Outcome" && uv run mypy src`
  - **done-when**: `Outcome` importa e mypy è clean.

- [x] **M1-T06h** — Models §3.2.8: baseline (2 tabelle)
  - **what**: `baseline_config.py` (`BaselineConfig`, CHECK baseline_name, UNIQUE
    `(experiment_id,baseline_name)`, `config_hash`), `baseline_equity_snapshot.py`
    (`BaselineEquitySnapshot`, FK baseline_config_id, UNIQUE
    `(experiment_id,baseline_name,tick_id)`).
  - **prd**: §3.2.8
  - **dep**: M1-T06g
  - **files**: `src/aiat/db/models/{baseline_config,baseline_equity_snapshot}.py`, `__init__.py`
  - **verify**: `uv run python -c "from aiat.db.models import BaselineConfig, BaselineEquitySnapshot" && uv run mypy src`
  - **done-when**: i 2 modelli importano e mypy è clean.

- [x] **M1-T06i** — Models §3.2.9: errors (1 tabella) + chiusura set 20
  - **what**: `error.py` (`Error`, FK nullable run/decision/experiment/model, index
    model_time/kind). Verificare che `db/models/__init__.py` esporti **tutte e 20** le
    entità (Experiment…Error).
  - **prd**: §3.2.9
  - **dep**: M1-T06h
  - **files**: `src/aiat/db/models/error.py`, `src/aiat/db/models/__init__.py`
  - **verify**: `uv run python -c "import aiat.db.models as m; n=[x for x in ('Experiment','Model','PromptTemplate','ContextSnapshot','ContextBuildRun','Run','LLMInvocation','Decision','DecisionAction','AccountSnapshot','Position','Order','FeeEvent','FundingEvent','CostEvent','TaxSimPeriod','Outcome','BaselineConfig','BaselineEquitySnapshot','Error') if hasattr(m,x)]; assert len(n)==20, n; print('20 models ok')" && uv run mypy src`
  - **done-when**: tutte e 20 le classi modello sono esportate da `aiat.db.models`.

- [x] **M1-T07** — `db/session.py`
  - **what**: Async engine (`create_async_engine`, driver `asyncpg`) + factory
    `AsyncSession` / `async_sessionmaker`. Helper `get_db_session(settings)` (usato da §10.1).
  - **prd**: §1.2
  - **dep**: M1-T05
  - **files**: `src/aiat/db/session.py`
  - **verify**: `uv run python -c "import aiat.db.session" && uv run mypy src`
  - **done-when**: il modulo importa e mypy è clean.

- [x] **M1-T08** — Setup `alembic/`
  - **what**: `alembic init` adattato: `alembic/env.py` async-aware (legge
    `AIAT_DATABASE_URL`, `target_metadata = Base.metadata` importando tutti i modelli),
    `alembic/script.py.mako`, `alembic.ini` a **root** (§2.2; DISCREPANZA #4 confermata).
  - **prd**: §2.2, §3.3
  - **dep**: M1-T06i, M1-T07
  - **files**: `alembic/env.py`, `alembic/script.py.mako`, `alembic.ini`, `alembic/versions/.keep`
  - **verify**: `uv run python -c "from alembic.config import Config; Config('alembic.ini'); import aiat.db.models" && uv run alembic --config alembic.ini history && echo "alembic config ok"`
  - **done-when**: la config Alembic carica e vede `Base.metadata` con i 20 modelli.

- [x] **M1-T09** 🐘 — Migration `001_initial_schema.py`
  - **what**: Generare la migration di bootstrap (autogenerate dai 20 modelli, poi review
    manuale per CHECK condizionali, FK composita runs→context_snapshots, indici parziali,
    UNIQUE). Una sola migration contiene tutto il DDL §3.2.
  - **prd**: §3.2, §12 M1
  - **dep**: M1-T08
  - **files**: `alembic/versions/001_initial_schema.py`
  - **verify**: `ls alembic/versions/001_*.py >/dev/null && uv run python -c "import importlib.util,glob; p=glob.glob('alembic/versions/001_*.py')[0]; s=importlib.util.spec_from_file_location('m',p); mod=importlib.util.module_from_spec(s); s.loader.exec_module(mod); assert hasattr(mod,'upgrade') and hasattr(mod,'downgrade'); print('migration ok')"`
  - **done-when**: la migration esiste con `upgrade()`/`downgrade()` importabili. **Nota
    🐘**: l'`alembic upgrade head` reale (creazione 20 tabelle) è verificato da M1-T11 su
    Postgres effimero.

- [x] **M1-T10** 🐘 — `tests/conftest.py`: fixture `pytest-postgresql`
  - **what**: Fixture come §9.3: `postgresql_proc(port=None)`, `postgresql`, `db_url`
    (applica `alembic upgrade head` sul DB effimero), `db_session` (`AsyncSession`,
    rollback in teardown).
  - **prd**: §9.3
  - **dep**: M1-T09
  - **files**: `tests/conftest.py`
  - **verify**: `uv run python -c "import ast; src=open('tests/conftest.py').read(); ast.parse(src); assert 'postgresql_proc' in src and 'db_url' in src and 'db_session' in src; print('conftest fixtures present')"`
  - **done-when**: la conftest è sintatticamente valida e definisce le fixture
    `postgresql_proc`/`db_url`/`db_session`. **Nota 🐘**: l'istanziazione effettiva (avvio
    cluster) richiede Postgres server e viene esercitata da M1-T11.

- [x] **M1-T11** 🐘 — `tests/integration/test_db_migrations.py`
  - **what**: Test §9.3: upgrade head da vuoto crea **20 tabelle**, tutti i CHECK
    applicati, tutti gli indici, downgrade base + upgrade idempotente. Verifica anche
    presenza colonne `experiment_id`/`model_id`/`run_id` sulle tabelle operative (inv #3).
  - **prd**: §9.3, §12 M1 (inv #3)
  - **dep**: M1-T10
  - **files**: `tests/integration/test_db_migrations.py`
  - **verify**: `uv run pytest tests/integration/test_db_migrations.py -q`
  - **done-when** 🐘: con Postgres server disponibile, `alembic upgrade head` crea 20
    tabelle con tutti i constraint e il downgrade/upgrade è idempotente.

- [x] **M1-T12** — Coverage `domain/` ≥95%
  - **what**: Completare i test unit finché `domain/` raggiunge ≥95% (CI gating §9.1).
  - **prd**: §9.1
  - **dep**: M1-T02, M1-T03
  - **files**: `tests/unit/domain/*`
  - **verify**: `uv run pytest tests/unit/domain --cov=src/aiat/domain --cov-report=term-missing --cov-fail-under=95 -q`
  - **done-when**: coverage `domain/` ≥95%.

> **DoD M1** (gate): test domain+integration verdi; `alembic upgrade head` crea 20 tabelle
> con tutti i constraint; coverage `domain/` ≥95%.

---

## M2 — LLM abstraction + StatsHandler 🤖 LOOP (VCR, no API reali)

> Codice + unit test (mock) scrivibili dal loop. **Chiude D3** (ADR). Parallelizzabile con
> M3 dopo M1. ⚠️ Il record cassette (M2-T12) è **🛑 [HUMAN-GATED]**: serve API key reali +
> rete verso i provider (bloccata dal firewall). Fonte: §7.3, §8, §9.2(llm), §9.4, §12 M2.
>
> **Routing dual-mode (ADR-0008)**: in sviluppo si usa `AIAT_LLM_GATEWAY=openrouter` (1 chiave
> `AIAT_OPENROUTER_API_KEY`, base_url `https://openrouter.ai/api/v1`) riusando
> `OpenAICompatibleClient`; l'esperimento (M6/M7) usa `gateway=direct` (default fail-safe).
> Principio **additivo**: i 4 client nativi (§8) restano implementati e attivi — OpenRouter è
> solo un base_url alternativo scelto a runtime dalla factory, non li rimpiazza.

- [x] **M2-T01** — `llm/exceptions.py`
  - **what**: TDD. Le **6 classi** §8.2: `LLMError` (base), `LLMTimeoutError`,
    `LLMRateLimitError`, `LLMAuthError`, `LLMParsingError`, `LLMUnrecoverableError`
    (`__init__(primary_error, fallback_error)`). (La TASK_MAP dice "5 classi" ma ne elenca
    6 incl. la base — vedi Contraddizioni.)
  - **prd**: §8.2
  - **dep**: M1
  - **files**: `src/aiat/llm/exceptions.py`, `tests/unit/llm/test_exceptions.py`
  - **verify**: `uv run pytest tests/unit/llm/test_exceptions.py -q && uv run mypy src`
  - **done-when**: le 6 eccezioni esistono con la gerarchia corretta e il test passa.

- [x] **M2-T02** — `llm/base.py`: `BaseLLMClient` ABC
  - **what**: ABC completo §7.3 (`provider`, `model_name_api`, `async def invoke(prompt, *,
    timeout_seconds=90) -> LLMInvocationResult`, docstring con semantica fallback/cost/
    nuisance).
  - **prd**: §7.3
  - **dep**: M2-T01, M1-T03
  - **files**: `src/aiat/llm/base.py`
  - **verify**: `uv run python -c "from aiat.llm.base import BaseLLMClient" && uv run mypy src`
  - **done-when**: l'ABC importa e mypy è clean.

- [x] **M2-T03** — `llm/structured.py`: `invoke_structured` + parser
  - **what**: TDD. `invoke_structured` con **fallback selettivo solo parsing** (NON
    timeout/rate/auth), `_extract_json_balanced` (state machine NORMAL/IN_STRING/
    IN_STRING_ESCAPE, §8.2 fix B.9), `_is_parsing_error`/`_is_rate_limit_error`/
    `_is_auth_error` (versione string-match iniziale, raffinata in M2-T04), `FALLBACK_SUFFIX`.
    Coprire §9.2: well-formed, nested, prose-surrounding, fallback-after-failure,
    unrecoverable.
  - **prd**: §8.2 (inv #6 via validazione)
  - **dep**: M2-T01
  - **files**: `src/aiat/llm/structured.py`, `tests/unit/llm/test_structured_parser.py`
  - **verify**: `uv run pytest tests/unit/llm/test_structured_parser.py -q && uv run mypy src`
  - **done-when**: i casi parser di §9.2 passano (estrazione bilanciata + fallback +
    `LLMUnrecoverableError`).

- [x] **M2-T04** **[D3]** — Raffina classificazione eccezioni (isinstance) + **ADR**
  - **what**: TDD. Raffinare `_is_rate_limit_error`/`_is_auth_error`: **isinstance()
    primario** su classi SDK ufficiali (`openai.RateLimitError`,
    `anthropic.RateLimitError`, `openai.AuthenticationError`, ecc.) + string-match
    fallback (§8.2 fix B.18). **Creare l'ADR** (prossimo numero progressivo, slug
    `exception-classification`) in `docs/decisions/` dal template `0000-template.md`,
    citando D3 e §15.4; aggiornare `docs/decisions/README.md`.
  - **prd**: §8.2, §15.4 (chiude **D3**)
  - **dep**: M2-T03
  - **files**: `src/aiat/llm/structured.py`, `tests/unit/llm/test_exception_classification.py`, `docs/decisions/000N-exception-classification.md`, `docs/decisions/README.md`
  - **verify**: `uv run pytest tests/unit/llm/test_exception_classification.py -q && ls docs/decisions/*-exception-classification.md >/dev/null 2>&1 && grep -q exception-classification docs/decisions/README.md`
  - **done-when**: i test classificano via isinstance() le eccezioni mockate dei 4 SDK e
    l'ADR D3 esiste ed è indicizzato.

- [x] **M2-T05** — `llm/stats_handler.py`: `StatsCallbackHandler`
  - **what**: TDD. `StatsCallbackHandler(AsyncCallbackHandler)` §8.3: aggregazione
    multi-tentativo (`n_attempts`), `on_llm_end` estrae usage per provider, `_extract_usage`
    (OpenAI/Anthropic/DeepSeek-R1/Qwen), `build_cost_event()` con **Decimal precision**
    (inv #12). Coprire §9.2: usage extraction 4 provider + `cost_usd` Decimal.
    **ADR-0008**: gli unit test con **risposte sintetiche** nei formati token **nativi**
    (OpenAI `prompt_tokens`/`completion_tokens`; Anthropic `input_tokens`/`output_tokens`;
    DeepSeek-R1 `reasoning_tokens`; Qwen) sono la **PRIMARY coverage** di quei formati: in
    modalità `openrouter` le cassette (T11/T12) esercitano solo il percorso OpenAI-style e
    **non** i formati nativi. Questo task **non** dipende dal gateway.
  - **prd**: §8.3 (inv #12)
  - **dep**: M2-T01, M1-T03
  - **files**: `src/aiat/llm/stats_handler.py`, `tests/unit/llm/test_stats_handler.py`
  - **verify**: `uv run pytest tests/unit/llm/test_stats_handler.py -q && uv run mypy src`
  - **done-when**: usage extraction per i 4 provider (formati nativi via risposte
    **sintetiche**, ADR-0008) + `cost_usd` Decimal corretto; test passa.

- [x] **M2-T06** — `llm/openai_client.py`
  - **what**: TDD (unit, langchain mockato). `OpenAIClient(BaseLLMClient)` su
    `langchain-openai`, usa `invoke_structured` + `StatsCallbackHandler`, popola
    `LLMInvocationResult` (nuisance snapshot).
  - **prd**: §8.1
  - **dep**: M2-T02, M2-T03, M2-T05
  - **files**: `src/aiat/llm/openai_client.py`, `tests/unit/llm/test_openai_client.py`
  - **verify**: `uv run pytest tests/unit/llm/test_openai_client.py -q && uv run mypy src`
  - **done-when**: il client implementa l'ABC; unit test (mock) verde.

- [x] **M2-T07** — `llm/anthropic_client.py`
  - **what**: TDD (unit, mock). `AnthropicClient(BaseLLMClient)` su `langchain-anthropic`.
  - **prd**: §8.1
  - **dep**: M2-T02, M2-T03, M2-T05
  - **files**: `src/aiat/llm/anthropic_client.py`, `tests/unit/llm/test_anthropic_client.py`
  - **verify**: `uv run pytest tests/unit/llm/test_anthropic_client.py -q && uv run mypy src`
  - **done-when**: il client implementa l'ABC; unit test (mock) verde.

- [x] **M2-T08** — `llm/openai_compatible_client.py`
  - **what**: TDD (unit, mock). `OpenAICompatibleClient(BaseLLMClient)` con `base_url`
    custom (DeepSeek `https://api.deepseek.com/v1`, Qwen
    `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`).
  - **prd**: §8.1
  - **dep**: M2-T02, M2-T03, M2-T05
  - **files**: `src/aiat/llm/openai_compatible_client.py`, `tests/unit/llm/test_openai_compatible_client.py`
  - **verify**: `uv run pytest tests/unit/llm/test_openai_compatible_client.py -q && uv run mypy src`
  - **done-when**: il client implementa l'ABC con base_url override; unit test (mock) verde.

- [x] **M2-T09** — `llm/factory.py`: `load_llm`
  - **what**: TDD (unit, mock). `load_llm(settings: AgentSettings) -> BaseLLMClient`
    (**tipizzato su `AgentSettings`, NON `BaseAIATSettings`** — least privilege, fix B.17).
    **Dual-mode (ADR-0008)**, switch su `settings.llm_gateway`: con `gateway="openrouter"`
    ritorna `OpenAICompatibleClient` (`base_url="https://openrouter.ai/api/v1"`, chiave
    `AIAT_OPENROUTER_API_KEY`, model name in convenzione OpenRouter, es. `"openai/gpt-4o"`,
    `"anthropic/claude-3.5-sonnet"`); con `gateway="direct"` (**default**) il dispatch nativo
    §8.1 resta **invariato** — `match` su `settings.llm_provider` → `OpenAIClient` /
    `AnthropicClient` / `OpenAICompatibleClient` (deepseek|qwen). Principio **additivo**:
    entrambi i rami coesistono, **nessun client rimosso**.
  - **prd**: §8.1 (fix B.17) + ADR-0008
  - **dep**: M2-T06, M2-T07, M2-T08 — il campo `llm_gateway` di `AgentSettings` è definito in
    **M5** (`config/settings.py`); per M2 il test usa un `settings` mockato/minimale con
    l'attributo `gateway` (`direct`/`openrouter`).
  - **files**: `src/aiat/llm/factory.py`, `tests/unit/llm/test_factory.py`
  - **verify**: `uv run pytest tests/unit/llm/test_factory.py -q && uv run mypy src`
  - **done-when**: il dispatch ritorna il client corretto in **entrambe** le modalità —
    `direct` per i 4 provider (openai/anthropic/deepseek/qwen) **e** `openrouter`
    (`OpenAICompatibleClient` con base_url OpenRouter).

- [x] **M2-T10** — `config/model_pricing.yaml`
  - **what**: Creare `src/aiat/config/model_pricing.yaml` con pricing USD/1M token per i 4
    modelli (struttura §8.4: `input`/`output`/`reasoning`). I nomi esatti dei modelli (D1)
    sono deferiti a M7: qui struttura + valori correnti/placeholder. Helper
    `load_pricing_for_model()` se utile a §10.1 A4.
    **Nota (ADR-0008)**: in modalità `openrouter` i nomi modello seguono la convenzione
    OpenRouter (`"provider/model"`), in `direct` i nomi nativi; il pricing deve poter mappare
    **entrambi** (o almeno documentare che i nomi OpenRouter vanno aggiunti alla mappa).
  - **prd**: §8.4
  - **dep**: M1
  - **files**: `src/aiat/config/model_pricing.yaml`
  - **verify**: `uv run python -c "import yaml; d=yaml.safe_load(open('src/aiat/config/model_pricing.yaml')); assert 'models' in d and len(d['models'])>=4, d; print('pricing ok')"`
  - **done-when**: YAML valido con ≥4 modelli, ciascuno con input/output/reasoning.

- [x] **M2-T11** — `tests/integration/test_llm_providers.py` + config VCR
  - **what**: Scrivere il test file con le **15 funzioni** di §9.4 (4 structured success,
    fallback, unrecoverable, timeout, rate-limit, auth, cost-tracking ×2, cost-aggregation,
    3 reasoning-trace) marcate con `@pytest.mark.vcr`, e aggiungere la **config VCR** a
    `tests/conftest.py` (`cassette_library_dir="tests/cassettes"`, `record_mode="none"`,
    `filter_headers=["authorization","x-api-key"]`, `match_on` §9.4). [Merge di TASK_MAP
    M2-T11(codice) + M2-T12.] Il loop scrive il codice; **non** registra cassette.
    **Nota (ADR-0008)**: le cassette saranno registrate via **OpenRouter**
    (`gateway=openrouter`, T12), quindi i test girano contro il percorso
    `OpenAICompatibleClient` e le risposte nelle cassette sono in **formato OpenAI-style**.
    La config VCR e il `match_on` restano invariati.
  - **prd**: §9.4
  - **dep**: M2-T03, M2-T05, M2-T09
  - **files**: `tests/integration/test_llm_providers.py`, `tests/conftest.py`, `tests/cassettes/.keep`
  - **verify**: `uv run pytest tests/integration/test_llm_providers.py --collect-only -q`
  - **done-when**: pytest **colleziona** le 15 funzioni (la collection non richiede
    cassette); config VCR presente. L'esecuzione reale è M2-T12.

- [ ] **M2-T12** 🛑 **[HUMAN-GATED]** — Record 15 cassette VCR
  - **what**: Registrare le 15 cassette in `tests/cassettes/` **via OpenRouter**
    (`AIAT_LLM_GATEWAY=openrouter`, `record_mode="once"`), **una volta sola** (~$0.01), sotto
    **supervisione umana**. **Decisione ADR-0008 (Strada 2, scelta accademica)**: le cassette
    le registra l'**umano** sotto supervisione (**non** il loop in autonomia), per controllo e
    verificabilità dei dati di test — sono artefatti sperimentali della tesi. Resta
    human-gated, ma ora basta **una** chiave (`AIAT_OPENROUTER_API_KEY`) e **un** dominio
    (`openrouter.ai`, da aprire nel firewall) invece dei 4 provider separati.
  - **prd**: §9.4 + ADR-0008
  - **dep**: M2-T11
  - **files**: `tests/cassettes/*.yaml`
  - **human-action**: (1) aprire `openrouter.ai` nel firewall del devcontainer
    (`init-firewall.sh`); (2) impostare `AIAT_OPENROUTER_API_KEY` e
    `AIAT_LLM_GATEWAY=openrouter` in `.env`; (3) eseguire con record_mode=once (es.
    `VCR_RECORD_MODE=once uv run pytest tests/integration/test_llm_providers.py`); (4)
    verificare le risposte registrate; (5) committare le cassette (header `authorization`/
    `x-api-key` filtrati da VCR).
  - **verify**: `test -n "$AIAT_OPENROUTER_API_KEY" && ls tests/cassettes/*.yaml >/dev/null 2>&1 && uv run pytest tests/integration/test_llm_providers.py -q`
  - **loop-rule**: se il `verify:` non è soddisfacibile (no `AIAT_OPENROUTER_API_KEY` / no
    rete verso `openrouter.ai` / no cassette) **non fingere il completamento**: lascia il task
    non spuntato, annota in `progress/log.md` "M2-T12 HUMAN-GATED, blocco qui (record VCR via
    OpenRouter richiede chiave reale + rete)". Se è l'unico task eseguibile rimasto, stampa
    `RALPH_BLOCKED`.
  - **done-when**: le 15 cassette esistono e `pytest test_llm_providers.py` passa in replay
    (`record_mode=none`).
  - **nota**: queste sono cassette di **sviluppo** (formato OpenRouter/OpenAI-style, percorso
    `OpenAICompatibleClient`); le cassette dei provider **diretti** per l'esperimento si
    registrano a **M6** (ADR-0008).

- [x] **M2-T13** — Coverage `llm/` ≥95%
  - **what**: Completare i test unit (mock) finché `llm/` raggiunge ≥95% misurato **solo su
    unit** (il gating CI core §9.6 usa `tests/unit/{domain,llm,execution}`, non integration).
  - **prd**: §9.1
  - **dep**: M2-T03, M2-T05, M2-T06, M2-T07, M2-T08, M2-T09
  - **files**: `tests/unit/llm/*`
  - **verify**: `uv run pytest tests/unit/llm --cov=src/aiat/llm --cov-report=term-missing --cov-fail-under=95 -q`
  - **done-when**: coverage `llm/` ≥95% dai soli unit test.

> **DoD M2** (gate): unit llm verdi; 4 client nativi + factory **dual-mode**
> (`AIAT_LLM_GATEWAY` {direct(default), openrouter}, ADR-0008) + stats_handler completi;
> coverage `llm/` ≥95%; ADR D3 creato. Punto attenzione umano: record cassette **via
> OpenRouter** (M2-T12).

---

## M3 — ContextOrchestrator + collectors 🤖 LOOP (codice+unit) / ⚠️ smoke reale

> Codice + unit (httpx mock) + integration (Postgres effimero 🐘). **Chiude D4 e D5**
> (ADR). Parallelizzabile con M2 dopo M1. Lo smoke reale (M3-T11) è ⚠️ ZONA-GRIGIA (rete
> bloccata). Fonte: §7.1, §7.2, §6.3, §12 M3.

- [x] **M3-T01** — `context/collectors/base.py`: `BaseCollector` ABC
  - **what**: `BaseCollector(ABC, Generic[T])` §7.2: `timeout_seconds`, `cache_ttl_seconds`,
    `async def collect() -> T`, eccezioni `CollectorTimeoutError`/`CollectorSourceError`.
  - **prd**: §7.2
  - **dep**: M1
  - **files**: `src/aiat/context/collectors/base.py`
  - **verify**: `uv run python -c "from aiat.context.collectors.base import BaseCollector" && uv run mypy src`
  - **done-when**: l'ABC importa e mypy è clean.

- [x] **M3-T02** — `context/collectors/technical.py`
  - **what**: TDD (httpx mock). Indicatori tecnici (RSI, MACD, EMA20/50, Bollinger, ATR,
    volume) → `TechnicalIndicators`, porting da `legacy/v1/indicators.py` usando
    `pandas-ta`, Decimal in output (inv #12).
  - **prd**: §2.2, §1.3, §6.3
  - **dep**: M3-T01
  - **files**: `src/aiat/context/collectors/technical.py`, `tests/unit/context/test_technical.py`
  - **verify**: `uv run pytest tests/unit/context/test_technical.py -q && uv run mypy src`
  - **done-when**: il collector produce `TechnicalIndicators` da candele mockate; test verde.

- [x] **M3-T03** — `context/collectors/sentiment.py`
  - **what**: TDD (httpx mock). `SentimentCollector` Fear&Greed → `SentimentSnapshot`
    (index 0-100, label, fetched_at). Espone `collect()` (usato da §10.1 O4).
  - **prd**: §2.2, §6.3
  - **dep**: M3-T01
  - **files**: `src/aiat/context/collectors/sentiment.py`, `tests/unit/context/test_sentiment.py`
  - **verify**: `uv run pytest tests/unit/context/test_sentiment.py -q && uv run mypy src`
  - **done-when**: produce `SentimentSnapshot` da risposta F&G mockata; test verde.

- [x] **M3-T04** **[D5]** — `context/collectors/news.py` + **ADR**
  - **what**: TDD (httpx mock). `NewsCollector` RSS (CryptoPanic, CoinDesk) → `list[NewsItem]`,
    `check_sources_reachability()` (usato da §10.1 O3). **Decidere** numero items/tick +
    lista RSS finale; **creare ADR** (prossimo numero, slug `rss-sources`) citando D5/§15.4;
    aggiornare `docs/decisions/README.md`.
  - **prd**: §2.2, §15.4 (chiude **D5**)
  - **dep**: M3-T01
  - **files**: `src/aiat/context/collectors/news.py`, `tests/unit/context/test_news.py`, `docs/decisions/000N-rss-sources.md`, `docs/decisions/README.md`
  - **verify**: `uv run pytest tests/unit/context/test_news.py -q && ls docs/decisions/*-rss-sources.md >/dev/null 2>&1 && grep -q rss-sources docs/decisions/README.md`
  - **done-when**: il collector parsa RSS mockato in `NewsItem` e l'ADR D5 (count+sources)
    esiste ed è indicizzato.

- [x] **M3-T05** — `context/collectors/onchain.py`
  - **what**: TDD (httpx mock). `OnchainCollector` (funding rate, OI, liquidations via HL
    info endpoint pubblico, read-only) → `list[OnChainSnapshot]`. Includere
    `HLPublicInfoClient` con `fetch_meta()` (usato da §10.1 O2).
  - **prd**: §2.2, §6.3
  - **dep**: M3-T01
  - **files**: `src/aiat/context/collectors/onchain.py`, `tests/unit/context/test_onchain.py`
  - **verify**: `uv run pytest tests/unit/context/test_onchain.py -q && uv run mypy src`
  - **done-when**: produce `OnChainSnapshot` da risposta HL-info mockata; test verde.

- [x] **M3-T06** **[D4]** — `context/controlled_signals.py` + **ADR**
  - **what**: Definire il vocabolario controllato finale (`CONTROLLED_SIGNALS`) e
    garantire che **combaci** con `ControlledSignal = Literal[...]` in
    `domain/schemas.py` (M1-T02). **Creare ADR** (prossimo numero, slug
    `controlled-signals`) citando D4/§15.4; aggiornare `README.md`. Test che asserisce
    `set(ControlledSignal args) == set(CONTROLLED_SIGNALS)`.
  - **prd**: §6.2, §3.2.1, §15.4 (inv #6, chiude **D4**)
  - **dep**: M1-T02
  - **files**: `src/aiat/context/controlled_signals.py`, `tests/unit/context/test_controlled_signals.py`, `docs/decisions/000N-controlled-signals.md`, `docs/decisions/README.md`
  - **verify**: `uv run pytest tests/unit/context/test_controlled_signals.py -q && ls docs/decisions/*-controlled-signals.md >/dev/null 2>&1 && grep -q controlled-signals docs/decisions/README.md`
  - **done-when**: vocabolario allineato al `Literal` di schemas + ADR D4 esiste/indicizzato.

- [x] **M3-T07** — `context/builder.py`: `ContextBuilder`
  - **what**: TDD (collectors mockati). `ContextBuilder` compone i 4 collector in un
    `ContextBundle`, calcola `source_timestamps`. Fetch parallelo con timeout per source
    (§4.1 CO.1).
  - **prd**: §6.3, §4.1
  - **dep**: M3-T02, M3-T03, M3-T04, M3-T05
  - **files**: `src/aiat/context/builder.py`, `tests/unit/context/test_builder.py`
  - **verify**: `uv run pytest tests/unit/context/test_builder.py -q && uv run mypy src`
  - **done-when**: con collectors mockati produce un `ContextBundle` valido; test verde.

- [x] **M3-T08** 🐘 — `db/repositories/context_build.py`: `ContextBuildRepository`
  - **what**: `ContextBuildRepository` §7.6 (fix B.5): `start_build`, `complete_build`
    (persiste context_snapshots + aggiorna context_build_runs in 1 txn), `fail_build`,
    `get_snapshot_for_tick`. **No commit interno** (UoW dell'orchestrator). Integration test
    su Postgres effimero (success + partial + failed).
  - **prd**: §7.6
  - **dep**: M1-T06i
  - **files**: `src/aiat/db/repositories/context_build.py`, `tests/integration/test_db_repositories_context_build.py`
  - **verify**: `uv run pytest tests/integration/test_db_repositories_context_build.py -q`
  - **done-when** 🐘: con Postgres, le 4 operazioni scrivono/leggono correttamente
    context_snapshots + context_build_runs.

- [x] **M3-T09** 🐘 — `orchestration/context_orchestrator.py`
  - **what**: Entrypoint del 5° servizio §7.1: `build_tick_context(tick_id, experiment_id)`
    compone `ContextBuilder` + `ContextBuildRepository`, gestisce fallimenti parziali →
    status, scrive **sempre** una context_build_runs row. Integration test su Postgres
    effimero (incl. fallimenti parziali).
  - **prd**: §7.1
  - **dep**: M3-T07, M3-T08
  - **files**: `src/aiat/orchestration/context_orchestrator.py`, `tests/integration/test_context_orchestrator.py`
  - **verify**: `uv run pytest tests/integration/test_context_orchestrator.py -q`
  - **done-when** 🐘: scrive context_snapshots + context_build_runs anche su fallimenti
    parziali; test verde.

- [x] **M3-T10** — Unit test collectors (aggregato)
  - **what**: Garantire che `tests/unit/context/` copra ogni collector (httpx mock). Colmare
    eventuali gap lasciati da T02-T05.
  - **prd**: §12 M3
  - **dep**: M3-T02, M3-T03, M3-T04, M3-T05
  - **files**: `tests/unit/context/*`
  - **verify**: `uv run pytest tests/unit/context -q`
  - **done-when**: tutti gli unit test dei collector passano.

- [ ] **M3-T11** ⚠️ **[HUMAN-GATED]** — Smoke reale orchestrator
  - **what**: Verifica §12 M3 reale: `python -m aiat` con `AIAT_SERVICE_ROLE=
    context_orchestrator` contro fonti reali (RSS/F&G/HL-info) + Postgres genera
    context_snapshot. Il loop non può: firewall blocca le fonti.
  - **prd**: §12 M3
  - **dep**: M3-T09
  - **files**: — (smoke run su codice esistente; nessun file nuovo)
  - **human-action**: eseguire da rete senza firewall, con `AIAT_DATABASE_URL` su un
    Postgres reale; osservare ≥1 (idealmente 4 in 1h) row in `context_snapshots`.
  - **verify**: `test -n "$AIAT_DATABASE_URL" && uv run python -m aiat` (role=context_orchestrator, contro fonti reali) `&& echo "verifica manuale: SELECT count(*) FROM context_snapshots"`
  - **loop-rule**: non soddisfacibile nel loop (rete/Postgres) → lascia non spuntato, annota
    "M3-T11 HUMAN-GATED/⚠️, blocco qui (smoke reale richiede rete+DB)". Se unico task
    rimasto, `RALPH_BLOCKED`.
  - **done-when**: l'orchestrator reale produce context_snapshot contro fonti vere.

> **DoD M3** (gate, parte loop): collectors + builder + orchestrator +
> ContextBuildRepository completi; unit (mock) + integration (Postgres) verdi; ADR D4 e D5
> creati.

---

## M4 — ExecutionLayer + guardrails 🤖 LOOP (codice+unit) / 🛑 e2e testnet

> Guardrails + sizing + outcome_resolver: logica pura, loop al 100% (unit + integration
> 🐘). **Chiude D2** (ADR). Fonte: §7.4, §7.5, §4.2, §9.2(execution), §12 M4.

> ```
> ╔══════════════════════════════════════════════════════════════════════╗
> ║  🛑🛑🛑  M4-T08 È IL PRIMO STOP FISICO INDEROGABILE  🛑🛑🛑            ║
> ║  Richiede un wallet Hyperliquid testnet REALE, fundato via faucet,     ║
> ║  con chiave privata in .env. Il loop NON può e NON deve simularlo.     ║
> ╚══════════════════════════════════════════════════════════════════════╝
> ```

- [x] **M4-T01** — `execution/sizing.py`
  - **what**: TDD. Sizing posizioni in **Decimal** (es. `notional = price * size_units *
    leverage`), **mai `float`** (inv #12). Coprire §9.2: precisione Decimal, nessuna
    aritmetica float.
  - **prd**: §9.2 (inv #12)
  - **dep**: M1
  - **files**: `src/aiat/execution/sizing.py`, `tests/unit/execution/test_sizing.py`
  - **verify**: `uv run pytest tests/unit/execution/test_sizing.py -q && uv run mypy src`
  - **done-when**: i calcoli usano solo Decimal e i test di precisione passano.

- [x] **M4-T02** — `execution/guardrails.py`: 4 guardrail Strategia C+
  - **what**: TDD. I 4 guardrail §7.4 **in ordine** (1 SL/TP mandatory → 2 size_pct clamp ≤
    max_size_pct → 3 leverage clamp ≤ min(1+conf×9, hard_max) → 4 confidence<min →
    force HOLD), mai disattivabili (inv #8), `GuardrailReport` per action con
    `original_side` se `forced_hold`. Coprire tutti i casi §9.2 (HOLD se no SL, size clamp
    0.20, leverage clamp, confidence<0.4→HOLD, ordine, report).
  - **prd**: §7.4 (inv #8; Figma F1/F3)
  - **dep**: M1-T02
  - **files**: `src/aiat/execution/guardrails.py`, `tests/unit/execution/test_guardrails.py`
  - **verify**: `uv run pytest tests/unit/execution/test_guardrails.py -q && uv run mypy src`
  - **done-when**: tutti i casi guardrail §9.2 passano nell'ordine corretto, con report.

- [x] **M4-T03** — `execution/hyperliquid_client.py` (ABC + mock)
  - **what**: TDD (HL mockato). ABC `HyperliquidClient` §7.5 (`fetch_portfolio_state`,
    `execute_action(action, run_id, current_position)`, `check_position_closure`) +
    `OrderResult`/`PositionClosureInfo`. Semantica LONG/SHORT/FLAT/HOLD (FLAT = close-only se
    posizione esiste). Refactor da `legacy/v1/hyperliquid_trader.py`. **Niente chiamate
    reali** (testnet = M4-T08).
  - **prd**: §7.5
  - **dep**: M1, M2-T01
  - **files**: `src/aiat/execution/hyperliquid_client.py`, `tests/unit/execution/test_hyperliquid_client.py`
  - **verify**: `uv run pytest tests/unit/execution/test_hyperliquid_client.py -q && uv run mypy src`
  - **done-when**: l'ABC + semantica side è implementata e testata con HL mockato.

- [x] **M4-T04** **[D2]** — `execution/outcome_resolver.py` + **ADR**
  - **what**: TDD. `OutcomeResolver` §4.2: risoluzione outcomes (pnl_net_fee,
    pnl_net_fee_funding, was_profitable_net, horizon_met). **Decidere la regola di labeling
    HOLD/FLAT (controfattuale)** e **creare ADR** (prossimo numero, slug `holdflat-outcome`)
    citando D2/§15.4; aggiornare `README.md`. (Critico: prima dell'analisi confidence/Brier.)
  - **prd**: §4.2, §15.4 (chiude **D2**)
  - **dep**: M1-T06i
  - **files**: `src/aiat/execution/outcome_resolver.py`, `tests/unit/execution/test_outcome_resolver.py`, `docs/decisions/000N-holdflat-outcome.md`, `docs/decisions/README.md`
  - **verify**: `uv run pytest tests/unit/execution/test_outcome_resolver.py -q && ls docs/decisions/*-holdflat-outcome.md >/dev/null 2>&1 && grep -q holdflat-outcome docs/decisions/README.md`
  - **done-when**: la regola HOLD/FLAT è implementata e testata; ADR D2 esiste/indicizzato.

- [x] **M4-T05** 🐘 — `db/repositories/positions.py`: `PositionsRepository`
  - **what**: `PositionsRepository` §7.6: `open_position` crea positions+orders+fee_events in
    1 txn; `close_position` → aggiorna position + crea outcomes (FK opening/closing_run_id);
    `opening_action_id` UNIQUE; `list_open_for_model`. No commit interno. Integration test
    su Postgres effimero.
  - **prd**: §7.6
  - **dep**: M1-T06i
  - **files**: `src/aiat/db/repositories/positions.py`, `tests/integration/test_db_repositories_positions.py`
  - **verify**: `uv run pytest tests/integration/test_db_repositories_positions.py -q`
  - **done-when** 🐘: open→close→outcomes funziona; `opening_action_id` duplicato →
    IntegrityError.

- [x] **M4-T06** — Coverage unit `execution/` (guardrails+sizing+resolver)
  - **what**: Completare gli unit test (guardrails, sizing, outcome_resolver,
    hyperliquid_client mock) per coprire i rami; preparare il gate 95% (M4-T09).
  - **prd**: §9.2
  - **dep**: M4-T01, M4-T02, M4-T03, M4-T04
  - **files**: `tests/unit/execution/*`
  - **verify**: `uv run pytest tests/unit/execution -q`
  - **done-when**: tutti gli unit test `execution/` passano.

- [x] **M4-T07** 🐘 — `tests/integration/test_db_repositories_positions.py` (estensione)
  - **what**: Completare lo scenario integration: open → close → outcomes con Postgres
    effimero, incluse fee_events e FK run_id, e i casi di errore (CHECK
    chk_position_closed_consistency).
  - **prd**: §9.3
  - **dep**: M4-T05
  - **files**: `tests/integration/test_db_repositories_positions.py`
  - **verify**: `uv run pytest tests/integration/test_db_repositories_positions.py -q`
  - **done-when** 🐘: lo scenario completo è verde su Postgres effimero.

- [ ] **M4-T08** 🛑 **[HUMAN-GATED]** — e2e su wallet HL testnet REALE  ← **PRIMO STOP FISICO**
  - **what**: Verifica §12 M4: smoke su wallet testnet apre LONG BTC con SL/TP, lo chiude,
    verifica `outcomes.pnl_net_fee_funding_usd` corretto. Il loop **non può**: serve wallet
    HL testnet reale fundato + chiave in `.env`; il firewall blocca anche Hyperliquid.
  - **prd**: §12 M4
  - **dep**: M4-T03, M4-T05
  - **files**: `tests/e2e/test_testnet_smoke.py` (marker `@pytest.mark.testnet`)
  - **human-action**: creare un wallet Hyperliquid **testnet**, fundarlo via faucet (1000$
    testnet), impostare `AIAT_HL_WALLET_PRIVATE_KEY` + `AIAT_HL_WALLET_ADDRESS` +
    `AIAT_NETWORK=testnet` in `.env` (MAI mainnet — inv #9), poi eseguire il test.
  - **verify**: `test -n "$AIAT_HL_WALLET_PRIVATE_KEY" && uv run pytest tests/e2e/test_testnet_smoke.py -m testnet -q`
  - **loop-rule**: **NON simulare**. Verify non soddisfacibile (manca la chiave reale,
    rete bloccata) → lascia non spuntato, annota in `progress/log.md` "M4-T08 HUMAN-GATED 🛑
    PRIMO STOP FISICO, blocco qui (richiede wallet testnet reale)". Se è l'unico task
    rimasto, stampa `RALPH_BLOCKED`.
  - **done-when**: con wallet testnet reale, apre/chiude LONG BTC e `outcomes` registra il
    PnL netto corretto.

- [x] **M4-T09** — Coverage `execution/` ≥95%
  - **what**: Portare `execution/` a ≥95% (gate CI core §9.6) dai soli unit test.
  - **prd**: §9.1
  - **dep**: M4-T06
  - **files**: `tests/unit/execution/*`
  - **verify**: `uv run pytest tests/unit/execution --cov=src/aiat/execution --cov-report=term-missing --cov-fail-under=95 -q`
  - **done-when**: coverage `execution/` ≥95%.

> **DoD M4** (gate, parte loop): guardrails+sizing+hyperliquid_client(ABC+mock)+
> outcome_resolver+PositionsRepository completi; unit+integration verdi; coverage
> `execution/` ≥95%; ADR D2 creato. 🛑 e2e testnet (M4-T08): STOP fisico umano.

---

## M5 — Decision loop e2e + isolation/parity 🤖 LOOP (codice+e2e mock) / 🛑 chiusura reale

> Il loop scrive decision_loop, scheduler, lifecycle, repository residui e gli e2e
> **mockati** (LLM cassette + HL mock + Postgres effimero 🐘). Fonte: §4.1, §7.6, §10.1,
> §10.3, §9.5, §9.7, §12 M5.

- [x] **M5-T01** 🐘 — `db/repositories/decisions.py`: `DecisionsRepository`
  - **what**: `DecisionsRepository` §7.6 con **transazione atomica** (`persist_decision`:
    decisions + decision_actions(3) + cost_events + llm_invocations in 1 commit, inv #4;
    `flush()` per gli ID ma **no commit/rollback interno**, fix B.6). Integration test su
    Postgres effimero: atomica, rollback su action invalida, CHECK, FK composita.
  - **prd**: §7.6 (inv #4, #11)
  - **dep**: M1-T06i, M2-T05
  - **files**: `src/aiat/db/repositories/decisions.py`, `tests/integration/test_db_repositories_decisions.py`
  - **verify**: `uv run pytest tests/integration/test_db_repositories_decisions.py -q`
  - **done-when** 🐘: persistenza atomica verde; rollback completo se un'action fallisce.

> **M5-T02a..c** — Repository residui §7.6 (DISCREPANZA #2: si segue §7.6, non §2.2; **NON**
> si crea `ledger.py` — §7.6 non definisce un LedgerRepository, i cost_events sono gestiti da
> `DecisionsRepository` per inv #4). Split per restare in 1 context window.

- [x] **M5-T02a** 🐘 — `snapshots.py` + `runs.py`
  - **what**: `SnapshotsRepository` (`persist_account_snapshot` con portfolio_state_hash,
    `get_context_snapshot`) + `RunsRepository` (`create_run`, `update_status`, `log_error`).
    No commit interno. Integration test ciascuno.
  - **prd**: §7.6
  - **dep**: M1-T06i
  - **files**: `src/aiat/db/repositories/{snapshots,runs}.py`, `tests/integration/test_db_repositories_snapshots_runs.py`
  - **verify**: `uv run pytest tests/integration/test_db_repositories_snapshots_runs.py -q`
  - **done-when** 🐘: account/context snapshot + lifecycle run verdi su Postgres effimero.

- [x] **M5-T02b** 🐘 — `outcomes.py`
  - **what**: `OutcomesRepository` §7.6 (`persist_outcome`, `list_for_model_in_window`).
    Integration test.
  - **prd**: §7.6
  - **dep**: M1-T06i
  - **files**: `src/aiat/db/repositories/outcomes.py`, `tests/integration/test_db_repositories_outcomes.py`
  - **verify**: `uv run pytest tests/integration/test_db_repositories_outcomes.py -q`
  - **done-when** 🐘: persist/list outcomes verde su Postgres effimero.

- [x] **M5-T02c** 🐘 — `baselines.py` + `tax_simulation.py`
  - **what**: `BaselineRepository` (`register_baseline_config` con config_hash,
    `get_baseline_config`, `persist_equity_snapshot`, `list_equity_history`) +
    `TaxSimulationRepository` (`compute_and_persist_period` con compensazione algebrica
    `max(0, gross-fees-funding)`, tax 0.26; `list_for_model`). Integration test.
  - **prd**: §7.6, §3.2.8, §4.3
  - **dep**: M1-T06i
  - **files**: `src/aiat/db/repositories/{baselines,tax_simulation}.py`, `tests/integration/test_db_repositories_baselines_tax.py`
  - **verify**: `uv run pytest tests/integration/test_db_repositories_baselines_tax.py -q`
  - **done-when** 🐘: baseline configs/equity + tax_sim_periods verdi su Postgres effimero.

- [x] **M5-T03** — `config/settings.py`: Settings per ruolo (least privilege)
  - **what**: TDD. `BaseAIATSettings` + `AgentSettings` + `ContextOrchestratorSettings`
    (§10.3, fix B.13): `env_prefix="AIAT_"`, discriminator `service_role`,
    `validate_api_key_matches_provider`, `SecretStr` per chiavi, default guardrail Decimal,
    `load_settings()` dispatcher. Test: validator + least privilege (orchestrator senza
    chiavi LLM/wallet).
  - **prd**: §10.3 (fix B.13)
  - **dep**: M1
  - **files**: `src/aiat/config/settings.py`, `tests/unit/config/test_settings.py`
  - **verify**: `uv run pytest tests/unit/config/test_settings.py -q && uv run mypy src`
  - **done-when**: validator coerenza api-key/provider attivo; `ContextOrchestratorSettings`
    rifiuta extra (least privilege); test verde.

- [x] **M5-T04** — `orchestration/lifecycle.py`: `startup_checks`
  - **what**: TDD (settings/DB mockati). Dispatcher `startup_checks` role-specific §10.1:
    check comuni (network testnet inv #9, db schema, active experiment) + agent A1-A10
    (incl. A9 memory off inv #5, A10 baseline fatal) + orchestrator O1-O4 (incl. O1 env-var
    leak detection). I check che toccano risorse reali (A6 HL, A7 LLM smoke) sono
    strutturati ma testati con mock; quelli puri (network, guardrail config, memory off,
    baseline presence) testati a fondo.
  - **prd**: §10.1 (inv #5, #8, #9)
  - **dep**: M5-T03
  - **files**: `src/aiat/orchestration/lifecycle.py`, `tests/unit/orchestration/test_lifecycle.py`
  - **verify**: `uv run pytest tests/unit/orchestration/test_lifecycle.py -q && uv run mypy src`
  - **done-when**: `_check_network_testnet` rifiuta non-testnet; memory-off e
    baseline-missing sollevano `RuntimeError`; dispatch per ruolo corretto (mock); test verde.

- [x] **M5-T05** — `orchestration/scheduler.py`: APScheduler
  - **what**: TDD. Config §4.1: `CronTrigger` minuti 0/15/30/45, `coalesce=True`,
    `max_instances=1`, `misfire_grace_time=60`; `build_scheduler_for_agent` (start_delay
    30s) e `build_scheduler_for_orchestrator`. Test sulla configurazione dei job (no run reale).
  - **prd**: §4.1
  - **dep**: M1
  - **files**: `src/aiat/orchestration/scheduler.py`, `tests/unit/orchestration/test_scheduler.py`
  - **verify**: `uv run pytest tests/unit/orchestration/test_scheduler.py -q && uv run mypy src`
  - **done-when**: i job hanno trigger/coalesce/max_instances/grace corretti; agent ha
    start_delay 30s; test verde.

- [x] **M5-T06** — `orchestration/decision_loop.py`: 1 tick completo
  - **what**: TDD (componenti mockati). `run_once(tick_id)` §4.1: read context_snapshot →
    render prompt → invoke LLM → guardrails → execute → persist atomico (DecisionsRepository).
    Budget hard timeout 180s, **no fallback a tick precedente** (§2.1). Aggiorna runs.status.
  - **prd**: §4.1
  - **dep**: M5-T01, M3-T09, M4-T02, M4-T03, M2-T09
  - **files**: `src/aiat/orchestration/decision_loop.py`, `tests/unit/orchestration/test_decision_loop.py`
  - **verify**: `uv run pytest tests/unit/orchestration/test_decision_loop.py -q && uv run mypy src`
  - **done-when**: con tutti i collaboratori mockati, un tick produce decision+actions+
    esecuzione+persist nell'ordine §4.1; test verde.

- [x] **M5-T07** — Completa `__main__.py`: dispatcher reale
  - **what**: TDD (mock). Completare `src/aiat/__main__.py` §11.2: `load_settings()` →
    `configure_logging` → `startup_checks` → `build_scheduler_for_agent` vs
    `build_scheduler_for_orchestrator` per ruolo → `scheduler.start()`. Test che mocka
    settings/startup/scheduler e verifica il dispatch per ruolo.
  - **prd**: §11.2
  - **dep**: M5-T04, M5-T05
  - **files**: `src/aiat/__main__.py`, `tests/unit/orchestration/test_main_dispatch.py`
  - **verify**: `uv run pytest tests/unit/orchestration/test_main_dispatch.py -q && uv run mypy src`
  - **done-when**: il dispatcher seleziona lo scheduler corretto per agent vs orchestrator
    (mockato); test verde.

- [x] **M5-T08** 🐘 — `tests/e2e/test_decision_loop_smoke.py`
  - **what**: e2e §9.5: `run_once` con LLM cassette (o stub) + HL mock + Postgres effimero;
    verifica `runs.status='success'`, 1 decision, 3 decision_actions, 1 cost_event, 1
    llm_invocation, account_snapshot con portfolio_state_hash; se action BTC=LONG → 3 orders
    (entry+SL+TP).
  - **prd**: §9.5
  - **dep**: M5-T06
  - **files**: `tests/e2e/test_decision_loop_smoke.py`
  - **verify**: `uv run pytest tests/e2e/test_decision_loop_smoke.py -q`
  - **done-when** 🐘: lo smoke e2e mockato è verde con i conteggi attesi.

- [x] **M5-T09** 🐘 — `tests/e2e/test_isolation.py` (inv #1)
  - **what**: e2e §9.5 / inv #1: seed 2 model_id, lancia agent `model_1`, fallisce se legge
    rows `model_2`. **Doppia strategia**: `RepositorySpy` (primario, `LeakDetected`) +
    DB-level trap Postgres (secondario via `SET LOCAL aiat.expected_model_id`).
  - **prd**: §9.5 (inv #1)
  - **dep**: M5-T01, M5-T02a
  - **files**: `tests/e2e/test_isolation.py`, `tests/e2e/_repository_spy.py`
  - **verify**: `uv run pytest tests/e2e/test_isolation.py -q`
  - **done-when** 🐘: il test fallisce (correttamente) se un agent legge un altro model_id;
    passa con isolamento rispettato.

- [x] **M5-T10** 🐘 — `tests/e2e/test_context_parity.py` (inv #13)
  - **what**: e2e §9.5 / inv #13: orchestrator → 1 snapshot; 4 agent stesso tick_id;
    verifica `context_snapshot_id` identico sui 4 runs + `context_hash` byte-identico
    (market); `portfolio_state_hash` diverge correttamente (OK).
  - **prd**: §9.5 (inv #13)
  - **dep**: M5-T06, M3-T09
  - **files**: `tests/e2e/test_context_parity.py`
  - **verify**: `uv run pytest tests/e2e/test_context_parity.py -q`
  - **done-when** 🐘: i 4 agent condividono lo stesso context_snapshot/hash; portfolio
    diverge.

- [x] **M5-T11** 🐘 — `tests/e2e/test_guardrail_e2e.py`
  - **what**: e2e §9.5: LLM mock propone size_pct=0.99 / leverage=30 / confidence=0.95;
    verifica `size_pct_executed=0.20` (clamp), `leverage_executed≤10` (hard cap), flag
    `size_pct_clamped=true` e `leverage_clamped=true` persistiti.
  - **prd**: §9.5
  - **dep**: M5-T06, M4-T02
  - **files**: `tests/e2e/test_guardrail_e2e.py`
  - **verify**: `uv run pytest tests/e2e/test_guardrail_e2e.py -q`
  - **done-when** 🐘: i clamp sono applicati e i flag persistiti in decision_actions.

- [ ] **M5-T12** 🐘 — Invariant coverage matrix §9.7
  - **what**: Creare `tests/invariant_coverage.py` che aggrega/registra i test gating dei
    **15 invarianti** §9.7 con marker `@pytest.mark.invariant("N")`, e una meta-asserzione
    che `{1..15}` siano tutti coperti (nessuna cella vuota). Colmare i gap mancanti (es.
    #5 memory-off, #12 no-float AST su schemas, #2/#3 via integration).
  - **prd**: §9.7
  - **dep**: M5-T08, M5-T09, M5-T10, M5-T11
  - **files**: `tests/invariant_coverage.py`, eventuali test gating mancanti
  - **verify**: `for n in $(seq 1 15); do grep -rEq "invariant\((\"|')$n(\"|')\)" tests/ || { echo "missing invariant $n"; exit 1; }; done && echo "15/15 invariant markers present"`
  - **done-when**: tutti i 15 invarianti hanno ≥1 test marcato `invariant`; nessuna cella
    vuota. (Esecuzione piena dei gating e2e/integration richiede Postgres 🐘.)

- [x] **M5-T13** — `observability/logging_config.py` + `metrics.py`
  - **what**: `configure_logging(level)` structlog JSON (inv #10), `metrics.py` minimale.
    Nessun `print` nel runtime (ruff T201 già attivo). (`logging_config.py` è in omit di
    `.coveragerc`.)
  - **prd**: §2.2, §11.3 (inv #10)
  - **dep**: M0
  - **files**: `src/aiat/observability/logging_config.py`, `src/aiat/observability/metrics.py`
  - **verify**: `uv run python -c "from aiat.observability.logging_config import configure_logging; configure_logging('INFO')" && uv run ruff check src/aiat/observability && uv run mypy src`
  - **done-when**: logging JSON configurabile, ruff/mypy clean, nessun print.

- [ ] **M5-T14** 🛑 **[HUMAN-GATED]** — Smoke locale multi-tick (4 tick)
  - **what**: Verifica §12 M5: 1 orchestrator + 4 agent fittizi (LLM mockato) su Postgres
    locale per 4 tick consecutivi → dataset coerente (per tick: 1 context_snapshot + 4 runs
    + 4 decisions + 12 decision_actions). Anche se LLM è mockato, è un'integrazione di
    sistema da **osservare** (scheduler reale, multi-processo, Postgres reale).
  - **prd**: §12 M5
  - **dep**: M5-T07, M5-T08, M5-T09, M5-T10, M5-T11
  - **files**: `scripts/smoke_multitick.py` (opzionale, helper di osservazione manuale)
  - **human-action**: avviare un Postgres locale (o devcontainer con Postgres), lanciare i 5
    ruoli (mockando gli LLM) per 4 tick, ispezionare i conteggi via SQL.
  - **verify**: `test -n "$AIAT_DATABASE_URL" && uv run python scripts/smoke_multitick.py` (o procedura equivalente osservata manualmente)
  - **loop-rule**: integrazione di sistema da osservare → non chiudere in autonomia; annota
    "M5-T14 HUMAN-GATED/⚠️, blocco qui (smoke multi-tick da osservare)". Se è l'unico task
    rimasto, `RALPH_BLOCKED`.
  - **done-when**: 4 tick consecutivi producono il dataset coerente atteso.

> **DoD M5** (gate, parte loop): decision_loop + scheduler + lifecycle + tutti i repository
> + settings completi; e2e mockati verdi; invariant matrix #1-#15 verde. 🛑 Da M5 in poi la
> chiusura reale e M6/M7 sono interamente umane.

---

## Note per il loop

1. **Un task alla volta**: prendi il **primo** task non spuntato (in ordine di file) con
   tutte le `dep:` soddisfatte. Non iniziarne più di uno per iterazione (vedi `PROMPT.md`).
2. **Verify prima di spuntare**: esegui SEMPRE il comando `verify:`. Spunta `- [x]` SOLO se
   esce 0 (o soddisfa la condizione descritta). **Mai** spuntare lavoro non verificato.
3. **TDD per i moduli core** (`domain/`, `llm/`, `execution/`): test PRIMA, rosso, verde,
   refactor. Coverage core ≥95%, globale ≥80% (CLAUDE.md, §9.1).
4. **ADR quando serve**: i task **[D2]/[D3]/[D4]/[D5]** DEVONO creare l'ADR relativo in
   `docs/decisions/` (prossimo numero progressivo dopo l'ultimo esistente, dal template
   `0000-template.md`) e aggiornare `docs/decisions/README.md`. Il `verify:` lo controlla
   via slug. Crea un ADR anche se devii dal PRD (CLAUDE.md).
5. **🐘 PG (PostgreSQL server)**: i task marcati 🐘 richiedono un server Postgres per
   `pytest-postgresql`. Se assente (vedi Prerequisiti d'ambiente), il `verify:` fallisce al
   **setup della fixture**, NON per un bug di codice: scrivi comunque il codice/test, annota
   in `progress/log.md` "🐘 PG non disponibile: integration non eseguibile, provisioning
   Postgres richiesto", e — se non puoi procedere oltre — lascia il task non spuntato. Non
   falsificare un verde.
6. **🛑 STOP su human-gated**: per i task **[HUMAN-GATED]** (M2-T12, M3-T11, M4-T08, M5-T14)
   il `verify:` non è soddisfacibile senza la risorsa reale (API key, rete, wallet testnet).
   **Non simulare, non fingere il completamento**: lascia il task non spuntato e annota in
   `progress/log.md` "HUMAN-GATED, blocco qui" con l'ID. Se è l'**unico** task eseguibile
   rimasto, stampa esattamente `RALPH_BLOCKED` e spiega in `progress/log.md`.
   **M4-T08 è il primo stop fisico inderogabile** (wallet HL testnet reale).
7. **Mai mainnet** (inv #9): `AIAT_NETWORK=testnet` sempre. **Mai `print()`** runtime
   (inv #10, ruff T201). **Decimal** per i soldi (inv #12). **Mai** committare `.env`.
8. **Comando CLI import-linter**: è `uv run lint-imports` (non `import-linter`).
9. **`RALPH_COMPLETE`**: stampalo SOLO se ogni task non-human-gated è spuntato e i loro
   verify passano. I task 🛑 [HUMAN-GATED] non bloccano `RALPH_COMPLETE` se sono gli unici
   rimasti e sono stati correttamente annotati come gated (decisione: il loop ha finito la
   sua parte). In dubbio, preferisci `RALPH_BLOCKED` con spiegazione.

---

## Contraddizioni rilevate

> Annotate qui invece di risolverle a caso (come da istruzioni). Dove la TASK_MAP aveva già
> deciso, si segue la TASK_MAP; dove il PRD è internamente incoerente, si segue la sezione
> autoritativa e si documenta.

1. **Conteggio tabelle 17 vs 20** — §12 (M1 DoD) e §3.1 dicono "17 SQLAlchemy models", ma il
   DDL §3.2 contiene **20 `CREATE TABLE`** (experiments, models, prompt_templates,
   context_snapshots, context_build_runs, runs, llm_invocations, decisions,
   decision_actions, account_snapshots, positions, orders, fee_events, funding_events,
   cost_events, tax_sim_periods, outcomes, baseline_configs, baseline_equity_snapshots,
   errors). **Risoluzione**: il DDL §3.2 è autoritativo → si usano **20 modelli/tabelle**
   (M1-T06a..i). Candidato ADR opzionale (TASK_MAP "0006?") non incluso come task per non
   inventare task fuori mappa; documentare se si desidera tracciamento formale.

2. **Set repository §2.2 vs §7.6** — §2.2 elenca in `db/repositories/` solo 4 file
   (`decisions.py`, `positions.py`, `snapshots.py`, `ledger.py`), ma §7.6 (+fix B.5)
   definisce `DecisionsRepository`, `PositionsRepository`, `SnapshotsRepository`,
   `RunsRepository`, `OutcomesRepository`, `ContextBuildRepository`, `BaselineRepository`,
   `TaxSimulationRepository` — e **nessun** `LedgerRepository`. **Risoluzione**: si segue
   §7.6 (più recente). `ledger.py` di §2.2 **non** viene creato: i cost_events sono
   persistiti atomicamente da `DecisionsRepository` (inv #4). Candidato ADR opzionale
   (TASK_MAP "0007?") non incluso come task.

3. **Enum `GuardrailKind`** — la TASK_MAP (M1-T01) lo cita ("Side, EntryType, Tier,
   GuardrailKind, RunStatus…"), ma **§6.1 NON lo definisce**. Gli enum reali §6.1 sono 8:
   Side, EntryType, Tier, Geography, RunStatus, ExecutionStatus, OrderKind, CloseReason.
   **Risoluzione**: si seguono i 8 enum §6.1; `GuardrailKind` **non** viene creato (PRD
   vince; non inventare). Se servisse in futuro, va aggiunto via deviazione documentata.

4. **Comando `import-linter` nel CI** — §9.6 (ci.yml) e CLAUDE.md scrivono
   `uv run import-linter`, ma il console script reale del pacchetto è **`lint-imports`**
   (`uv run import-linter` fallirebbe con "command not found"). **Risoluzione**: TASKS.md usa
   `uv run lint-imports` (M0-T08, M0-T10) e M0-T10 corregge il ci.yml di conseguenza.

5. **"5 classi" eccezioni LLM** — la TASK_MAP (M2-T01) dice "5 classi §8.2" ma ne elenca 6
   (LLMError base + LLMTimeoutError + LLMRateLimitError + LLMAuthError + LLMParsingError +
   LLMUnrecoverableError). **Risoluzione**: si creano **6 classi** come da codice §8.2.

6. **Verifica §12 M1 "alembic crea 17 tabelle"** — conseguenza di (1): il test M1-T11
   verifica **20** tabelle, non 17.

---

*Generato il 2026-06-13 da `docs/TASK_MAP.md` + `docs/PRD_V2.md`. Copre M0→M5. M6/M7 fuori
scope (umane). I `verify:` sono stati validati contro il filesystem reale e i vincoli
d'ambiente (no Postgres server / no Docker daemon / no `python` nudo / firewall egress).*
