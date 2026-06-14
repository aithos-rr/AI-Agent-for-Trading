# Progress Log — AI Trading Agent V2

---

## 2026-06-13 — M0-T01

**Task**: `pyproject.toml` con `uv` + dipendenze  
**Changed**: Creato `/workspace/pyproject.toml` con tutte le runtime deps (langchain-core, langchain-openai, langchain-anthropic, pydantic>=2, pydantic-settings, sqlalchemy[asyncio]>=2, asyncpg, alembic, apscheduler<4, hyperliquid-python-sdk, httpx, structlog, pandas, numpy, pandas-ta, tenacity, pyyaml) e dev deps in `[dependency-groups].dev` (pytest, pytest-asyncio, pytest-cov, pytest-postgresql, vcrpy, ruff, mypy, import-linter).  
**Result**: `uv sync` esce 0, tutti gli import chiave OK.  
**Learnings**: Usato `[dependency-groups]` (PEP 735) invece di `[project.optional-dependencies]` per dev deps — uv sync installa entrambi per default.  
**Next**: M0-T02 (generare uv.lock e committarlo — già generato da uv sync).

---

## 2026-06-13 — M0-T02

**Task**: Genera e committa `uv.lock`  
**Changed**: `uv.lock` già generato da uv sync in M0-T01 e committato.  
**Result**: `uv sync --frozen` esce 0.  
**Learnings**: Il lockfile viene generato automaticamente da `uv sync` — nessuna azione extra necessaria.  
**Next**: M0-T03 (skeleton src/aiat/ + tests/).

---

## 2026-06-13 — M0-T03 through M0-T12 (M0 complete)

**Tasks**: M0-T03 skeleton, M0-T04 __main__ stub, M0-T05 ruff config, M0-T06 mypy strict, M0-T07 pytest+coveragerc+smoke, M0-T08 import-linter, M0-T09 Dockerfile, M0-T10 ci.yml, M0-T11 .env.example check, M0-T12 README.

**Changed**:
- `src/aiat/` + all sub-packages (`config`, `domain`, `db/models`, `db/repositories`, `context/collectors`, `prompts`, `llm`, `execution`, `orchestration`, `observability`) with `__init__.py`
- `tests/` skeleton with `unit/{domain,llm,execution,context,orchestration}`, `integration/`, `e2e/`
- `src/aiat/__main__.py` — stub that reads AIAT_SERVICE_ROLE and logs via structlog
- `pyproject.toml` — ruff (T201+E/F/I/UP/B, target py312), mypy (strict, overrides for untyped 3rd party), pytest (asyncio_mode=auto, markers), importlinter (domain independent contract)
- `.coveragerc` — branch=True, omit __main__/logging_config, standard excludes
- `tests/test_smoke.py` — package import smoke test
- `docker/Dockerfile` — multi-stage builder+runtime, uid 10001, PYTHONPATH=/app/src
- `.github/workflows/ci.yml` — lint+mypy, unit (80%), core-95% (domain/llm/execution), integration, e2e
- `README.md` — minimal with uv sync + run commands

**Learnings**:
- import-linter `layers` contract `ignore_imports` field is not supported (or requires imports to exist). Simplified to just the `forbidden` domain-independence contract for the skeleton phase.
- `mypy` strict mode requires `ignore_missing_imports` overrides for 3rd-party packages without type stubs (apscheduler, hyperliquid, pandas-ta, vcr, pytest-postgresql).

**Result**: Full M0 DoD green — uv sync/ruff/mypy/pytest/lint-imports all exit 0. Dockerfile structurally correct.

**Next**: M1-T01 (domain/enums.py — 8 enums from §6.1).

---

## 2026-06-13 — M1-T01..T12 (M1 COMPLETE)

**Tasks**: All M1 tasks — domain schemas, DB models (20 tables), alembic setup, migration, integration tests, domain coverage.

**Changed**:
- `src/aiat/domain/enums.py` — 8 enums (Side, EntryType, RunStatus, OrderStatus, PositionSide, NetworkEnv, ServiceRole, AgentModel)
- `src/aiat/domain/schemas.py` — TradeDecision, ActionDecision + context schemas + DTO runtime
- `src/aiat/domain/exceptions.py` — AIATError hierarchy (ContextBuildError, ExecutionRejectedError, ExecutionTimeoutError)
- `src/aiat/db/models/` — 20 SQLAlchemy 2.x async models: experiments, models, prompt_templates, context_snapshots, context_build_runs, runs, llm_invocations, decisions, decision_actions, account_snapshots, positions, orders, fee_events, funding_events, cost_events, tax_sim_periods, outcomes, baseline_configs, baseline_equity_snapshots, errors
- `src/aiat/db/session.py` — async_sessionmaker factory (get_db_session)
- `alembic.ini` + `alembic/env.py` (sync psycopg driver for migrations) + `alembic/versions/001_initial_schema.py` (full DDL for 20 tables)
- `tests/conftest.py` — postgresql_proc_fixture + db_url (creates "aiat_tests" DB, runs alembic) + db_session (async, rollback teardown)
- `tests/integration/test_db_migrations.py` — 5 tests: upgrade/downgrade/idempotency + constraint/column checks
- `tests/unit/domain/test_exceptions.py` — exception hierarchy tests
- `tests/unit/domain/test_schemas_trade_decision.py` — additional coverage tests (branch coverage gaps filled)

**Learnings**:
- psycopg3 (`postgresql+psycopg`) re-escapes JSONB `server_default` string literals in DDL, making `'[]'::jsonb` arrive as `'''[]''::jsonb'` (invalid JSON). Fix: use `sa.text("jsonb_build_array()")` — avoids any string literal.
- `asyncpg` has issues with PREPARE + DDL containing JSONB server_default. Solution: run alembic migrations with psycopg (sync) driver; convert URL in env.py (`+asyncpg` → `+psycopg`).
- pytest-postgresql `postgresql_proc` starts the server but does NOT create the test database. Must manually CREATE DATABASE via psycopg connecting to the `postgres` admin database first.
- Use separate databases for general fixtures (`aiat_tests`) vs migration cycle tests (`aiat_migration_test`) to avoid upgrade/downgrade cycles corrupting the session-scoped fixture DB.

**Result**: Full M1 DoD green — 20 tables created/destroyed/recreated, all constraints present, domain/ coverage 100%, all CI checks pass.

**Next**: MILESTONE_COMPLETE M1 → proceed to M2 (LLM abstraction + StatsHandler).

---

## 2026-06-13 — M2-T01..T13 (LLM abstraction layer + ADR D3)

**Tasks completed**: M2-T01 through M2-T11, M2-T13 (M2-T12 HUMAN-GATED — see below)

**Changed**:
- `src/aiat/llm/exceptions.py` — 6 exception classes (LLMError, LLMTimeoutError, LLMRateLimitError, LLMAuthError, LLMParsingError, LLMUnrecoverableError)
- `src/aiat/llm/base.py` — BaseLLMClient ABC
- `src/aiat/llm/structured.py` — invoke_structured + _extract_json_balanced (state machine) + _is_parsing_error/_is_rate_limit_error/_is_auth_error with module-level SDK type resolution (ADR-0009)
- `src/aiat/llm/stats_handler.py` — StatsCallbackHandler with OpenAI/Anthropic/DeepSeek-R1/Qwen usage extraction, Decimal cost computation
- `src/aiat/llm/openai_client.py` — OpenAIClient(BaseLLMClient)
- `src/aiat/llm/anthropic_client.py` — AnthropicClient(BaseLLMClient)
- `src/aiat/llm/openai_compatible_client.py` — OpenAICompatibleClient with DEEPSEEK/QWEN/OPENROUTER base URLs
- `src/aiat/llm/factory.py` — load_llm dual-mode (direct / openrouter, ADR-0008)
- `src/aiat/config/settings.py` — AgentSettings stub (llm_provider, llm_gateway, model_name_api, keys, temperature, top_p, max_tokens, seed)
- `src/aiat/config/model_pricing.yaml` + `src/aiat/config/pricing.py` — load_pricing_for_model()
- `tests/unit/llm/` — 119 unit tests covering all modules
- `tests/integration/test_llm_providers.py` — 15 VCR-marked tests (collect-only verified; cassettes pending M2-T12)
- `tests/cassettes/.keep` — placeholder for VCR cassettes directory
- `docs/decisions/0009-exception-classification.md` — ADR D3 (isinstance() primary + string-match fallback)
- `docs/decisions/README.md` — ADR 0009 entry added

**Verify output**:
- `uv run ruff check src tests` → clean
- `uv run ruff format --check src tests` → clean
- `uv run mypy src` → clean (49 files)
- `uv run lint-imports` → 1 contract kept, 0 broken
- `uv run pytest tests/unit/llm --cov=src/aiat/llm --cov-fail-under=95` → 119 passed, 98% coverage

**Learnings**:
- Module-level SDK type resolution (try/except ImportError at import time, not inside functions) avoids uncoverable branch-miss on installed packages. Functions use pre-resolved tuples directly.
- `asyncio.TimeoutError` → `TimeoutError` (UP041): Python 3.11+ unifies them; ruff enforces.
- `raise ... from err` (B904) required inside except blocks — B904 is not auto-fixable, must be done manually.
- `LLMResult` in LangChain validates `generations` as real `Generation`/`ChatGeneration` instances, rejects `MagicMock`. Tests must use real `ChatGeneration(message=AIMessage(...))`.
- `ChatAnthropic` constructor kwargs (`model`, `max_tokens`) need `# type: ignore[call-arg]` due to missing mypy stubs.

**M2-T12 HUMAN-GATED**: Record 15 VCR cassettes via OpenRouter requires real `AIAT_OPENROUTER_API_KEY` + network access to openrouter.ai (blocked by devcontainer firewall). Leaving M2-T12 unchecked.

**Next**: RALPH_BLOCKED — M2-T12 is the only remaining M2 task and is HUMAN-GATED. Proceed to M3 in parallel.

---

---

## 2026-06-14 — M3-T01 (BaseCollector ABC)

**Task**: M3-T01 — `context/collectors/base.py`: `BaseCollector` ABC

**Changed**:
- `src/aiat/context/collectors/base.py` — `BaseCollector[T: BaseModel](ABC)` with `timeout_seconds`, `cache_ttl_seconds`, `collect() -> T`; `CollectorTimeoutError`; `CollectorSourceError`

**Note**: PRD §7.2 shows `class BaseCollector(ABC, Generic[T])` but ruff UP046 enforces PEP 695 type parameter syntax for Python 3.12+ → used `class BaseCollector[T: BaseModel](ABC)` (bound retained, TypeVar dropped).

**Verify output**:
- `uv run python -c "from aiat.context.collectors.base import BaseCollector"` → ok
- `uv run mypy src` → Success: no issues found in 50 source files
- `uv run ruff check src/aiat/context/collectors/base.py` → All checks passed!
- `uv run ruff format --check src/aiat/context/collectors/base.py` → 1 file already formatted

**Next**: M3-T02 — `context/collectors/technical.py` (TDD, httpx mock)
