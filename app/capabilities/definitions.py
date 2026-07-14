from __future__ import annotations

from dataclasses import dataclass

from app.core.registries.refs import require_capability_ref, require_tool_ref


@dataclass(frozen=True)
class CapabilityDefinition:
    capability_ref: str
    plan_schema: str
    result_schema: str
    service_entrypoint: str
    allowed_tool_refs: frozenset[str]
    error_codes: frozenset[str]
    log_events: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_ref", require_capability_ref(self.capability_ref))
        object.__setattr__(
            self,
            "allowed_tool_refs",
            frozenset(require_tool_ref(tool_ref) for tool_ref in self.allowed_tool_refs),
        )
        if not self.plan_schema:
            raise ValueError(f"capability {self.capability_ref} requires plan_schema")
        if not self.result_schema:
            raise ValueError(f"capability {self.capability_ref} requires result_schema")
        if not self.service_entrypoint:
            raise ValueError(f"capability {self.capability_ref} requires service_entrypoint")

