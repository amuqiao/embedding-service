from fastapi import Header

from app.core.exceptions import UnauthorizedError
from app.infrastructure.config import settings


async def require_service_auth(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError()
    token = authorization.removeprefix("Bearer ").strip()
    if not token or token != settings.SERVICE_API_KEY:
        raise UnauthorizedError()
    return "default"
