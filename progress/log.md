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
