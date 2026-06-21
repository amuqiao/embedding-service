from __future__ import annotations


def register_all_job_types() -> None:
    from app.jobs.registry import register
    from app.jobs.types.arithmetic import ArithmeticJob
    from app.jobs.types.job_test_add import JobTestAddJob
    from app.jobs.types.job_test_echo import JobTestEchoJob

    for executor_cls in (ArithmeticJob, JobTestAddJob, JobTestEchoJob):
        register(executor_cls())
