import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.infrastructure.config import settings
from app.infrastructure.database import engine

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health")
async def health():
    return {"status": "ok", "service": "novel-localization-ai", "version": "1.0.0"}


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
        import urllib.parse as _up
        parsed = _up.urlparse(settings.REDIS_URL)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 6379
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=2)
        writer.close()
        await writer.wait_closed()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.warning("health_check_redis_failed error=%s", exc)
        checks["redis"] = "error"
        ok = False

    if not ok:
        return JSONResponse(status_code=503, content={"status": "degraded", **checks})
    return {"status": "ok", **checks}
