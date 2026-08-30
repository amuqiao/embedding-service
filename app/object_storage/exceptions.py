from __future__ import annotations


class ObjectStorageError(RuntimeError):
    """Base exception for object storage core failures."""


class ObjectStorageConfigError(ObjectStorageError, ValueError):
    """Raised when object storage configuration is invalid."""


class ObjectStorageValidationError(ObjectStorageError, ValueError):
    """Raised when an object storage contract value is invalid."""


class ObjectStorageNotFoundError(ObjectStorageError, FileNotFoundError):
    """Raised when a requested object does not exist."""


class ObjectStorageBackendError(ObjectStorageError):
    """Raised when a provider backend cannot complete the requested operation."""
