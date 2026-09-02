from __future__ import annotations

from app.business_packages.base import BusinessPackage
from app.business_packages.registrar import RegisterExecutor
from app.business_packages.audio_stem_separation.errors import register_audio_stem_separation_errors
from app.business_packages.audio_stem_separation.schemas import SCHEMAS as AUDIO_STEM_SCHEMAS
from app.business_packages.audio_stem_separation.triton_schemas import SCHEMAS as TRITON_SCHEMAS


def register_job_package(register: RegisterExecutor) -> None:
    from app.business_packages.audio_stem_separation.executor import AudioStemSeparationJob
    from app.business_packages.audio_stem_separation.triton_executor import AudioStemSeparationTritonJob

    register_audio_stem_separation_errors()
    register(AudioStemSeparationJob())
    register(AudioStemSeparationTritonJob())


PACKAGE = BusinessPackage(
    name="audio_stem_separation",
    register=register_job_package,
    requires_object_storage=True,
    schemas=AUDIO_STEM_SCHEMAS + TRITON_SCHEMAS,
)
