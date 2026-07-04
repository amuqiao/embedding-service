from __future__ import annotations


def register_all_job_types() -> None:
    from app.jobs.registry import register
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
    ):
        register(executor_cls())
    register_example_workflows()
    register_poster_title_image_workflow()
