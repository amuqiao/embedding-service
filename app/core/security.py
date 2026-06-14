import logging
import secrets

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import UnauthorizedError
from app.core.config import settings

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)


async def require_service_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        logger.warning("auth_failed reason=missing_bearer")
        raise UnauthorizedError()
    if not settings.SERVICE_API_KEY:
        logger.warning("auth_failed reason=service_key_not_configured")
        raise UnauthorizedError()
    if not secrets.compare_digest(credentials.credentials, settings.SERVICE_API_KEY):
        logger.warning("auth_failed reason=invalid_api_key")
        raise UnauthorizedError()
    return "default"
