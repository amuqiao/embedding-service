from __future__ import annotations


def register_all_job_types() -> None:
    from app.jobs.registry import register
    from app.jobs.types.arithmetic import ArithmeticJob
    from app.jobs.types.job_test_add import JobTestAddJob
    from app.jobs.types.job_test_echo import JobTestEchoJob
    from app.jobs.types.job_real_llm_double_echo import JobRealLlmDoubleEchoJob
    from app.jobs.types.job_real_llm_echo import JobRealLlmEchoJob

    for executor_cls in (
        ArithmeticJob,
        JobTestAddJob,
        JobTestEchoJob,
        JobRealLlmEchoJob,
        JobRealLlmDoubleEchoJob,
    ):
        register(executor_cls())
