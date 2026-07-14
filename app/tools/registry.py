from __future__ import annotations

from app.tools.definitions import ToolDefinition

_registry: dict[str, ToolDefinition] = {}
_FROZEN = False


def register(definition: ToolDefinition) -> ToolDefinition:
    global _registry

    existing = _registry.get(definition.tool_ref)
    if _FROZEN:
        if existing == definition:
            return existing
        raise RuntimeError(f"tool registry is frozen; cannot register: {definition.tool_ref}")
    if existing is not None:
        if existing == definition:
            return existing
        raise ValueError(f"duplicate tool_ref: {definition.tool_ref}")
    _registry[definition.tool_ref] = definition
    return definition


def get(tool_ref: str) -> ToolDefinition:
    definition = _registry.get(tool_ref)
    if definition is None:
        raise KeyError(f"unknown tool_ref: {tool_ref!r}")
    return definition


def all_tool_refs() -> set[str]:
    return set(_registry)


def all_tool_definitions() -> dict[str, ToolDefinition]:
    return dict(_registry)


def freeze() -> None:
    global _FROZEN

    _FROZEN = True


def is_frozen() -> bool:
    return _FROZEN


def clear_for_tests() -> None:
    global _FROZEN

    _registry.clear()
    _FROZEN = False

