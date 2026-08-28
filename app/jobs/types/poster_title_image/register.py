from __future__ import annotations

from app.jobs.types._registrar import JobTypePackage, RegisterExecutor, register_executor_classes
from app.jobs.types.poster_title_image.errors import register_poster_title_image_errors
from app.jobs.types.poster_title_image.executor import (
    PosterTitleImageGenerateItemJob,
    PosterTitleImageJoinJob,
    PosterTitleImageJob,
    PosterTitleImageStyleProbeJob,
    register_poster_title_image_workflow,
)


def register_job_package(register: RegisterExecutor) -> None:
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


PACKAGE = JobTypePackage(name="poster_title_image", register=register_job_package)
