from __future__ import annotations

_ENABLED_JOB_TYPE_DEPENDENCIES: dict[str, frozenset[str]] = {
    "example_workflow": frozenset({"example_sleep", "example_pair", "example_collect"}),
    "poster_title_image": frozenset(
        {
            "poster_title_image_style_probe",
            "poster_title_image_generate_item",
            "poster_title_image_join",
        }
    ),
}


def _expanded_enabled_job_types(
    configured_job_types: tuple[str, ...],
    *,
    release_env: bool,
) -> tuple[frozenset[str], frozenset[str]] | None:
    if not configured_job_types:
        return None

    from app.jobs import registry as job_registry

    specs = job_registry.all_job_type_specs()
    unknown = sorted(set(configured_job_types) - set(specs))
    if unknown:
        raise ValueError(f"ENABLED_JOB_TYPES references unknown job_type: {unknown}")

    external = set(configured_job_types)
    enabled = set(external)
    for job_type in configured_job_types:
        spec = specs[job_type]
        if spec.visibility == "internal" or spec.role == "leaf":
            raise ValueError("ENABLED_JOB_TYPES must list external root-capable job_types")
        if release_env and spec.visibility != "public":
            raise ValueError("release APP_ENV ENABLED_JOB_TYPES must list only public job_types")
        enabled.update(_ENABLED_JOB_TYPE_DEPENDENCIES.get(job_type, frozenset()))

    missing_dependencies = sorted(enabled - set(specs))
    if missing_dependencies:
        raise ValueError(f"ENABLED_JOB_TYPES references unknown dependent job_type: {missing_dependencies}")
    return frozenset(enabled), frozenset(external)


def register_all_job_types() -> None:
    from app.core.config import settings
    from app.capabilities.register import register_all_capabilities
    from app.jobs.registry import configure_enabled_job_types, register
    from app.jobs.types.audio_stem_separation import AudioStemSeparationJob
    from app.jobs.types.audio_stem_separation.errors import register_audio_stem_separation_errors
    from app.jobs.types.audio_stem_separation_triton import AudioStemSeparationTritonJob
    from app.jobs.types.poster_title_image.errors import register_poster_title_image_errors
    from app.jobs.types.arithmetic import ArithmeticJob
    from app.jobs.types.examples import (
        ExampleCollectJob,
        ExamplePairJob,
        ExampleSleepJob,
        ExampleWorkflowJob,
        register_example_workflows,
    )
    from app.jobs.types.job_real_llm_double_echo import JobRealLlmDoubleEchoJob
    from app.jobs.types.job_real_llm_echo import JobRealLlmEchoJob
    from app.jobs.types.poster_title_image import (
        PosterTitleImageGenerateItemJob,
        PosterTitleImageJoinJob,
        PosterTitleImageJob,
        PosterTitleImageStyleProbeJob,
        register_poster_title_image_workflow,
    )
    from app.jobs.types.tagged_text_translation import TaggedTextTranslationJob
    from app.tools.register import register_all_tools

    register_all_tools()
    register_all_capabilities()
    register_audio_stem_separation_errors()
    register_poster_title_image_errors()
    for executor_cls in (
        ArithmeticJob,
        ExamplePairJob,
        ExampleSleepJob,
        ExampleCollectJob,
        ExampleWorkflowJob,
        JobRealLlmEchoJob,
        JobRealLlmDoubleEchoJob,
        PosterTitleImageJob,
        PosterTitleImageStyleProbeJob,
        PosterTitleImageGenerateItemJob,
        PosterTitleImageJoinJob,
        TaggedTextTranslationJob,
        AudioStemSeparationJob,
        AudioStemSeparationTritonJob,
    ):
        register(executor_cls())
    register_example_workflows()
    register_poster_title_image_workflow()
    expanded_enabled_job_types = _expanded_enabled_job_types(
        settings.job.enabled_job_types,
        release_env=settings.runtime.is_release_env,
    )
    if expanded_enabled_job_types is None:
        configure_enabled_job_types(None)
    else:
        runtime_job_types, external_job_types = expanded_enabled_job_types
        configure_enabled_job_types(runtime_job_types, external_job_types=external_job_types)
