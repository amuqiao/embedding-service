from __future__ import annotations

from app.business_packages.base import BusinessPackage
from app.business_packages.examples.schemas import SCHEMAS
from app.business_packages.registrar import RegisterExecutor, register_executor_classes


def register_job_package(register: RegisterExecutor) -> None:
    from app.business_packages.examples.executor import (
        ExampleCollectJob,
        ExamplePairJob,
        ExampleSleepJob,
        ExampleWorkflowJob,
        register_example_workflows,
    )

    register_executor_classes(
        register,
        (
            ExamplePairJob,
            ExampleSleepJob,
            ExampleCollectJob,
            ExampleWorkflowJob,
        ),
    )
    register_example_workflows()


PACKAGE = BusinessPackage(name="examples", register=register_job_package, schemas=SCHEMAS)
