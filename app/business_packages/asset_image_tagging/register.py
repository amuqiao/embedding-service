from __future__ import annotations

from app.business_packages.asset_image_tagging.errors import register_asset_image_tagging_errors
from app.business_packages.asset_image_tagging.schemas import SCHEMAS
from app.business_packages.base import BusinessPackage
from app.business_packages.registrar import RegisterExecutor


def register_job_package(register: RegisterExecutor) -> None:
    from app.business_packages.asset_image_tagging.executor import AssetImageTaggingJob

    register_asset_image_tagging_errors()
    register(AssetImageTaggingJob())


PACKAGE = BusinessPackage(name="asset_image_tagging", register=register_job_package, schemas=SCHEMAS)
