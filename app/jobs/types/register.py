from __future__ import annotations


def register_all_job_types() -> None:
    from app.jobs.registry import register
    from app.jobs.types.arithmetic import ArithmeticJob
    from app.jobs.types.job_test_add import JobTestAddJob
    from app.jobs.types.job_test_echo import JobTestEchoJob
    from app.jobs.types.job_test_workflow import (
        JobTestCollectJob,
        JobTestWorkflowJob,
        register_test_workflows,
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

    for executor_cls in (
        ArithmeticJob,
        JobTestAddJob,
        JobTestEchoJob,
        JobTestCollectJob,
        JobTestWorkflowJob,
        JobRealLlmEchoJob,
        JobRealLlmDoubleEchoJob,
        PosterTitleImageJob,
        PosterTitleImageStyleProbeJob,
        PosterTitleImageGenerateItemJob,
        PosterTitleImageJoinJob,
    ):
        register(executor_cls())
    register_test_workflows()
    register_poster_title_image_workflow()
