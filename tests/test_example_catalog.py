import importlib
import sys

from app.jobs import registry as job_registry
from app.business_packages.example_jobs.catalog import all_example_workflow_mode_specs


def test_example_workflow_mode_catalog_is_machine_readable_contract():
    specs = all_example_workflow_mode_specs()

    assert tuple(item["mode"] for item in specs) == ("single", "chain", "group", "chord", "map", "starmap", "chunks")
    assert {item["mode"]: item["primitive"] for item in specs} == {
        "single": "task",
        "chain": "chain",
        "group": "group",
        "chord": "chord",
        "map": "map",
        "starmap": "starmap",
        "chunks": "chunks",
    }
    assert {item["mode"]: item["expected_node_count"] for item in specs} == {
        "single": 1,
        "chain": 3,
        "group": 3,
        "chord": 3,
        "map": 2,
        "starmap": 2,
        "chunks": 3,
    }


def test_workflow_modes_smoke_import_does_not_register_job_types():
    job_registry.clear_for_tests()
    sys.modules.pop("scripts.verify.workflow_modes_smoke", None)

    importlib.import_module("scripts.verify.workflow_modes_smoke")

    assert job_registry.all_job_types() == []
