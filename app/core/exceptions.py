from typing import Any


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def __reduce__(self):
        return (
            AppError,
            (self.code, self.message),
            {"status_code": self.status_code, "details": self.details},
        )


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Missing or invalid service token"):
        super().__init__("UNAUTHORIZED", message, status_code=401)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Service token is not allowed"):
        super().__init__("FORBIDDEN", message, status_code=403)


class ValidationAppError(AppError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(code, message, status_code=400, details=details)


class NotFoundAppError(AppError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, status_code=404)


class InternalAppError(AppError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(code, message, status_code=500, details=details)
