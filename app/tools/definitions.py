from __future__ import annotations

from dataclasses import dataclass

from app.core.registries.refs import require_tool_ref


@dataclass(frozen=True)
class ToolDefinition:
    tool_ref: str
    kind: str
    entrypoint_path: str
    request_schema: str | None = None
    result_schema: str | None = None
    required_settings: tuple[str, ...] = ()
    startup_validators: tuple[str, ...] = ()
    error_codes: frozenset[str] = frozenset()
    log_events: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_ref", require_tool_ref(self.tool_ref))
        if not self.kind:
            raise ValueError(f"tool {self.tool_ref} requires kind")
        if not self.entrypoint_path:
            raise ValueError(f"tool {self.tool_ref} requires entrypoint_path")

