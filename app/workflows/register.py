def register_all_workflows() -> None:
    from app.jobs import registry as job_registry
    from app.workflows.job_test_add import JobTestAddWorkflow

    if "job_test_add" not in job_registry.all_job_types():
        job_registry.register(JobTestAddWorkflow())
