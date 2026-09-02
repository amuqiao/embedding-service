from __future__ import annotations

from app.business_packages.base import BusinessPackage
from app.business_packages.registrar import RegisterExecutor
from app.business_packages.example_lifecycle_probe.errors import register_example_lifecycle_probe_errors
from app.business_packages.example_lifecycle_probe.executor import ExampleLifecycleProbeJob


def register_job_package(register: RegisterExecutor) -> None:
    register_example_lifecycle_probe_errors()
    register(ExampleLifecycleProbeJob())


PACKAGE = BusinessPackage(name="example_lifecycle_probe", register=register_job_package)
