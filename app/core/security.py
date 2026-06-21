import logging
import re
import secrets

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import UnauthorizedError
from app.core.config import settings

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)
CALLER_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$")


def _caller_id_from_header(caller_id: str | None) -> str:
    if settings.DISABLE_CALLER_ID_HEADER:
        return "default"
    if caller_id:
        normalized = caller_id.strip()
        if not CALLER_ID_RE.fullmatch(normalized):
            logger.warning("auth_failed reason=invalid_caller_id")
            raise UnauthorizedError()
        return normalized
    return "default"


async def require_service_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    caller_id: str | None = Header(default=None, alias="X-AI-Service-Caller-ID"),
) -> str:
    if settings.DISABLE_HTTP_AUTH_HEADER:
        return _caller_id_from_header(caller_id)
    if credentials is None or credentials.scheme.lower() != "bearer":
        logger.warning("auth_failed reason=missing_bearer")
        raise UnauthorizedError()
    if not settings.SERVICE_API_KEY:
        logger.warning("auth_failed reason=service_key_not_configured")
        raise UnauthorizedError()
    if not secrets.compare_digest(credentials.credentials, settings.SERVICE_API_KEY):
        logger.warning("auth_failed reason=invalid_api_key")
        raise UnauthorizedError()
    return _caller_id_from_header(caller_id)
