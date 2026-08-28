from __future__ import annotations

from app.jobs.types._registrar import JobTypePackage, RegisterExecutor
from app.jobs.types.example_lifecycle_probe.errors import register_example_lifecycle_probe_errors
from app.jobs.types.example_lifecycle_probe.executor import ExampleLifecycleProbeJob


def register_job_package(register: RegisterExecutor) -> None:
    register_example_lifecycle_probe_errors()
    register(ExampleLifecycleProbeJob())


PACKAGE = JobTypePackage(name="example_lifecycle_probe", register=register_job_package)
