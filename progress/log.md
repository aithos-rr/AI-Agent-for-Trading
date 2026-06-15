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

---

## 2026-06-14 — M3-T02 (TechnicalCollector)

**Task**: M3-T02 — `context/collectors/technical.py`

**Changed**:
- `src/aiat/context/collectors/technical.py` — `TechnicalCollector[TechnicalIndicators]` fetches 200 15m OHLCV candles from Hyperliquid `/info` endpoint via httpx, computes RSI-14, MACD histogram, EMA20/50, Bollinger upper/lower, ATR-14, volume_24h_usd using pandas_ta; all outputs as `Decimal` (inv #12).
- `tests/unit/context/test_technical.py` — 12 unit tests covering: happy path, all fields are Decimal, price=last close, volume positive, Bollinger upper > lower, EMA20 > EMA50 in rising market, symbol uppercased, HTTP 500 → CollectorSourceError, empty candles → CollectorSourceError, ReadTimeout → CollectorTimeoutError, ConnectError → CollectorSourceError, unsupported symbol → CollectorSourceError.
- `pyproject.toml` — added `"pandas.*"` to mypy `ignore_missing_imports` overrides (pandas has no bundled stubs; already a project dependency).

**Notes**:
- pandas_ta registers `df.ta` via pandas extension accessor — must `import pandas_ta` explicitly (noqa: F401).
- Bollinger column names from pandas_ta are dynamic (e.g. `BBU_20_2.0_2.0`); used `startswith("BBU_")` / `startswith("BBL_")` for robustness instead of hardcoded names.
- `float(str(c["key"]))` pattern handles both string and numeric candle values while satisfying mypy strict on `dict[str, object]`.

**Verify output**:
```
uv run pytest tests/unit/context/test_technical.py -q
12 passed in 1.70s

uv run ruff check src tests && uv run ruff format --check src tests
All checks passed!
78 files already formatted

uv run mypy src
Success: no issues found in 51 source files
```

**Next**: M3-T03 — `context/collectors/sentiment.py`

---

## 2026-06-14 — M3-T03 (SentimentCollector)

**Task**: M3-T03 — `context/collectors/sentiment.py`

**Changed**:
- `src/aiat/context/collectors/sentiment.py` — `SentimentCollector[SentimentSnapshot]` fetches Fear & Greed index from `https://api.alternative.me/fng/` (public, no API key); maps `value_classification` string to `Literal["extreme_fear","fear","neutral","greed","extreme_greed"]` via `_LABEL_MAP`; all error paths raise `CollectorTimeoutError` / `CollectorSourceError`; `fetched_at` is UTC ISO string.
- `tests/unit/context/test_sentiment.py` — 20 unit tests covering: happy path (SentimentSnapshot returned), all label mappings (5), index boundaries 0/100, HTTP 500 → CollectorSourceError, ReadTimeout → CollectorTimeoutError, ConnectError → CollectorSourceError, empty data array, missing data key, unknown classification label, default timeout (5s), custom timeout, default cache_ttl (60s).

**Notes**:
- Fear & Greed API is `alternative.me` (public, free, no key) — legacy code used CMC Pro API (requires key); switched to public endpoint matching PRD §10.1 O4 design (`SentimentCollector(timeout_seconds=5)` with no key).
- Ruff UP017: `datetime.timezone.utc` → `from datetime import UTC` then `datetime.now(tz=UTC)`.

**Verify output**:
```
uv run pytest tests/unit/context/test_sentiment.py -q
20 passed in 0.22s

uv run ruff check src tests && uv run ruff format --check src tests
All checks passed! / 80 files already formatted

uv run mypy src
Success: no issues found in 52 source files
```

**Next**: M3-T04 — `context/collectors/news.py` + ADR [D5]

---

## 2026-06-14 — M3-T04 (NewsCollector + ADR D5)

**Task**: M3-T04 — `context/collectors/news.py` + **ADR [D5]** (closes bounded deferral D5)

**Changed**:
- `src/aiat/context/collectors/base.py` — removed `T: BaseModel` bound from `BaseCollector[T]`; news/onchain collectors return `list[...]` which is not a BaseModel subclass. Removed unused `from pydantic import BaseModel` import.
- `src/aiat/context/collectors/news.py` — `NewsCollector[list[NewsItem]]` fetching from 2 RSS sources (CryptoPanic + CoinDesk); sequential per-source fetch with partial failure tolerance (at least 1 must succeed); `_parse_rss()` uses stdlib `xml.etree.ElementTree` + `email.utils.parsedate_to_datetime`; `check_sources_reachability()` via HEAD requests; `MAX_ITEMS_PER_TICK=10`.
- `tests/unit/context/test_news.py` — 26 unit tests: happy path (both sources), partial failure (one source down), all-fail paths (CollectorSourceError / CollectorTimeoutError), `check_sources_reachability`, defaults, title/summary truncation, sorting by recency, max_items cap.
- `docs/decisions/0011-rss-sources.md` — ADR closing D5: 10 items/tick, 2 sources (CryptoPanic + CoinDesk), partial failure semantics documented. Next milestone obligation: seed must include `MAX_ITEMS_PER_TICK` in `prompt_template_hash`.
- `docs/decisions/README.md` — added ADR-0011 entry.

**D5 decision (ADR-0011)**:
- 10 items/tick max (≈2250 tokens — fits all 4 target models)
- 2 RSS sources: `cryptopanic` + `coindesk`
- Partial failure tolerated: at least 1 source must succeed; `CollectorTimeoutError` only when ALL timed out

**Verify output**:
```
uv run pytest tests/unit/context/test_news.py -q
26 passed in 0.19s

ls docs/decisions/*-rss-sources.md → ok
grep -q rss-sources docs/decisions/README.md → ok

uv run ruff check src tests && uv run ruff format --check src tests
All checks passed! / 82 files already formatted

uv run mypy src
Success: no issues found in 53 source files
```

**Next**: M3-T05 — `context/collectors/onchain.py`

---

## 2026-06-14 — M3-T05 (OnchainCollector + HLPublicInfoClient)

**Task**: M3-T05 — `context/collectors/onchain.py`

**Changed**:
- `src/aiat/context/collectors/onchain.py` — `HLPublicInfoClient` (read-only httpx client for HL public `/info` endpoint: `fetch_meta()` → `{"type":"meta"}` dict, `fetch_meta_and_asset_ctxs()` → `(meta, list[asset_ctx])`); `OnchainCollector[list[OnChainSnapshot]]` fetching `metaAndAssetCtxs` for BTC/ETH/SOL: `funding_rate_8h` from `ctx["funding"]`, `open_interest_usd` = `openInterest * markPx`, `long_short_ratio` from `impactPxs[0]/impactPxs[1]` (bid/ask impact ratio; falls back to `Decimal("1")` if absent), `liquidations_24h_usd` = `dayNtlVlm * 0.001` (HL has no public global liquidations endpoint; dayNtlVlm proxy documented).
- `tests/unit/context/test_onchain.py` — 24 unit tests: happy path (3 snapshots, BTC/ETH/SOL order, all Decimal), field extraction (funding, OI, long_short_ratio, liquidations), edge cases (no impactPxs → ratio=1), errors (HTTP 500 → CollectorSourceError, ReadTimeout → CollectorTimeoutError, ConnectError → CollectorSourceError, missing symbol → CollectorSourceError, missing universe → CollectorSourceError, malformed ctx → CollectorSourceError), config (default/custom timeout, cache_ttl), HLPublicInfoClient URL routing (testnet/mainnet/custom).

**Notes**:
- HL public API has no global liquidations endpoint; `dayNtlVlm * 0.001` is a proxy approximation (documented in source docstring).
- `long_short_ratio` derived from `impactPxs[0]/impactPxs[1]` (long impact price / short impact price) — ratio > 1 indicates long-side pressure.
- `asyncio.wait_for` timeout raises `TimeoutError` (Python 3.11+ unified alias); `httpx.TimeoutException` handles network-level timeouts separately.

**Verify output**:
```
uv run pytest tests/unit/context/test_onchain.py -q
24 passed in 0.19s

uv run mypy src
Success: no issues found in 54 source files

uv run ruff check src tests && uv run ruff format --check src tests
All checks passed! / 84 files already formatted
```

**Next**: M3-T06 — `context/controlled_signals.py` + ADR [D4]

---

## 2026-06-14 — M3-T06 (controlled_signals.py + ADR-0012 [D4])

**Task**: M3-T06 — `context/controlled_signals.py` + ADR (closes bounded deferral D4)

**Changed**:
- `src/aiat/context/controlled_signals.py` — `CONTROLLED_SIGNALS: frozenset[str]` with all 18 values from §6.2, exactly matching `ControlledSignal = Literal[...]` in `domain/schemas.py`. Module docstring explains the hash-locking constraint (§3.2.1) and invariant #6.
- `tests/unit/context/test_controlled_signals.py` — 22 tests: alignment test (`set(get_args(ControlledSignal)) == CONTROLLED_SIGNALS`), count (18), 5 categories, parametrized presence of all 18 signals, format invariant (`category.name`).
- `docs/decisions/0012-controlled-signals.md` — ADR closing D4: 18-value §6.2 vocabulary adopted as final; rationale, alternatives considered (wait for smoke tests, reduce to 12), test gating, seed M7 propagation noted.
- `docs/decisions/README.md` — added ADR-0012 entry.

**D4 decision (ADR-0012)**:
- Adopted §6.2 preliminary list verbatim (18 signals, 5 categories)
- Rationale: vocabulary aligns with all 4 collectors (T02-T05); prompt_template_hash must be stable before M7 seed; no empirical evidence yet to warrant deviation
- Risk documented: if M3-T11 smoke reveals LLM signal drift, a superseding ADR + hash update would be needed

**Verify output**:
```
uv run pytest tests/unit/context/test_controlled_signals.py -q
22 passed in 0.07s

ls docs/decisions/*-controlled-signals.md → ok
grep -q controlled-signals docs/decisions/README.md → ok

uv run ruff check src tests → All checks passed!
uv run ruff format --check src tests → 86 files already formatted
uv run mypy src → Success: no issues found in 55 source files
```

**Next**: M3-T07 — `context/builder.py` (ContextBuilder composing 4 collectors into ContextBundle)

---

## 2026-06-14 — M3-T07 (ContextBuilder)

**Task**: M3-T07 — `context/builder.py`: `ContextBuilder`

**Changed**:
- `src/aiat/context/builder.py` — `ContextBuilder` class that composes TechnicalCollector×3 (BTC/ETH/SOL), SentimentCollector, NewsCollector, OnchainCollector into a `ContextBundle` via parallel fetch (`asyncio.gather(..., return_exceptions=True)`). Per-source timestamps recorded on successful fetch. `ContextBuildError` raised if any collector fails, with error message naming each failing source. Module-level `_SOURCE_LABELS` tuple ensures `zip(strict=True)` is safe.
- `tests/unit/context/test_builder.py` — 19 unit tests with mock collectors (`AsyncMock`): happy path (bundle returned, tick fields, 3 technical entries, correct symbols, sentiment/news/onchain content, 6 source_timestamp keys, ISO string format, all collectors called), failure paths (each of the 6 sources failing individually raises `ContextBuildError` with the source name in the message, multiple-failure reporting, successful sources absent from error message, pydantic roundtrip).

**Design decisions**:
- `_stamped()` inner async function captures `source_timestamps` dict and records timestamp only on successful return; exceptions propagate out and are captured by `return_exceptions=True`.
- Named constructor parameters (`technical_btc`, `technical_eth`, `technical_sol`) rather than a sequence — explicit and type-safe; easier to inject mocks in tests.
- Hard overall timeout (30s per §4.1) is the ContextOrchestrator's responsibility (M3-T09), not the builder's.

**Verify output**:
```
uv run pytest tests/unit/context/test_builder.py -q
19 passed in 0.21s

uv run ruff check src tests → All checks passed!
uv run ruff format --check src tests → 88 files already formatted
uv run mypy src → Success: no issues found in 56 source files
```

**Next**: M3-T08 🐘 — `db/repositories/context_build.py` (ContextBuildRepository — requires Postgres)

---

## 2026-06-14 — M3-T08 (ContextBuildRepository)

**Task**: M3-T08 🐘 — `db/repositories/context_build.py`: `ContextBuildRepository`

**Changed**:
- `src/aiat/db/repositories/context_build.py` — `ContextBuildRepository` with 4 methods:
  `start_build` (creates ContextBuildRun status='running', returns build_run_id),
  `complete_build` (inserts ContextSnapshot from ContextBundle, links to build run, returns snapshot_id),
  `fail_build` (updates build run to failed/timeout without creating snapshot),
  `get_snapshot_for_tick` (SELECT by experiment_id + tick_id). No internal commit — caller owns the UoW.
- `src/aiat/db/repositories/__init__.py` — exports `ContextBuildRepository`
- `alembic/versions/002_fix_context_build_runs.py` — new migration to bring context_build_runs
  and context_snapshots in line with their models: adds `failure_stage`/`error_context` columns,
  drops stale `error_message`/`duration_ms`, fixes check constraint to include 'running', removes
  spurious FK-to-experiments, adds UniqueConstraint, renames partial index.
- `tests/integration/test_db_repositories_context_build.py` — 7 integration tests:
  start_build creates running row; complete_build (success + partial); fail_build (failed + timeout);
  get_snapshot_for_tick returns snapshot; returns None when missing.

**Design decisions**:
- `context_hash` = SHA-256 of `ContextBundle.model_dump_json()` — deterministic Pydantic v2 serialization.
- `start_build` takes `tick_at: str` (ISO) to match PRD §7.6 signature; parsed to aware datetime internally.
- Migration 002 needed because migration 001 (autogenerated at M1-T09) did not perfectly reflect the models
  for `context_build_runs` (wrong column names, missing columns, incorrect check constraint, spurious FKs).
  This is a forward migration per CLAUDE.md policy (never modify retroactively).

**Verify output**:
```
uv run pytest tests/integration/test_db_repositories_context_build.py -q
7 passed in 6.16s

uv run ruff check src tests → All checks passed!
uv run ruff format --check src tests → 90 files already formatted
uv run mypy src → Success: no issues found in 57 source files
```

**Next**: M3-T09 🐘 — `orchestration/context_orchestrator.py` (requires Postgres)

---

## M3-T09 — `orchestration/context_orchestrator.py` (2026-06-14)

**Task**: M3-T09 🐘 — ContextOrchestrator entrypoint for 5th Railway service (PRD §7.1)

**Files created**:
- `src/aiat/orchestration/context_orchestrator.py` — `ContextOrchestrator` class:
  `build_tick_context(tick_id, tick_at, experiment_id)` composes `ContextBuilder` +
  `ContextBuildRepository`, applies a hard timeout (`asyncio.wait_for`), and always
  commits a `context_build_runs` row (status: success / failed / timeout). Owns the UoW.
- `tests/integration/test_context_orchestrator.py` — 8 integration tests:
  success (returns bundle + persists snapshot + build_run status='success'),
  collector failure (raises ContextBuildError + build_run status='failed' + no snapshot),
  timeout (raises ContextBuildError with 'timed out' + build_run status='timeout').

**Design decisions**:
- `build_tick_context` signature extended with `tick_at` parameter (required by
  `ContextBuildRepository.start_build`; PRD Protocol omits it as an implementation detail).
- `asyncio.TimeoutError` replaced with `TimeoutError` (UP041 ruff rule; identical in Python 3.11+).
- `TimeoutError` handler uses `raise ... from None` (B904) to suppress chained exception context.
- No "partial" status path: `ContextBuilder.build()` raises `ContextBuildError` on ANY
  collector failure; the orchestrator maps this to status='failed'. The repository's
  `status='partial'` remains available for a future extension.

**Verify output**:
```
uv run pytest tests/integration/test_context_orchestrator.py -q
8 passed in 5.93s

uv run ruff check src tests → All checks passed!
uv run ruff format --check src tests → 92 files already formatted
uv run mypy src → Success: no issues found in 58 source files
```

**Next**: M3-T10 — Unit test collectors (aggregato)

---

## M3-T10 — Unit test collectors (aggregato) (2026-06-14)

**Task**: M3-T10 — Ensure `tests/unit/context/` covers each collector; fill gaps left by T02-T05.

**What changed**:
Added 11 targeted tests across 4 test files to cover previously uncovered error branches:

- `test_sentiment.py` (+3): invalid JSON (lines 71-72), data[0] not a dict (line 80),
  malformed entry missing key (lines 85-86).
- `test_technical.py` (+3): invalid JSON from candles endpoint (lines 93-94), malformed
  candle data with missing keys (lines 117-118), insufficient candles <50 (line 121).
- `test_news.py` (+2): items with empty title are skipped via `continue` (line 53),
  invalid pubDate falls back to `datetime.now()` (lines 58-59).
- `test_onchain.py` (+3): `fetch_meta()` non-200 response (line 57), unexpected
  `metaAndAssetCtxs` structure with only 1 element (line 82), `TimeoutError` from inner
  coroutine triggers `CollectorTimeoutError` (line 123).

**Coverage result** (before → after):
- `news.py`: 96% → 100%
- `onchain.py`: 95% → 100%
- `sentiment.py`: 89% → 100%
- `technical.py`: 88% → 95% (lines 132-133 and 148-149 remain: defensive pandas-ta
  exception handlers that require internal mock of the library to trigger — left uncovered
  as they are extreme edge cases not worth fragile internal mocking)
- Total context/: 93% → 99%

**Verify output**:
```
uv run pytest tests/unit/context -q
134 passed in 2.40s

uv run ruff check src tests → All checks passed!
uv run ruff format --check src tests → 92 files already formatted
uv run mypy src → Success: no issues found in 58 source files
```

**Next**: M3-T11 HUMAN-GATED/⚠️, blocco qui (smoke reale richiede rete+DB reale).

---

## 2026-06-14 — M3 supervisor review (Opus) + gate esterno

**Contesto**: review adversariale del diff M3 (5 dimensioni × reviewer + verifica
scettica, 21 agent). 9 finding confermati. Triage e azioni dell'operatore (Opus):

**Fix applicati (bug isolati con test, autonomi per CLAUDE.md):**
- **F1 (#12)** `technical.py`: `price_usd` proveniva da `df["close"]` (float64) → round-trip
  lossy stringa→float→Decimal. Ora `price_usd=Decimal(close_raw[-1])` dalla stringa API
  grezza (esatto). Aggiunto `test_price_usd_preserves_full_precision` (17 cifre); corretto
  `test_price_is_last_close` che mascherava il bug (asseriva contro il valore float-roundtripped).
- **F3/F8** `news.py`: ordinamento per recency era lessicografico sulla stringa ISO →
  errato con offset timezone misti. Ora `published_at` normalizzato a UTC in `_parse_rss`
  e sort per `datetime.fromisoformat(...)`. Aggiunto `test_sorted_by_absolute_instant_across_timezones`.
- **F6** `onchain.py`: `decimal.InvalidOperation` non catturato → rompeva il contratto
  `CollectorSourceError`. Aggiunto a tupla except. Aggiunto `test_non_numeric_funding_raises_source_error`.
- **F7** `technical.py`: timeout default 30s → **10s** (PRD §4.1) + parametro `timeout_seconds`
  in `__init__` (coerenza con gli altri collector). Aggiunti `test_default_timeout_is_10`/`test_custom_timeout`.
- **F9** `ADR-0011`: correggeva male §7.2 ("nessun bound") — §7.2 riga 1486 definisce
  `TypeVar("T", bound=BaseModel)`. Riformulato come deviazione consapevole registrata.

**Non modificato (by-design, PRD-compliant):**
- **F2 (#13)** orchestrator commit non protetto: PRD §3.2.2 righe 421-422 accettano
  esplicitamente "crash prima di scrivere row → agent leggono 'missed'"; tutti i path
  gestiti scrivono già `fail_build`. Nessuna violazione → nessuna modifica.

**Deferiti all'utente (semantica dati LLM → decisione + ADR; il smoke reale M3-T11 li rivela):**
- **F4** `onchain.long_short_ratio` derivato da `impactPxs` (bid/ask) = rumore ~1.0; HL `/info`
  non espone un long/short ratio globale. Serve: fonte corretta oppure placeholder documentato (ADR).
- **F5** `onchain.funding_rate_8h`: HL ritorna il funding **orario**; salvato senza conversione
  in un campo "8h". Il PRD riga 330 *assume* periodo 8h. Serve decidere: ×8 vs rinominare (ADR).

**Verify output (gate esterno `tools/gate_check.sh M3`, nel container con Postgres):**
```
ruff / format / mypy --strict / lint-imports → clean
pytest all tiers (unit+integration) → 293 passed, coverage globale 97.56% (soglia 80%)
pytest core (domain+llm) → 98.86% (soglia 95%)
pytest integration (Postgres effimero) → 35 passed
GATE PASSED for M3
```

**Next**: M3 (parte loop) COMPLETA e validata. Resta solo **M3-T11 [HUMAN-GATED]** (smoke
reale orchestrator: rete sbloccata + AIAT_DATABASE_URL su Postgres reale) + decisioni F4/F5.
Handoff all'utente.

---

## 2026-06-14 — M3-T11 smoke reale (umano) + decisioni F4/F5/news → ADR-0013

**Contesto**: smoke manuale dei 4 collector contro API reali (host fuori firewall, script
usa-e-getta `/smoke_m3.py`, gitignored). Scoperto che `python -m aiat` è ancora lo stub M0
(wiring orchestrator rimandato a M5-T07), quindi M3-T11 *letterale* non chiudibile ora; lo
smoke dei collector ha però validato i parser sul reale e rivelato 3 problemi confermati
dal dato vero.

**Esito smoke**: technical ✅ (price_usd esatto), sentiment ✅ (F&G), news ❌ (entrambe le
fonti rotte), onchain ⚠️ (F4/F5 confermati). Dato reale: `funding=0.0000125` (orario),
`premium=-0.0002442` (BTC), nessun long/short globale, CoinDesk HTTP 308, CryptoPanic XML
non valido.

**Decisioni utente (3/3 raccomandate) → implementate, ADR-0013**:
- **F5 funding ×8**: `funding_rate_8h = ctx["funding"] * 8` (HL ritorna l'orario; nome campo
  invariato, semantica corretta).
- **F4 premium**: rinominato `OnChainSnapshot.long_short_ratio` → `premium` (Decimal signed,
  `ctx["premium"]`); rimossa la derivazione fasulla da `impactPxs`. Deviazione §6.3 → ADR-0013.
- **News robuste**: `follow_redirects=True` (GET+HEAD, risolve CoinDesk 308) + parsing a 2
  livelli (ElementTree strict → fallback lenient `html.parser`, risolve CryptoPanic). Nessuna
  dep nuova.

**Changed**: `domain/schemas.py` (premium), `context/collectors/onchain.py` (×8, premium),
`context/collectors/news.py` (follow_redirects + `_parse_rss_strict`/`_parse_rss_lenient`),
test onchain/news/builder/serialization/integration aggiornati, `docs/decisions/0013-*.md`
+ README, `.gitignore` (smoke).

**Verify (gate esterno M3, container + Postgres)**:
```
ruff / format / mypy --strict / lint-imports → clean
pytest all tiers → 295 passed, coverage globale 97.57% (soglia 80%)
pytest core (domain+llm) → 98.86% (soglia 95%)
pytest integration (Postgres effimero) → 35 passed
GATE PASSED for M3
```

**Next**: i fix sono unit-testati con mock realistici. Validazione finale facoltativa:
re-run `/smoke_m3.py` sul host (deve mostrare news non vuote + `premium`/`funding ×8`).
Smoke end-to-end `python -m aiat` rimandato a M5-T07 (entrypoint). M3 pronto; M4 può partire.

---

## 2026-06-14 — M3-T11 re-run smoke (umano, host) — fix VALIDATI sul reale

Re-run di `/smoke_m3.py` dopo i fix di ADR-0013. Esito su dato reale:
- **News ✅ funziona**: CoinDesk segue il redirect 308 → 25 item → 10 reali ordinati per
  recency (es. 2026-06-14T19:17Z, 18:30Z, 15:00Z). Il `follow_redirects` ha risolto.
- **F5 ✅**: `funding_rate_8h = "0.0001000"` su tutti gli asset (= 0.0000125 orario ×8).
- **F4 ✅**: campo `premium` reale e direzionale (BTC −0.00026, ETH −0.00058, SOL +0.00013).
- **Technical/Sentiment ✅**: price esatto, F&G 18.

**Limitazione nota → DECISIONE registrata**: **CryptoPanic resta non parsabile** anche con
il fallback lenient (`Invalid XML line 90`, deterministico su entrambi i run — quell'URL non
restituisce un RSS standard). Il fallback ha estratto 0 item → fonte scartata. Le news
funzionano comunque (tolleranza fallimento parziale, ADR-0011), ma **di fatto su 1 sola fonte
(CoinDesk)**, indebolendo la ratio "2 fonti anti-bias".

**Decisione (raccomandazione accettata)**: si accetta CoinDesk-only **per ora** (M3 chiuso,
M4 indipendente); **sostituire CryptoPanic con un feed RSS funzionante** (es. CoinTelegraph/
Decrypt) come follow-up **prima di M7** (esperimento ufficiale) — richiederà aggiornamento di
ADR-0011/0013 + costante `_RSS_SOURCES` + un re-run smoke di validazione. Tracciato anche in
memoria di sessione (`m3-open-data-decisions`).

**Next**: M3 concluso con rigore. Procedere a M4 (ExecutionLayer, chiude D2) via Ralph loop.

---

## 2026-06-14 — M4-T01 `execution/sizing.py` — DONE

**Task**: TDD implementation of position sizing (Decimal-only, no float — invariant #12).

**What changed**:
- Created `src/aiat/execution/sizing.py`:
  - `PositionSizing` frozen dataclass (all-Decimal fields)
  - `compute_position_sizing()` function
  - Formulae: `initial_margin = equity × size_pct`, `size_units = margin / price`,
    `notional = price × size_units × leverage`, SL/TP prices for LONG/SHORT
- Created `tests/unit/execution/test_sizing.py` (13 tests):
  - PRD §9.2 assertions: `notional = price × size_units × leverage`
  - Decimal type checks for every field (no float leakage)
  - LONG/SHORT SL/TP price direction correctness
  - Immutability (frozen dataclass) test

**Verify output**:
```
13 passed in 0.07s   (pytest tests/unit/execution/test_sizing.py -q)
All checks passed!   (ruff check src tests)
Success: no issues found in 59 source files   (mypy src)
```

**Next**: M4-T02 — `execution/guardrails.py` (4 guardrail Strategia C+, closes when all cases pass in order).

---

## 2026-06-14 — M4-T02 `execution/guardrails.py` — DONE

**Task**: TDD implementation of 4 guardrail Strategia C+ (invariant #8 — never disableable).

**What changed**:
- Created `src/aiat/execution/guardrails.py`:
  - `GuardrailStrategy` Protocol (§7.4 interface)
  - `_force_hold(source)` helper: creates valid HOLD action preserving metadata
  - `Guardrails` class with `apply()` and `_apply_to_action()`:
    - G1: SL/TP mandatory — LONG/SHORT without SL or TP → force HOLD (defense-in-depth)
    - G2: size_pct clamp → max_size_pct (AIAT_MAX_SIZE_PCT default 0.20)
    - G3: leverage clamp → min(1 + confidence×9, hard_max) — quantized to 2dp ROUND_DOWN
    - G4: confidence gate — confidence < min_open_confidence → force HOLD
  - Returns (post-clamp TradeDecision, list[GuardrailReport] per action)
- Created `tests/unit/execution/test_guardrails.py` (24 tests):
  - Clean pass-through (no flags)
  - G1: SL missing, TP missing, both missing, G1 skips G2/G3
  - G2: size 0.50→0.20 clamp; at-limit not clamped; within-limit unchanged
  - G3: leverage 20→8.2 by confidence formula; clamped to hard_max; round-down at 0.01
  - G4: confidence 0.3→HOLD; at-threshold passes; above-threshold passes
  - Ordering: G1→G2→G3→G4 confirmed; all-four-in-sequence flags all set correctly
  - HOLD/FLAT unaffected by all guardrails
  - GuardrailReport structure: original_side preserved, isinstance checks

**Verify output**:
```
24 passed in 0.17s   (pytest tests/unit/execution/test_guardrails.py -q)
All checks passed!   (ruff check src tests)
Success: no issues found in 60 source files   (mypy src)
```

**Next**: M4-T03 — `execution/hyperliquid_client.py` (ABC + mock, semantica LONG/SHORT/FLAT/HOLD).

---

## M4-T03 — `execution/hyperliquid_client.py` (ABC + mock)

**Task**: TDD implementation of HyperliquidClient ABC + MockHyperliquidClient (§7.5).

**What changed**:
- Created `src/aiat/execution/hyperliquid_client.py`:
  - `OrderResult(BaseModel)`: Pydantic v2 model for a single HL order result, `extra="forbid"`, `status` as Literal of 6 values, all Decimal fields, `raw_response: dict[str, Any]`
  - `PositionClosureInfo(BaseModel)`: Pydantic v2 model for closed position info
  - `HyperliquidClient(ABC)`: 3 abstract async methods — `fetch_portfolio_state`, `execute_action(action, run_id, current_position)`, `check_position_closure`; docstring documents full side semantics per §7.5 fix A.2/B.4
  - `MockHyperliquidClient(HyperliquidClient)`: in-memory mock, implements full LONG/SHORT/FLAT/HOLD semantics:
    - HOLD → []
    - FLAT + no position → []
    - FLAT + position → [CLOSE order]
    - LONG/SHORT + no position → [ENTRY, STOP_LOSS, TAKE_PROFIT]
    - LONG/SHORT + same side → [] (no add-to-position in v2)
    - LONG/SHORT + opposite side → [CLOSE, ENTRY, STOP_LOSS, TAKE_PROFIT]
  - `executed_actions` list for test introspection
- Created `tests/unit/execution/test_hyperliquid_client.py` (26 tests):
  - OrderResult model validation (valid filled/triggered, extra fields forbidden, all 6 statuses)
  - PositionClosureInfo model validation
  - ABC cannot be instantiated
  - MockHyperliquidClient: portfolio state, HOLD, FLAT (no pos/with pos), LONG/SHORT (no pos, same side, opposite side), check_position_closure (known/unknown), executed_actions tracking

**Verify output**:
```
26 passed in 0.16s   (pytest tests/unit/execution/test_hyperliquid_client.py -q)
Success: no issues found in 61 source files   (mypy src)
All checks passed!   (ruff check src tests)
```

**Next**: M4-T04 — `execution/outcome_resolver.py` + ADR [D2] (HOLD/FLAT labeling rule).

---

## M4-T04 — `execution/outcome_resolver.py` + ADR-0014 [D2]

**Task**: TDD implementation of OutcomeResolver (§4.2) + close bounded deferral D2 (HOLD/FLAT outcome labeling rule).

**D2 Decision**: HOLD/FLAT `was_profitable_net = True` iff `|Δprice%| ≤ fee_roundtrip_pct` (fee-hurdle counterfactual). All PnL fields = Decimal("0"); `holding_duration_min = time_horizon_min`; `horizon_met = True` (by convention — passive choice maintained for full horizon). Default `fee_roundtrip_pct = 0.002` (0.1% taker × 2 sides). Rationale: simplest rule consistent with RESEARCH §2.1 confidence definition; avoids simulating hypothetical positions with arbitrary size/leverage parameters.

**What changed**:
- Created `src/aiat/execution/outcome_resolver.py`:
  - `PositionOutcomeInput(dataclass frozen)`: inputs for closed LONG/SHORT positions
  - `HoldFlatOutcomeInput(dataclass frozen)`: inputs for HOLD/FLAT decisions with price data
  - `OutcomeResult(dataclass frozen)`: computed outcome ready for OutcomesRepository
  - `OutcomeResolver.resolve_position()`: computes pnl_net_fee, pnl_net_fee_funding, was_profitable_net (strict >0), horizon_met (≤ time_horizon)
  - `OutcomeResolver.resolve_hold_flat()`: applies D2 fee-hurdle rule; all PnL=0; horizon_met=True
- Created `tests/unit/execution/test_outcome_resolver.py` (24 tests):
  - resolve_position: profitable, unprofitable, zero boundary, negative/positive funding, PnL consistency, tax_sim=0, horizon_met bounds, identity fields, large loss
  - resolve_hold_flat: below/above/at threshold, price up/down, no change, all PnL=0, holding_duration=time_horizon, horizon_met=True, identity fields, absolute symmetry
- Created `docs/decisions/0014-holdflat-outcome.md` (ADR for D2)
- Updated `docs/decisions/README.md` (indexed ADR-0014)

**Verify output**:
```
24 passed in 0.06s   (pytest tests/unit/execution/test_outcome_resolver.py -q)
All checks passed!   (ruff check src tests)
Success: no issues found in 62 source files   (mypy src)
VERIFY PASSED        (full task verify: pytest + ls ADR + grep README)
```

**Next**: M4-T05 — `db/repositories/positions.py`: PositionsRepository (🐘 Postgres integration).

---

## M4-T05 — `db/repositories/positions.py`: `PositionsRepository`

**Task**: TDD implementation of PositionsRepository (§7.6): `open_position` (positions + orders + fee_events), `close_position` (update position + create outcomes), `list_open_for_model`. Integration test on ephemeral Postgres.

**What changed**:
- Created `src/aiat/db/repositories/positions.py`:
  - `PositionsRepository.__init__(session: AsyncSession)`: no internal commit; caller owns UoW
  - `open_position(action_id, order_results, run_id) -> str`: reads DecisionAction, derives entry_price/size/leverage/SL-TP prices from action.stop_loss_pct/take_profit_pct; inserts Position, N Orders, M FeeEvents (one per order with fee_usd≠None); returns position_id
  - `close_position(position_id, closure, closing_run_id) -> None`: updates Position closing fields; sums fee_events + funding_events from DB; reads opening DecisionAction for confidence/time_horizon; inserts Outcome row (tax_sim=pnl_net_fee_funding for now, populated in M5)
  - `list_open_for_model(model_id) -> list[Position]`: filters closed_at IS NULL
  - `_fee_type(order_kind)`: ENTRY→"taker_open", others→"taker_close"
- Updated `src/aiat/db/repositories/__init__.py`: export PositionsRepository
- Created `tests/integration/test_db_repositories_positions.py` (6 tests):
  - test_open_position_creates_rows: verifies all Position fields (SL/TP computed correctly)
  - test_open_position_orders_and_fees_created: 3 orders, 1 fee_event with fee_type="taker_open"
  - test_close_position_updates_and_creates_outcome: verifies Position closing fields + Outcome PnL math
  - test_close_position_unprofitable: was_profitable_net=False when net PnL ≤ 0
  - test_duplicate_opening_action_raises_integrity_error: UNIQUE on opening_action_id → IntegrityError
  - test_list_open_for_model_returns_open_only: returns 1 open, 0 after close

**Verify output**:
```
6 passed in 6.31s   (pytest tests/integration/test_db_repositories_positions.py -v)
All checks passed!  (ruff check src tests)
Success: no issues found in 63 source files   (mypy src)
VERIFY PASSED
```

**Next**: M4-T06 — Coverage unit `execution/` (guardrails+sizing+resolver+hyperliquid mock).

---

## M4-T06 — Coverage unit `execution/` (guardrails+sizing+resolver)

**Task**: Verify and complete unit test coverage for all `execution/` modules (guardrails, sizing, outcome_resolver, hyperliquid_client mock) to pass the 95% gate.

**What changed**: No new files needed — coverage was already at 100% across all 5 execution modules as a result of M4-T01..T04 TDD implementation.

- `execution/__init__.py`: 100%
- `execution/guardrails.py`: 100% (42 stmts, 14 branches)
- `execution/hyperliquid_client.py`: 100% (56 stmts, 10 branches)
- `execution/outcome_resolver.py`: 100% (59 stmts)
- `execution/sizing.py`: 100% (23 stmts, 2 branches)

**Verify output**:
```
87 passed in 0.55s
TOTAL: 180 stmts, 0 miss, 26 branches, 0 branch-partial → 100%
```

**Next**: M4-T07 — Integration test extension for PositionsRepository (🐘 Postgres required).

---

## M4-T07 — Integration test extension for PositionsRepository

**Task**: Extend `tests/integration/test_db_repositories_positions.py` with the complete open→close→outcomes scenario including fee_event FK run_id verification, funding events in PnL, and `chk_position_closed_consistency` error case.

**What changed**:
- Extended `tests/integration/test_db_repositories_positions.py` (6 → 9 tests):
  - `test_fee_event_run_id_matches_opening_run`: verifies `fee_events.run_id`, `model_id`, `experiment_id` FK chain correctness from `open_position`
  - `test_close_position_with_funding_events`: inserts a `FundingEvent` manually, then `close_position` — verifies `sum_funding_usd=2.00`, `pnl_net_fee_usd=9.50`, `pnl_net_fee_funding_usd=7.50`
  - `test_close_position_consistency_check_enforced`: sets only `closed_at` on a position (leaving `exit_price/realized_pnl_usd/close_reason` NULL) → verifies `IntegrityError` from `chk_position_closed_consistency`

**Verify output**:
```
9 passed, 1 warning in 5.81s   (pytest tests/integration/test_db_repositories_positions.py -q)
All checks passed!  (ruff check src tests)
Success: no issues found in 63 source files   (mypy src)
VERIFY PASSED
```

**Next**: M4-T08 HUMAN-GATED 🛑 (wallet testnet) — skip; M4-T09 — Coverage `execution/` ≥95%.

---

## 2026-06-14 — M4-T09

**Task**: Coverage `execution/` ≥95% (gate CI core §9.1)

**What changed**: Ran coverage gate — M4-T06 already achieved 100% on all 5 execution modules (guardrails, hyperliquid_client, outcome_resolver, sizing, __init__). No new files needed.

**Verify output**:
```
87 passed in 0.66s
TOTAL: 180 stmts, 0 miss, 26 branches, 0 branch-partial → 100%
Required test coverage of 95% reached. Total coverage: 100.00%
```

**All non-human-gated M4 tasks complete**: M4-T01 through M4-T07 and M4-T09 ✅. M4-T08 is HUMAN-GATED 🛑 (wallet HL testnet reale — PRIMO STOP FISICO).

MILESTONE_COMPLETE M4

---

## 2026-06-14 — GATE M4 (orchestratore Opus): external gate + review adversariale

**Loop**: 8 iterazioni Sonnet, 1 task/commit pulito (M4-T01→T07, T09; T08 lasciato `[ ]`
HUMAN-GATED). ADR-0014 (holdflat-outcome) creato, chiude D2. ✅

**External gate `./tools/gate_check.sh M4`** (container + Postgres effimero): inizialmente
1 FAIL su `ruff format --check` (4 file non formattati — il verify per-task del loop non
lancia `ruff format --check`). Fix meccanico `uv run ruff format` (solo wrapping, nessuna
logica). Re-run → **GATE PASSED**: ruff/format/mypy --strict/lint-imports clean;
393 passed (cov globale 96.97%, soglia 80%); core 207 passed (cov 99.18%, soglia 95%);
integration 45 passed.

**Review adversariale (Workflow multi-agent, 13 agent, verifica scettica vs PRD ground
truth)** su inv #4/#8/#12 + D2 + correttezza moduli. 7 findings → 6 confermati, 1 respinto.

Bug isolati CONFERMATI e CORRETTI (con test, autonomo):
- **[CRITICAL] funding sign** `outcome_resolver.py:99`: faceva `pnl_net_fee + funding`
  (ADD), contraddicendo PRD §3.2.6 (`funding_amount_usd signed: + = paghi, - = ricevi` →
  va SOTTRATTO), §3.2.6 tax-sim (`gross - fees - funding`), e `positions.py:180` (corretto,
  SUBTRACT, confermato da integration test +2.00→7.50). Corrotto `pnl_net_fee_funding_usd`
  + `was_profitable_net` (label Brier, RESEARCH §4.2). **Fix**: `pnl_net_fee - funding`.
- **[HIGH] test invertito** `test_outcome_resolver.py`: 3 test (`-12→-3`, `+10→+6`, `-2→70`)
  codificavano la convenzione SBAGLIATA (positivo=ricevuto) e "verdeggiavano" il bug.
  **Fix**: rinominati/corretti a convenzione PRD (`-12`=ricevuto→+21, `+10`=pagato→-14,
  `-2`=ricevuto→74) + nuovo test di riconciliazione cross-path (resolver vs repo: +2.00→7.50).
- **[HIGH] was_profitable_net divergente**: conseguenza del sign bug; risolto dal fix sopra
  (operatore `>0` già identico nei due path). Coperto dal test di riconciliazione.
- **[MEDIUM] fallback size** `positions.py:58`: `filled_size_units or requested` tratta un
  fill reale `Decimal("0")` come falsy → riscriveva la size con la requested (size fabbricata,
  inv #12). **Fix**: `is not None` esplicito (un vero zero-fill ora fallisce loud via
  `chk_position_size_units_gt0`). + integration test del ramo fallback (filled=None→requested).

Risultato test: all tiers 391→393, core 206→207, integration 44→45. Tutti verdi.

Finding RESPINTO (1): "sizing test tautologico" — è un'osservazione di qualità del test, non
un difetto; il verificatore scettico l'ha correttamente respinto (premise ground-truth invertita).

**APERTO — DECISIONE DI DESIGN (NON corretto, richiede ADR + utente)**:
- **[HIGH] convenzione `size_units`** — `sizing.py` usa `size_units = margin/price`
  (unleveraged) con `notional = price·size_units·leverage`; `positions.py` usa
  `size_units = filled qty` (leveraged, dall'exchange) con `notional = size_units·price`.
  **Il PRD si auto-contraddice**: §9.2 riga 2346 avalla la formula di `sizing.py`, ma il
  DDL §3.2.4 + realtà di esecuzione (HL fill) avallano `positions.py`. Stesso nome di colonna,
  due semantiche → tocca notional/exposure/PnL (validità scientifica). `sizing.py` è **dead
  code** oggi (chiamato solo dai suoi test, mai da `positions.py`/servizi) → NON blocca il gate
  M4 (DoD moduli soddisfatto), ma va risolto PRIMA che M5 cabli `sizing.py`. Correlato: finding
  DEFERRED su `MockHyperliquidClient._open_orders` che mette `size_pct` in `requested_size_units`
  (conversione `size_pct→size_units` è lavoro M5/decision_loop, non ancora esistente).
  Regola CLAUDE.md (deviazione PRD ambigua + validità scientifica) → **chiedo all'utente** +
  ADR. Raccomandazione: adottare la convenzione leveraged di `positions.py` (= ciò che HL
  riempie davvero), ADR che devia da §9.2 riga 2346, fix `sizing.py`.

**Next**: M4 PASSED committato e pushato. STOP FISICO: (a) M4-T08 wallet HL testnet reale;
(b) decisione convenzione `size_units` (sopra). Entrambi richiedono input umano.

---

## 2026-06-14 — convenzione `size_units` RISOLTA (utente) → ADR-0015

**Decisione utente**: convenzione **leveraged** (= `positions.py`). `size_units` = quantità
eseguita on-chain (leveraged). `sizing.py` corretto: `notional = margin·leverage`,
`size_units = notional/price` (era `size_units = margin/price`, `notional = price·units·leverage`).
**ADR-0015** creato (devia da §9.2 r.2346, superata; prevalgono §3.2.4 DDL + §7.5) + README.

**Fix applicati**: `execution/sizing.py` (formula + docstring); `tests/unit/execution/
test_sizing.py` (4 assert riconvenzionati: es. margin 100/lev 2 → notional 200, **size_units
2** non 1; evitato round-trip Decimal lossy asserendo le relazioni esatte
`notional=margin·lev` e `size_units=notional/price`).

**Rinviato a M5 (in ADR-0015)**: `MockHyperliquidClient._open_orders` mette ancora
`size_pct` in `requested_size_units` (placeholder, NON percorso dati reale — il decision_loop
M5 non esiste); la conversione `size_pct→size_units` via `compute_position_sizing` va fatta
quando M5 cabla `execute_action→open_position` + client HL reale. (Scoping: il mock non tocca
la validità scientifica oggi; fix-now solo il vero disallineamento sizing.py↔positions.py.)

**Verify**: `./tools/gate_check.sh M4` → **GATE PASSED** (393/207/45, cov 96.97%/99.18%).

**Strategia (discussa con l'utente)**: si conferma l'approccio **gated incrementale**
(verify+debug per milestone), NON "scrivi tutta M5 poi testa alla fine" — il bug funding-sign
appena intercettato al gate M4 è la prova del valore della verifica incrementale. I test reali
(API/testnet/ENV) restano gate umani fuori dal container. M4-T08: 1 wallet testnet basta per
lo smoke; servono 4 wallet (1/modello, isolamento inv #1/#13) solo per M6/M7.

---

## 2026-06-14 — AUDIT COMPLETO M0-M4 (richiesto dall'utente) → 1 fix isolato

**Audit adversariale** (Workflow, 13 agent, 7 dimensioni: domain-schemas, db-models/migrations,
llm-layer, context-layer, execution-postfix, cross-cutting inv #10/#11/#12/#14, scientific-validity)
con verifica scettica vs PRD/ADR/RESEARCH. **6 findings → 1 confermato (fix), 5 respinti.**

**CONFERMATO e CORRETTO (medium — non rischio tesi, ma incoerenza reale):**
- `positions.py:212` `close_position` scriveva `pnl_net_fee_funding_tax_sim_usd =
  pnl_net_fee_funding_usd`, mentre `OutcomeResolver` (resolve_position:115, resolve_hold_flat:153)
  e ADR-0014 (r.55, "popolato da tax sim, mai dal resolver") impongono **`Decimal("0")`**. Due
  writer della stessa colonna in disaccordo (stessa classe del bug funding-sign). **Fix**:
  `= Decimal("0")` + docstring + assert nell'integration test. (NB: la colonna per-position è
  "future work, non usata per tesi" — PRD §3.2.7 r.797; RQ1 post-tax usa l'aggregato
  `tax_sim_periods`, non questa colonna → niente impatto su validità scientifica, solo coerenza.)
  Mantenuto il write dell'outcome in close_position (mandato da PRD §9.3 + DoD M4).

**RESPINTI (5, verificati a mano — rifiuti fondati):**
- Pydantic "no strict=True" → falso difetto: il path reale usa `Decimal(str(value))`; strict=True
  romperebbe il parsing JSON LLM (`model_validate(json.loads(...))`); "strict" nel PRD = filosofia
  contratti tipati (extra='forbid'), non il flag. decimal_places fa da backstop.
- status `partial` irraggiungibile nel context snapshot → enhancement, non difetto (ADR-0011 tollera
  il degrado news; partial/success di inv #15 è per le `runs`, non `context_build_runs`; source_timestamps
  persistiti = ricostruibile).
- pubDate malformato → `datetime.now()` fallback → trade-off **esplicitamente accettato** in ADR-0011
  (r.84-89) con test dedicato; l'hash è deterministico sul payload persistito (build once per tick).
- close_position scrive `outcomes` = violazione bounded context → falso: PRD §9.3 r.2373 + DoD M4
  r.3272/3275 lo **mandano** esplicitamente in M4.
- tax-sim divergence "bias RQ1" → respinto come rischio-tesi (RQ1 usa l'aggregato, non la colonna
  per-row); è la stessa cosa del fix sopra, confermata come incoerenza ma non come rischio scientifico.

**Verify**: `./tools/gate_check.sh M4` → **GATE PASSED** (393/207/45, cov 96.97%/99.18%).
**Esito**: M0-M4 confermati stabili e coerenti col PRD/ADR; nessun rischio di validità scientifica
residuo trovato. Pronti per M5 quando l'utente dà il via (+ scaffolding M4-T08 a richiesta).

---

## 2026-06-14 — M5-T01: DecisionsRepository

**Task**: `db/repositories/decisions.py` — `DecisionsRepository` §7.6 (inv #4).

**What changed**:
- Created `src/aiat/db/repositories/decisions.py` with `DecisionsRepository`:
  - `persist_decision`: atomic INSERT decisions → decision_actions(3) → cost_events → llm_invocations with `flush()` for intermediate IDs; no internal commit.
  - `get_by_run(run_id)` → `Decision | None`
  - `get_action_history(model_id, symbol, since)` → `list[DecisionAction]` ordered by `created_at DESC`
- `pricing_snapshot` Decimals converted to `str` for JSONB JSON-serializability.
- `original_side` populated only when `forced_hold=True` (guards guardrail override audit trail).
- Created `tests/integration/test_db_repositories_decisions.py` — 12 tests:
  - Happy path: 4-row atomic create verified (decision + 3 actions + cost_event + llm_invocation)
  - requested vs executed fields mapping
  - forced_hold sets original_side correctly
  - Rollback: duplicate run_id → IntegrityError (UNIQUE on decisions.run_id)
  - FK violation: non-existent run_id → IntegrityError
  - UNIQUE check: duplicate (decision_id, symbol) in decision_actions → IntegrityError
  - cost_event.decision_id FK validity (inv #4 proven by SELECT)
  - get_by_run happy path + unknown run returns None
  - get_action_history: model/symbol filter, since filter, multi-run aggregation

**Verify**: `uv run pytest tests/integration/test_db_repositories_decisions.py -v`
```
12 passed in 5.77s
```
Full suite: `406 passed, 97% coverage`.

---

## 2026-06-14 — M5-T02a: SnapshotsRepository + RunsRepository

**Task**: `db/repositories/snapshots.py` + `db/repositories/runs.py` — §7.6.

**What changed**:
- Created `src/aiat/db/repositories/snapshots.py` with `SnapshotsRepository`:
  - `persist_account_snapshot(run_id, portfolio_state)`: fetches the Run row for experiment_id/model_id; computes portfolio_state_hash (SHA-256 of model_dump_json); computes total_position_value_usd as sum(current_price × size_units); inserts AccountSnapshot. No internal commit.
  - `get_context_snapshot(experiment_id, tick_id)`: SELECT on context_snapshots; returns None if absent.
- Created `src/aiat/db/repositories/runs.py` with `RunsRepository`:
  - `create_run(...)`: inserts Run with status='running', run_started_at=now(), all required fields. Returns run_id str.
  - `update_status(run_id, status, failure_stage)`: sets status + run_completed_at for terminal statuses; raises ValueError if run not found.
  - `log_error(...)`: inserts Error row with all nullable FKs; no run required.
- Updated `src/aiat/db/repositories/__init__.py` to export `DecisionsRepository`, `RunsRepository`, `SnapshotsRepository`.
- Created `tests/integration/test_db_repositories_snapshots_runs.py` — 16 tests:
  - AccountSnapshot: creates row, portfolio_state_hash, total_position_value_usd (BTC 51000×0.01=510), inherits experiment/model, run_id UNIQUE constraint, missing run raises ValueError
  - get_context_snapshot: returns existing, None if tick missing, None if experiment mismatch
  - create_run: creates row with running status, duplicate (exp,model,sched) → IntegrityError
  - update_status: SUCCESS sets completed_at, FAILED sets failure_stage, unknown run raises ValueError
  - log_error: inserts with nullable FKs, inserts with run FK linked

**Verify**: `uv run pytest tests/integration/test_db_repositories_snapshots_runs.py -q`
```
16 passed in 6.06s
```
ruff clean, mypy clean (66 source files).

---

## 2026-06-14 — M5-T02b: OutcomesRepository

**Task**: `db/repositories/outcomes.py` — §7.6.

**What changed**:
- Created `src/aiat/db/repositories/outcomes.py` with `OutcomesRepository`:
  - `persist_outcome(...)`: inserts an Outcome row with all §3.2.7 fields. No internal commit. Returns outcome_id str UUID.
  - `list_for_model_in_window(model_id, start, end)`: returns Outcomes by model_id within a created_at time window, ordered ascending.
- Updated `src/aiat/db/repositories/__init__.py` to export `OutcomesRepository`.
- Created `tests/integration/test_db_repositories_outcomes.py` — 9 tests:
  - persist_outcome: success, correct field values
  - duplicate position_id → IntegrityError (UNIQUE constraint)
  - confidence out of range → IntegrityError (CHECK)
  - sum_fees_usd < 0 → IntegrityError (CHECK)
  - time_horizon_min=0 → IntegrityError (CHECK)
  - list_for_model_in_window: returns outcome in window
  - list_for_model_in_window: excludes other model_id
  - list_for_model_in_window: excludes outcomes outside window
  - list_for_model_in_window: ordering (ascending)

**Verify**: `uv run pytest tests/integration/test_db_repositories_outcomes.py -q`
```
9 passed in 5.80s
```
ruff clean, mypy clean (67 source files).

---

## 2026-06-14 — M5-T02c: BaselineRepository + TaxSimulationRepository

**Task**: `db/repositories/baselines.py` + `db/repositories/tax_simulation.py` — §7.6.

**What changed**:
- Created `src/aiat/db/repositories/baselines.py` with `BaselineRepository`:
  - `register_baseline_config(experiment_id, baseline_name, config_json)`: inserts BaselineConfig with SHA-256 of canonical JSON as config_hash. Returns baseline_config_id str UUID.
  - `get_baseline_config(experiment_id, baseline_name)`: SELECT by (experiment_id, baseline_name); returns None if absent.
  - `persist_equity_snapshot(baseline_config_id, tick_id, tick_at, equity_usd, pnl_usd_cumulative, raw_state)`: looks up BaselineConfig to derive experiment_id + baseline_name, then inserts BaselineEquitySnapshot. Raises ValueError if config not found.
  - `list_equity_history(experiment_id, baseline_name)`: returns snapshots ordered by tick_at ASC.
- Created `src/aiat/db/repositories/tax_simulation.py` with `TaxSimulationRepository`:
  - `compute_and_persist_period(...)`: aggregates Outcome objects → total_pnl_gross, total_fees, total_funding; applies §4.3 algebraic compensation (taxable_base = max(0, net)); persists TaxSimPeriod. Returns period_id.
  - `list_for_model(model_id)`: returns periods ordered by period_start ASC.
- Updated `src/aiat/db/repositories/__init__.py` to export both new repositories.
- Created `tests/integration/test_db_repositories_baselines_tax.py` — 15 tests:
  - BaselineRepository (9): register success+hash determinism, get found+not_found, duplicate raises, invalid name raises, persist_equity_snapshot success+invalid_config_raises, list_equity_history ordered
  - TaxSimulationRepository (6): compute with profit (correct aggregation), compute with net loss (taxable_base clamped to 0), empty outcomes (all zeros), list_for_model ordered, list excludes other model, duplicate quarter raises

**Verify**: `uv run pytest tests/integration/test_db_repositories_baselines_tax.py -q`
```
15 passed in 6.06s
```
ruff clean, mypy clean (69 source files).

---

## 2026-06-14 — M5-T03: config/settings.py — Settings per ruolo (least privilege)

**Task**: Full `BaseAIATSettings` / `AgentSettings` / `ContextOrchestratorSettings` + `load_settings()` — PRD §10.3, fix B.13.

**What changed**:
- Replaced stub `settings.py` (BaseModel-only) with full pydantic-settings implementation:
  - `BaseAIATSettings(BaseSettings)`: common fields (`experiment_id`, `git_commit_sha`, `database_url` SecretStr, `network` locked to "testnet", `log_level`, `service_role`); `env_prefix="AIAT_"`, `extra="forbid"`
  - `AgentSettings(BaseAIATSettings)`: full agent fields (model_id, prompt_template_hash, llm_provider Literal, model_name_api, temperature/top_p/max_tokens/seed optional, all 4 LLM API keys as `SecretStr | None`, `llm_gateway`+`openrouter_api_key` (ADR-0008), `hl_wallet_private_key`+`hl_wallet_address`, guardrail Decimal defaults, inject_decision_history=False); `validate_api_key_matches_provider` validator (skips check for openrouter gateway; validates provider key presence for direct mode)
  - `ContextOrchestratorSettings(BaseAIATSettings)`: lean class — no LLM keys, no wallet; `extra="forbid"` rejects any agent-specific extras
  - `load_settings()`: dispatches on AIAT_SERVICE_ROLE env var
- Updated `src/aiat/llm/factory.py`: handles `SecretStr | None` (`.get_secret_value()` after assert-not-None) and `Decimal | None`/`int | None` for temperature/max_tokens (fallback defaults)
- Updated `tests/unit/llm/test_factory.py`: `_base_settings` helper now includes all required fields; `test_load_llm_unknown_provider_raises` uses `MagicMock(spec=AgentSettings)` with all accessed attrs to test defensive `case _:` branch
- Updated `tests/integration/test_llm_providers.py`: `_OPENROUTER_SETTINGS_BASE` includes all required fields
- Created `tests/unit/config/__init__.py` and `tests/unit/config/test_settings.py` (22 tests)

**Notes**:
- `.env` has `AIAT_LLM_GATEWAY=openrouter` — `ContextOrchestratorSettings` (extra="forbid") rejects these agent-specific vars if loaded from .env; tests use `_env_file=None` kwarg (pydantic-settings v2 per-instantiation override) to simulate clean orchestrator environment
- `load_settings` dispatch tests use `unittest.mock.patch` to avoid .env interference

**Verify**: `uv run pytest tests/unit/config/test_settings.py -q && uv run mypy src`
```
22 passed in 0.25s
Success: no issues found in 69 source files
```
ruff clean, mypy clean (69 source files). All unit tests: 370 passed.

---

## 2026-06-14 — M5-T04: orchestration/lifecycle.py — startup_checks (§10.1)

**Task**: `startup_checks` dispatcher + role-specific checks A1-A10 (agent) and O1-O4 (orchestrator).

**What changed**:
- Created `src/aiat/orchestration/lifecycle.py`:
  - `EXPECTED_ALEMBIC_VERSION = "002"`, `EXPECTED_BASELINES` frozenset of 3 baselines
  - `_db_session(settings)` — `@asynccontextmanager` helper wrapping `get_db_session` (adapts PRD pseudocode to actual `session.py` interface which takes URL string and returns `async_sessionmaker`, not an async CM directly)
  - `startup_checks()` — dispatcher: common checks → isinstance dispatch to agent or orchestrator branch
  - `_check_network_testnet` — invariant #9 (pure, testable)
  - `_check_db_connectivity_and_schema` — alembic version check
  - `_check_active_experiment` — experiment exists + not ended; SHA mismatch = warning only
  - `_agent_startup_checks` — A1/A2/A3 (model/provider/wallet), A4 (pricing YAML), A5 (prompt template), A6 (`_check_hl_reachability`, uses MockHyperliquidClient placeholder until M6), A7 (`_check_llm_credentials`, uses `load_llm().invoke()` mock in tests), A8 (guardrail Decimal validity), A9 (memory off, invariant #5), A10 (baseline presence, fatal)
  - `_orchestrator_startup_checks` — O1 (env-var leak detection via `os.environ`), delegates O2-O4 to `_check_orchestrator_sources`
  - `_check_orchestrator_sources` — O2 (HL info endpoint), O3 (RSS via NewsCollector), O4 (F&G via SentimentCollector); all mockable in unit tests
- Created `tests/unit/orchestration/test_lifecycle.py` (24 tests, TDD):
  - `_check_network_testnet`: 3 tests (ok, rejects mainnet, rejects arbitrary)
  - `_check_db_connectivity_and_schema`: 2 tests (ok, mismatch)
  - `_check_active_experiment`: 4 tests (ok, not found, ended, sha-mismatch = warn not raise)
  - `_agent_startup_checks`: 10 tests (A2 provider mismatch, A3 wallet mismatch, A5 template missing, A8 max_size=0, A8 leverage=0, A8 valid, A9 memory on, A9 memory off, A10 missing, A10 present)
  - `_orchestrator_startup_checks`: 3 tests (O1 MODEL_ID leaked, O1 WALLET_PRIVATE_KEY leaked, O1 clean env)
  - `startup_checks`: 2 dispatch tests (agent, orchestrator)
- Design note: `_db_session` is a private helper so tests can patch it cleanly for each DB call (A1/A2/A3, A5, A10 each need a separate session). `_check_hl_reachability` and `_check_llm_credentials` are also separate private functions for the same reason.

**Verify**:
```
24 passed in 0.35s
Success: no issues found in 70 source files
ruff clean, lint-imports clean
394 passed, 2 warnings in 6.35s (all unit tests)
```

---

## M5-T05 — `orchestration/scheduler.py`: APScheduler (2026-06-14)

**Task**: Build APScheduler factories `build_scheduler_for_orchestrator` and `build_scheduler_for_agent` with correct CronTrigger config (PRD §4.1).

**What changed**:
- Created `src/aiat/orchestration/scheduler.py`:
  - `_JOB_DEFAULTS = {'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 60}`
  - `_CRON_MINUTE = "0,15,30,45"` (constant, both roles use the same quarter-hour marks)
  - `_unbound_orchestrator_tick` / `_unbound_agent_tick` — RuntimeError placeholders when no `tick_job` is passed (production wiring happens in M5-T07 `__main__.py`)
  - `build_scheduler_for_orchestrator(settings, tick_job=None)` → `AsyncIOScheduler` with `CronTrigger(minute='0,15,30,45', second=0)`
  - `build_scheduler_for_agent(settings, tick_job=None)` → `AsyncIOScheduler` with `CronTrigger(minute='0,15,30,45', second=settings.agent_start_delay_seconds)` (default 30s)
- Created `tests/unit/orchestration/test_scheduler.py` (19 tests, TDD):
  - Constant validation: `_JOB_DEFAULTS` values
  - Orchestrator: returns `AsyncIOScheduler`, 1 job, `CronTrigger` type, `_job_defaults` coalesce/max_instances/misfire_grace_time, trigger repr `"minute='0,15,30,45'"` + `"second='0'"`
  - Agent: same defaults, trigger `"second='30'"`, respects custom delay (45s test), differs from orchestrator

**Design notes**:
- `tick_job` optional parameter enables TDD without `decision_loop.py` existing yet (M5-T06 will implement `run_once`). Production `__main__.py` (M5-T07) passes the bound callable.
- APScheduler 3.x `job.coalesce` / `job.max_instances` are `__slots__` attributes only set when the scheduler runs jobs — tests check `scheduler._job_defaults` instead (correct for configuration testing).
- Used `_env_file=None` pattern for `ContextOrchestratorSettings` fixtures (same pattern as `test_settings.py`) to prevent the dev `.env` (which has `AIAT_LLM_GATEWAY=openrouter`) from triggering `extra="forbid"` on the orchestrator model.
- Trigger repr verified: `cron[minute='0,15,30,45', second='0']` (orchestrator) and `cron[minute='0,15,30,45', second='30']` (agent).

**Verify**:
```
19 passed in 0.24s
Success: no issues found in 71 source files
ruff clean, lint-imports clean
413 passed, 2 warnings in 8.20s (all unit tests)
```

---

## 2026-06-14 — M5-T06

**Task**: `orchestration/decision_loop.py` — 10-step decision loop (PRD §4.1)  
**Changed**:
- Created `src/aiat/orchestration/decision_loop.py`: `DecisionLoop` class with `run_once(tick_id, scheduled_for)` executing the full PRD §4.1 10-step sequence. Key internals: `_render_prompt` (template + market context + portfolio + confidence_def → sha256 hash), `_wait_for_context_snapshot` (3 retries × 5s), `_execute_actions` (HOLD skipped; LONG/SHORT/FLAT executed via HL client + positions persisted), `_check_pending_closures` (SL/TP triggers), `_finalize_run` (fresh session for timeout/error handlers).
- Used mutable `list[str | None]` holder pattern so `_execute_tick` can propagate `run_id` to the timeout handler immediately after `create_run`, before the LLM call that may block.
- Created `tests/unit/orchestration/test_decision_loop.py`: 15 unit tests covering happy path, missed tick (with retry count verification), timeout (run_id propagated via holder), guardrail params, LONG execution, step ordering (context → portfolio → run → LLM → persist → success), and `RuntimeError` on missing prompt template.

**Design notes**:
- PRD §4.1 lists `create_run` as step 1, but `Run.context_snapshot_id` is NOT NULL FK — reordered to: context_snapshot first (step 3), portfolio (step 2), render prompt (step 4), create run (step 1 — last before LLM).
- The `nonlocal run_id` pattern from the summary was insufficient since `run_id` was local to `_execute_tick`. Fixed by `_run_id_holder: list[str | None]` shared between `run_once` and `_execute_tick`.
- Invariant #4 satisfied: `cost_events` persisted inside `persist_decision` atomic transaction (same commit).
- Invariant #13 satisfied: missed tick returns `None` and logs error, never uses stale snapshot.

**Verify**:
```
15 passed in 1.65s
Success: no issues found in 72 source files
ruff clean
428 passed, 2 warnings in 8.13s (all unit tests)
```
