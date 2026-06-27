from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db_engine() -> AsyncEngine:
    global _engine, _session_factory

    if _engine is not None:
        return _engine
    connect_args = {} if settings.database.ssl else {"ssl": False}
    _engine = create_async_engine(
        settings.database.url,
        pool_pre_ping=True,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        pool_recycle=settings.database.pool_recycle,
        connect_args=connect_args,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def close_db_engine() -> None:
    global _engine, _session_factory

    if _engine is None:
        return
    engine = _engine
    _engine = None
    _session_factory = None
    await engine.dispose()


def get_db_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("database engine is not initialized")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("database session factory is not initialized")
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_factory()() as session:
        yield session
