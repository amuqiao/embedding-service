from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import UnauthorizedError
from app.infrastructure.config import settings

bearer_scheme = HTTPBearer(auto_error=False)


async def require_service_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError()
    if not credentials.credentials or credentials.credentials != settings.SERVICE_API_KEY:
        raise UnauthorizedError()
    return "default"
