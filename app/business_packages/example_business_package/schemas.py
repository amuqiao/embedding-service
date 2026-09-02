from __future__ import annotations

from pydantic import BaseModel


class ExampleBusinessPackagePingResponse(BaseModel):
    message: str


SCHEMAS = (ExampleBusinessPackagePingResponse,)
