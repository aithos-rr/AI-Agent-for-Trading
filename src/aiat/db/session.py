"""Async SQLAlchemy engine and session factory (§1.2, §10.1)."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def get_db_session(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given database URL.

    Args:
        database_url: asyncpg-compatible PostgreSQL URL
            (e.g. ``postgresql+asyncpg://user:pw@host/db``).

    Returns:
        An ``async_sessionmaker`` producing ``AsyncSession`` instances with
        ``expire_on_commit=False`` (safe for async context).
    """
    engine = create_async_engine(database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
