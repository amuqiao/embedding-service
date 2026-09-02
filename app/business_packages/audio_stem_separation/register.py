from __future__ import annotations

from app.business_packages.base import BusinessPackage
from app.business_packages.registrar import RegisterExecutor
from app.business_packages.audio_stem_separation.errors import register_audio_stem_separation_errors
from app.business_packages.audio_stem_separation.executor import AudioStemSeparationJob


def register_job_package(register: RegisterExecutor) -> None:
    register_audio_stem_separation_errors()
    register(AudioStemSeparationJob())


PACKAGE = BusinessPackage(
    name="audio_stem_separation",
    register=register_job_package,
    requires_object_storage=True,
)
