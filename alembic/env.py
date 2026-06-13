"""Alembic migration env — reads AIAT_DATABASE_URL from environment (§3.3).

Migrations run synchronously via the psycopg driver even though the app uses
asyncpg at runtime. This avoids asyncpg's known issue with PREPARE + DDL
containing JSONB server_default literals.
"""

import os
from logging.config import fileConfig

from sqlalchemy import create_engine, pool

# Import Base — must happen AFTER all model modules are imported so that
# Base.metadata contains all 20 tables (§3.2.1-§3.2.9).
import aiat.db.models  # noqa: F401 — registers all 20 models on Base.metadata
from aiat.db.models.base import Base
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Override the URL from the environment variable (§11.4).
# Always convert to psycopg (sync) so migrations don't use asyncpg prepared stmts.
_db_url = os.environ.get("AIAT_DATABASE_URL", config.get_main_option("sqlalchemy.url") or "")
_sync_url = _db_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
config.set_main_option("sqlalchemy.url", _sync_url)


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (generates SQL script)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live psycopg (sync) connection."""
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
