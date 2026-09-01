from __future__ import annotations

from app.business_packages.base import BusinessPackage
from app.jobs.types._registrar import RegisterExecutor
from app.jobs.types.tagged_text_translation.executor import TaggedTextTranslationJob


def register_job_package(register: RegisterExecutor) -> None:
    register(TaggedTextTranslationJob())


PACKAGE = BusinessPackage(name="tagged_text_translation", register=register_job_package)
