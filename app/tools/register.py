from __future__ import annotations


def register_all_tools() -> None:
    """Register all tool definitions."""
    from app.tools.definitions import ToolDefinition
    from app.tools.registry import register

    register(
        ToolDefinition(
            tool_ref="object_storage_read:1",
            kind="object_storage",
            entrypoint_path="app.tools.object_storage:read_object_bytes",
            request_schema="CanonicalObjectRefSnapshot",
            result_schema=None,
            required_settings=("storage.backend", "job.oss_input_max_bytes"),
            error_codes=frozenset(
                {
                    "INPUT_TOO_LARGE",
                    "OSS_BUCKET_NOT_CONFIGURED",
                    "OSS_FETCH_FAILED",
                    "OSS_OBJECT_NOT_FOUND",
                    "OSS_REGION_NOT_CONFIGURED",
                }
            ),
        )
    )
