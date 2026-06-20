def register_all_workflows() -> None:
    from app.core import workflow_registry
    from app.workflows.job_test_add import JobTestAddWorkflow

    workflow_registry.register(JobTestAddWorkflow())
