from __future__ import annotations

from app.ai.policy import job_models as _impl

JOB_MODEL_CONFIG_ROOT = _impl.JOB_MODEL_CONFIG_ROOT
JOB_MODEL_CONFIG_FILENAME = _impl.JOB_MODEL_CONFIG_FILENAME
POSTER_TITLE_IMAGE_JOB_TYPE = _impl.POSTER_TITLE_IMAGE_JOB_TYPE
DEFAULT_PUBLIC_SLOT = _impl.DEFAULT_PUBLIC_SLOT
PublicModelSlot = _impl.PublicModelSlot
PosterTitleImageModelSelection = _impl.PosterTitleImageModelSelection
ModelSlotPolicy = _impl.ModelSlotPolicy
JobModelPolicy = _impl.JobModelPolicy


def _sync_config_root() -> None:
    _impl.JOB_MODEL_CONFIG_ROOT = JOB_MODEL_CONFIG_ROOT


def has_model_selection_config(job_type: str) -> bool:
    _sync_config_root()
    return _impl.has_model_selection_config(job_type)


def get_job_model_policy(job_type: str) -> JobModelPolicy:
    _sync_config_root()
    return _impl.get_job_model_policy(job_type)


def get_public_model_slot(job_type: str) -> PublicModelSlot:
    _sync_config_root()
    return _impl.get_public_model_slot(job_type)


def get_poster_title_image_model_selection() -> PosterTitleImageModelSelection:
    _sync_config_root()
    return _impl.get_poster_title_image_model_selection()


def poster_title_image_generation_default_model_id() -> str:
    _sync_config_root()
    return _impl.poster_title_image_generation_default_model_id()


def poster_title_image_generation_allowed_model_ids() -> tuple[str, ...]:
    _sync_config_root()
    return _impl.poster_title_image_generation_allowed_model_ids()


def poster_title_image_style_probe_model_id() -> str:
    _sync_config_root()
    return _impl.poster_title_image_style_probe_model_id()
