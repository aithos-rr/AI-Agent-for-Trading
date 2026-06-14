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
