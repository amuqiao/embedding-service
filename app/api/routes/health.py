import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health")
async def health():
    return {"status": "ok", "service": settings.SERVICE_NAME, "version": "1.0.0"}


@router.get("/healthz", include_in_schema=False)
async def healthz():
    checks: dict = {}
    ok = True

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        logger.warning("health_check_db_failed error=%s", exc)
        checks["db"] = "error"
        ok = False

    try:
        from redis.asyncio import Redis

        redis = Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        try:
            await asyncio.wait_for(redis.ping(), timeout=2)
        finally:
            await redis.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.warning("health_check_redis_failed error=%s", exc)
        checks["redis"] = "error"
        ok = False

    if not ok:
        return JSONResponse(status_code=503, content={"status": "degraded", **checks})
    return {"status": "ok", **checks}
