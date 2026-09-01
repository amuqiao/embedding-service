from __future__ import annotations

from app.jobs.types._registrar import JobTypePackage, RegisterExecutor
from app.jobs.types.audio_stem_separation.errors import register_audio_stem_separation_errors
from app.jobs.types.audio_stem_separation.executor import AudioStemSeparationJob


def register_job_package(register: RegisterExecutor) -> None:
    register_audio_stem_separation_errors()
    register(AudioStemSeparationJob())


PACKAGE = JobTypePackage(
    name="audio_stem_separation",
    register=register_job_package,
    requires_object_storage=True,
)
