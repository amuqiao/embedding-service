from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.operations import operation_route_kwargs_for_spec
from app.core.security import require_service_auth
from app.business_packages.example_business_package.operations import (
    EXAMPLE_BUSINESS_PACKAGE_PING,
)
from app.business_packages.example_business_package.schemas import (
    ExampleBusinessPackagePingResponse,
)


router = APIRouter(tags=["example-business-package"], dependencies=[Depends(require_service_auth)])


@router.get(
    EXAMPLE_BUSINESS_PACKAGE_PING.path,
    **operation_route_kwargs_for_spec(EXAMPLE_BUSINESS_PACKAGE_PING),
)
async def ping_example_business_package() -> ExampleBusinessPackagePingResponse:
    return ExampleBusinessPackagePingResponse(message="ok")
