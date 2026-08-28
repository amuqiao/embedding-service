from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.logging import configure_logging
from app.tasks.runtime import ensure_worker_runtime_initialized

logger = logging.getLogger(__name__)

RunOnce = Callable[[AsyncSession], Awaitable[dict[str, Any]]]


async def run_once_with_session(run_once: RunOnce) -> dict[str, Any]:
    engine = create_async_engine(settings.database.url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            return await run_once(db)
    finally:
        await engine.dispose()


async def _run_role_loop(
    *,
    role: str,
    run_once: Callable[[], Awaitable[dict[str, Any]]],
    interval_seconds: int,
) -> None:
    while True:
        result = await run_once()
        logger.info("%s_loop_completed result=%s", role, result)
        await asyncio.sleep(interval_seconds)


def run_role_cli(
    *,
    role: str,
    run_once: Callable[[], Awaitable[dict[str, Any]]],
    default_interval_seconds: int,
    argv: list[str] | None = None,
) -> None:
    parser = argparse.ArgumentParser(prog=f"python -m app.runtime.{role}")
    parser.add_argument("command", choices=("once", "loop"))
    parser.add_argument("--interval-seconds", type=int, default=default_interval_seconds)
    args = parser.parse_args(argv)
    if args.interval_seconds <= 0:
        raise SystemExit("--interval-seconds must be greater than 0")

    configure_logging()
    ensure_worker_runtime_initialized()

    if args.command == "once":
        result = asyncio.run(run_once())
        logger.info("%s_once_completed result=%s", role, result)
        return

    asyncio.run(_run_role_loop(role=role, run_once=run_once, interval_seconds=args.interval_seconds))
