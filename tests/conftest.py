"""Pytest fixtures for integration and e2e tests (§9.3)."""

import glob
import os
import shutil

import psycopg
import pytest
import pytest_asyncio
from alembic.config import Config
from pytest_postgresql.factories import postgresql_proc
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command


def _resolve_pg_ctl() -> str:
    """Locate the ``pg_ctl`` binary across environments (§9.3).

    ``pytest_postgresql`` launches its *own* ephemeral PostgreSQL cluster from
    local binaries — it does not connect to an external server — so it needs an
    absolute path to ``pg_ctl``. That path differs per environment, so resolve
    it dynamically instead of hardcoding one major version:

    1. ``PATH`` (``shutil.which``) — the devcontainer puts PG15 on PATH.
    2. Debian/Ubuntu layout ``/usr/lib/postgresql/<ver>/bin/pg_ctl`` — CI
       ``ubuntu-latest`` installs the server there (e.g. PG16) but *not* on
       PATH; pick the highest major version present.
    3. Fallback to the historical devcontainer path (PG15).
    """
    found = shutil.which("pg_ctl")
    if found:
        return found
    candidates = glob.glob("/usr/lib/postgresql/*/bin/pg_ctl")
    if candidates:

        def _major(path: str) -> int:
            version = path.split("/usr/lib/postgresql/")[1].split("/", 1)[0]
            return int(version.split(".")[0])

        return max(candidates, key=_major)
    return "/usr/lib/postgresql/15/bin/pg_ctl"


# Ephemeral PostgreSQL process — port=None for auto-selection (§9.3).
# Session-scoped: Postgres server starts once per test session.
postgresql_proc_fixture = postgresql_proc(
    port=None,
    executable=_resolve_pg_ctl(),
)


def _create_db(proc: object, dbname: str) -> None:  # type: ignore[misc]
    """Create *dbname* on the ephemeral server (connecting via postgres admin db)."""
    p = proc  # type: ignore[attr-defined]
    with psycopg.connect(
        host=p.host, port=p.port, user=p.user, dbname="postgres", autocommit=True
    ) as conn:
        conn.execute(f"CREATE DATABASE {dbname}")


@pytest.fixture(scope="session")
def db_url(postgresql_proc_fixture: object) -> str:  # type: ignore[override]
    """Apply alembic migrations to ephemeral Postgres; return asyncpg URL (§9.3).

    Session-scoped: migrations run once; db_session rollbacks provide test isolation.
    """
    _create_db(postgresql_proc_fixture, "aiat_tests")
    p = postgresql_proc_fixture  # type: ignore[attr-defined]
    asyncpg_url = f"postgresql+asyncpg://{p.user}@{p.host}:{p.port}/aiat_tests"
    os.environ["AIAT_DATABASE_URL"] = asyncpg_url
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", asyncpg_url)
    command.upgrade(cfg, "head")
    return asyncpg_url


@pytest_asyncio.fixture(scope="function")
async def db_session(db_url: str) -> AsyncSession:  # type: ignore[misc]
    """Async session with rollback teardown for test isolation (§9.3)."""
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


# VCR config for LLM integration tests (§9.4).
# Tests use @pytest.mark.vcr to select their cassette.
#
# CRITICAL: pytest-recording resolves this via request.getfixturevalue("vcr_config"),
# so it MUST be a pytest *fixture*. A plain module-level dict is silently ignored —
# the plugin falls back to its own empty-dict default (pytest_recording/plugin.py),
# which would drop record_mode/filter_headers/match_on/cassette_library_dir entirely
# (verified: with a module global, the effective vcr_config is {} and record_mode
# stays "none" even with VCR_RECORD_MODE=once set).
#
# record_mode is env-overridable so the same code records and replays (ADR-0008,
# M2-T12): record real cassettes via OpenRouter with VCR_RECORD_MODE=once on a
# network without firewall; default "none" replays in CI/devcontainer (no network,
# no recording). filter_headers and match_on are kept fixed — never persist API
# keys to cassettes, and match requests precisely (incl. body).
@pytest.fixture
def vcr_config() -> dict[str, object]:
    return {
        "cassette_library_dir": "tests/cassettes",
        "record_mode": os.environ.get("VCR_RECORD_MODE", "none"),
        "filter_headers": ["authorization", "x-api-key"],
        "match_on": ["method", "scheme", "host", "port", "path", "query", "body"],
        # Store decompressed response bodies (strip Content-Encoding: gzip) so
        # cassettes are human-readable, git-diffable, and easy to hand-author for
        # the synthetic error/fallback scenarios (M2-T12).
        "decode_compressed_response": True,
    }
