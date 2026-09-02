from __future__ import annotations

from app.business_packages.base import BusinessPackage
from app.business_packages.registrar import RegisterExecutor, register_executor_classes
from app.business_packages.poster_title_image.errors import register_poster_title_image_errors
from app.business_packages.poster_title_image.schemas import SCHEMAS


def register_job_package(register: RegisterExecutor) -> None:
    from app.business_packages.poster_title_image.executor import (
        PosterTitleImageGenerateItemJob,
        PosterTitleImageJoinJob,
        PosterTitleImageJob,
        PosterTitleImageStyleProbeJob,
        register_poster_title_image_workflow,
    )

    register_poster_title_image_errors()
    register_executor_classes(
        register,
        (
            PosterTitleImageJob,
            PosterTitleImageStyleProbeJob,
            PosterTitleImageGenerateItemJob,
            PosterTitleImageJoinJob,
        ),
    )
    register_poster_title_image_workflow()


PACKAGE = BusinessPackage(
    name="poster_title_image",
    register=register_job_package,
    requires_object_storage=True,
    schemas=SCHEMAS,
)
