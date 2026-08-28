from __future__ import annotations

from app.jobs.types._registrar import JobTypePackage, RegisterExecutor
from app.jobs.types.audio_stem_separation_triton.executor import AudioStemSeparationTritonJob


def register_job_package(register: RegisterExecutor) -> None:
    register(AudioStemSeparationTritonJob())


PACKAGE = JobTypePackage(name="audio_stem_separation_triton", register=register_job_package)
