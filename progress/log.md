# Progress Log — AI Trading Agent V2

---

## 2026-06-13 — M0-T01

**Task**: `pyproject.toml` con `uv` + dipendenze  
**Changed**: Creato `/workspace/pyproject.toml` con tutte le runtime deps (langchain-core, langchain-openai, langchain-anthropic, pydantic>=2, pydantic-settings, sqlalchemy[asyncio]>=2, asyncpg, alembic, apscheduler<4, hyperliquid-python-sdk, httpx, structlog, pandas, numpy, pandas-ta, tenacity, pyyaml) e dev deps in `[dependency-groups].dev` (pytest, pytest-asyncio, pytest-cov, pytest-postgresql, vcrpy, ruff, mypy, import-linter).  
**Result**: `uv sync` esce 0, tutti gli import chiave OK.  
**Learnings**: Usato `[dependency-groups]` (PEP 735) invece di `[project.optional-dependencies]` per dev deps — uv sync installa entrambi per default.  
**Next**: M0-T02 (generare uv.lock e committarlo — già generato da uv sync).

---
