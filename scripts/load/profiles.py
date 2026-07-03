from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.load.support import LoadError, ROOT_DIR, ensure_parent


@dataclass(frozen=True)
class LoadProfile:
    key: str
    title: str
    job_type: str
    case: str = "job-flow"
    job_params: dict[str, Any] | None = None
    users: int | None = None
    spawn_rate: float | None = None
    run_time: str | None = None
    poll_interval_seconds: float | None = None
    flow_timeout_seconds: float | None = None
    wait_min_seconds: float | None = None
    wait_max_seconds: float | None = None

    def manifest(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "job_type": self.job_type,
            "case": self.case,
            "job_params_present": self.job_params is not None,
            "defaults": {
                "users": self.users,
                "spawn_rate": self.spawn_rate,
                "time": self.run_time,
                "poll_interval_seconds": self.poll_interval_seconds,
                "flow_timeout_seconds": self.flow_timeout_seconds,
                "wait_min_seconds": self.wait_min_seconds,
                "wait_max_seconds": self.wait_max_seconds,
            },
        }


BUILTIN_PROFILES: dict[str, LoadProfile] = {
    "echo": LoadProfile(
        key="echo",
        title="内置 echo Job",
        job_type="job_test_echo",
        case="job-flow",
        users=4,
        spawn_rate=1.0,
        run_time="60s",
        flow_timeout_seconds=45.0,
    ),
    "workflow": LoadProfile(
        key="workflow",
        title="内置 workflow Job",
        job_type="job_test_workflow",
        case="workflow-flow",
        users=4,
        spawn_rate=1.0,
        run_time="60s",
        flow_timeout_seconds=90.0,
    ),
}


def profile_rows() -> list[dict[str, object]]:
    return [
        {
            "key": profile.key,
            "job_type": profile.job_type,
            "case": profile.case,
            "title": profile.title,
        }
        for profile in BUILTIN_PROFILES.values()
    ]


def resolve_profile(ref: str | None) -> LoadProfile | None:
    if ref is None:
        return None
    if ref in BUILTIN_PROFILES:
        return BUILTIN_PROFILES[ref]

    path = Path(ref).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    if not path.is_file():
        allowed = ", ".join(sorted(BUILTIN_PROFILES))
        raise LoadError(f"profile not found: {ref}; expected built-in key ({allowed}) or JSON file", exit_code=2)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LoadError(f"profile must be valid JSON object: {exc}", exit_code=2) from exc
    if not isinstance(raw, dict):
        raise LoadError("profile must be a JSON object", exit_code=2)
    return _profile_from_dict(raw, source=str(path))


def _profile_from_dict(raw: dict[str, Any], *, source: str) -> LoadProfile:
    _reject_unknown_keys(
        raw,
        allowed={"profile_version", "key", "title", "job_type", "case", "job_params", "defaults"},
        label="profile",
        source=source,
    )
    key = _required_str(raw, "key", source=source)
    job_type = _required_str(raw, "job_type", source=source)
    title = str(raw.get("title") or key)
    case_value = raw.get("case")
    if not isinstance(case_value, str) or not case_value.strip():
        raise LoadError(f"profile case is required: {source}", exit_code=2)
    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise LoadError(f"profile defaults must be an object: {source}", exit_code=2)
    _reject_unknown_keys(
        defaults,
        allowed={
            "users",
            "spawn_rate",
            "time",
            "poll_interval_seconds",
            "flow_timeout_seconds",
            "wait_min_seconds",
            "wait_max_seconds",
        },
        label="profile defaults",
        source=source,
    )
    job_params = raw.get("job_params")
    if job_params is not None and not isinstance(job_params, dict):
        raise LoadError(f"profile job_params must be an object: {source}", exit_code=2)
    return LoadProfile(
        key=key,
        title=title,
        job_type=job_type,
        case=case_value.strip(),
        job_params=job_params,
        users=_optional_int(defaults.get("users"), "defaults.users", source=source),
        spawn_rate=_optional_float(defaults.get("spawn_rate"), "defaults.spawn_rate", source=source),
        run_time=_optional_str(defaults.get("time"), "defaults.time", source=source),
        poll_interval_seconds=_optional_float(
            defaults.get("poll_interval_seconds"),
            "defaults.poll_interval_seconds",
            source=source,
        ),
        flow_timeout_seconds=_optional_float(
            defaults.get("flow_timeout_seconds"),
            "defaults.flow_timeout_seconds",
            source=source,
        ),
        wait_min_seconds=_optional_float(defaults.get("wait_min_seconds"), "defaults.wait_min_seconds", source=source),
        wait_max_seconds=_optional_float(defaults.get("wait_max_seconds"), "defaults.wait_max_seconds", source=source),
    )


def profile_template(*, key: str, job_type: str) -> dict[str, Any]:
    return {
        "profile_version": 1,
        "key": key,
        "title": key,
        "job_type": job_type,
        "case": "job-flow",
        "job_params": {},
        "defaults": {
            "users": 4,
            "spawn_rate": 1.0,
            "time": "60s",
            "poll_interval_seconds": 0.5,
            "flow_timeout_seconds": 45.0,
            "wait_min_seconds": 0.1,
            "wait_max_seconds": 1.0,
        },
    }


def write_profile_template(path: Path, payload: dict[str, Any], *, force: bool) -> None:
    if path.exists() and not force:
        raise LoadError(f"profile file already exists: {path}; pass --force to overwrite", exit_code=2)
    ensure_parent(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _reject_unknown_keys(raw: dict[str, Any], *, allowed: set[str], label: str, source: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise LoadError(f"{label} has unknown keys: {joined}: {source}", exit_code=2)


def _required_str(raw: dict[str, Any], key: str, *, source: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LoadError(f"profile {key} is required: {source}", exit_code=2)
    return value.strip()


def _optional_str(value: Any, key: str, *, source: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LoadError(f"profile {key} must be a non-empty string: {source}", exit_code=2)
    return value.strip()


def _optional_int(value: Any, key: str, *, source: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value < 1:
        raise LoadError(f"profile {key} must be a positive integer: {source}", exit_code=2)
    return value


def _optional_float(value: Any, key: str, *, source: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or value < 0:
        raise LoadError(f"profile {key} must be a non-negative number: {source}", exit_code=2)
    return float(value)
