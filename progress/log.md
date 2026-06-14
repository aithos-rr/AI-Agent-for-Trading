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
