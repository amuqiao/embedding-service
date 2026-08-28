from app.core.error_registry import ErrorSpec, register_error_specs

EXAMPLE_LIFECYCLE_PROBE_FORCED_FAILURE = "EXAMPLE_LIFECYCLE_PROBE_FORCED_FAILURE"

EXAMPLE_LIFECYCLE_PROBE_ERROR_SPECS: dict[str, ErrorSpec] = {
    EXAMPLE_LIFECYCLE_PROBE_FORCED_FAILURE: ErrorSpec(
        "112001",
        EXAMPLE_LIFECYCLE_PROBE_FORCED_FAILURE,
        "example lifecycle probe forced failure",
        500,
        scope="job",
        owner="example_lifecycle_probe",
    ),
}


def register_example_lifecycle_probe_errors() -> None:
    register_error_specs(EXAMPLE_LIFECYCLE_PROBE_ERROR_SPECS)
