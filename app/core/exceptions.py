from typing import Any

from app.core.error_registry import get_error_spec


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        spec = get_error_spec(code)
        self.code = code
        self.message = message
        self.status_code = spec.http_status
        self.details = details or {}

    def __reduce__(self):
        return (
            AppError,
            (self.code, self.message),
            {"details": self.details},
        )


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Missing or invalid service token"):
        super().__init__("UNAUTHORIZED", message)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Service token is not allowed"):
        super().__init__("FORBIDDEN", message)


class ValidationAppError(AppError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(code, message, details=details)


class NotFoundAppError(AppError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message)


class InternalAppError(AppError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(code, message, details=details)
