from __future__ import annotations

from app.business_packages.base import BusinessPackage
from app.jobs.types._registrar import RegisterExecutor
from app.jobs.types.audio_stem_separation_triton.executor import AudioStemSeparationTritonJob


def register_job_package(register: RegisterExecutor) -> None:
    register(AudioStemSeparationTritonJob())


PACKAGE = BusinessPackage(
    name="audio_stem_separation_triton",
    register=register_job_package,
    requires_object_storage=True,
)
