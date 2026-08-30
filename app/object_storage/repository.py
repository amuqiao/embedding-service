from __future__ import annotations

from abc import ABC, abstractmethod

from .models import ObjectMeta, ObjectRef, PutObjectResult


class ObjectStorageRepository(ABC):
    @abstractmethod
    def get_bytes(self, ref: ObjectRef) -> bytes: ...

    @abstractmethod
    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        content_disposition: str | None = None,
    ) -> PutObjectResult: ...

    @abstractmethod
    def head(self, ref: ObjectRef) -> ObjectMeta: ...

    @abstractmethod
    def delete(self, ref: ObjectRef) -> None: ...
