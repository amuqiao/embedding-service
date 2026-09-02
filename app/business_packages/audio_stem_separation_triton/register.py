from __future__ import annotations

from app.business_packages.base import BusinessPackage
from app.business_packages.registrar import RegisterExecutor
from app.business_packages.audio_stem_separation_triton.executor import AudioStemSeparationTritonJob


def register_job_package(register: RegisterExecutor) -> None:
    register(AudioStemSeparationTritonJob())


PACKAGE = BusinessPackage(
    name="audio_stem_separation_triton",
    register=register_job_package,
    requires_object_storage=True,
)
