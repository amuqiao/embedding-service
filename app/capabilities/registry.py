from __future__ import annotations

from app.capabilities.definitions import CapabilityDefinition

_registry: dict[str, CapabilityDefinition] = {}
_FROZEN = False


def register(definition: CapabilityDefinition) -> CapabilityDefinition:
    global _registry

    existing = _registry.get(definition.capability_ref)
    if _FROZEN:
        if existing == definition:
            return existing
        raise RuntimeError(f"capability registry is frozen; cannot register: {definition.capability_ref}")
    if existing is not None:
        if existing == definition:
            return existing
        raise ValueError(f"duplicate capability_ref: {definition.capability_ref}")
    _registry[definition.capability_ref] = definition
    return definition


def get(capability_ref: str) -> CapabilityDefinition:
    definition = _registry.get(capability_ref)
    if definition is None:
        raise KeyError(f"unknown capability_ref: {capability_ref!r}")
    return definition


def all_capability_refs() -> set[str]:
    return set(_registry)


def all_capability_definitions() -> dict[str, CapabilityDefinition]:
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

