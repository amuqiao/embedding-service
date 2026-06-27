import pytest

from app.core.database import close_db_engine, get_db_engine, get_session_factory, init_db_engine


@pytest.mark.asyncio
async def test_database_session_factory_requires_explicit_initialization():
    await close_db_engine()

    with pytest.raises(RuntimeError, match="session factory is not initialized"):
        get_session_factory()
    with pytest.raises(RuntimeError, match="engine is not initialized"):
        get_db_engine()


@pytest.mark.asyncio
async def test_database_engine_initialization_is_idempotent_and_closable():
    await close_db_engine()

    engine = init_db_engine()

    assert init_db_engine() is engine
    assert get_db_engine() is engine
    assert get_session_factory() is not None

    await close_db_engine()
    with pytest.raises(RuntimeError, match="engine is not initialized"):
        get_db_engine()
