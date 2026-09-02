from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

RegistryRefKind = Literal["tool_ref"]

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_VERSION_PATTERN = re.compile(r"^[1-9][0-9]*$")


@dataclass(frozen=True)
class VersionedRegistryRef:
    kind: RegistryRefKind
    key: str
    version: str

    @property
    def value(self) -> str:
        return f"{self.key}:{self.version}"


def parse_versioned_ref(value: str, *, kind: RegistryRefKind) -> VersionedRegistryRef:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{kind} must be a non-empty string")
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"{kind} must use <key>:<version>: {value!r}")
    key, version = parts
    if not _KEY_PATTERN.fullmatch(key):
        raise ValueError(f"{kind} has invalid key: {value!r}")
    if not _VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"{kind} has invalid version: {value!r}")
    return VersionedRegistryRef(kind=kind, key=key, version=version)


def require_tool_ref(value: str) -> str:
    return parse_versioned_ref(value, kind="tool_ref").value
