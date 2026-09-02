from __future__ import annotations

from app.business_packages.base import BusinessPackage
from app.business_packages.registrar import RegisterExecutor
from app.business_packages.tagged_text_translation.schemas import SCHEMAS


def register_job_package(register: RegisterExecutor) -> None:
    from app.business_packages.tagged_text_translation.executor import TaggedTextTranslationJob

    register(TaggedTextTranslationJob())


PACKAGE = BusinessPackage(name="tagged_text_translation", register=register_job_package, schemas=SCHEMAS)
