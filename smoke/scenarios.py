from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SmokeScenario:
    name: str
    type: str
    acceptance_class: str
    dependencies: list[str]
    entrypoints: list[str]
    destructive: bool = False
    supports_resume: bool = False
    standard_option_groups: list[str] = field(default_factory=list)
    conditional_dependencies: list[str] = field(default_factory=list)
    contract_roles: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value not in ([], None)}


SCENARIOS = [
    SmokeScenario(
        name="example-lifecycle-probe",
        type="workflow",
        acceptance_class="platform_acceptance",
        dependencies=["api", "dispatcher", "taskiq_worker", "db", "redis"],
        entrypoints=["example-lifecycle-probe"],
        conditional_dependencies=["callbacker"],
        contract_roles=["reconciler"],
        standard_option_groups=["job", "callback"],
    ),
    SmokeScenario(
        name="example-reconciler-probe",
        type="workflow",
        acceptance_class="platform_acceptance",
        dependencies=["api", "dispatcher", "taskiq_worker", "reconciler", "callbacker", "db", "redis"],
        entrypoints=["example-reconciler-probe"],
        destructive=True,
        standard_option_groups=["job", "callback", "fault-injection"],
    ),
    SmokeScenario(
        name="llm-job-billing",
        type="workflow",
        acceptance_class="business_e2e",
        dependencies=["api", "worker", "db", "redis", "llm_provider"],
        entrypoints=["llm-job-billing"],
        standard_option_groups=["job"],
    ),
    SmokeScenario(
        name="llm-job-double-billing",
        type="workflow",
        acceptance_class="business_e2e",
        dependencies=["api", "worker", "db", "redis", "llm_provider"],
        entrypoints=["llm-job-double-billing"],
        standard_option_groups=["job"],
    ),
    SmokeScenario(
        name="poster-title-image",
        type="workflow",
        acceptance_class="business_e2e",
        dependencies=["api", "worker", "db", "redis", "oss", "image_provider"],
        entrypoints=["poster-title-image"],
        standard_option_groups=["job", "artifact"],
    ),
    SmokeScenario(
        name="tagged-text-translation",
        type="workflow",
        acceptance_class="business_e2e",
        dependencies=["api", "worker", "db", "redis", "llm_provider"],
        entrypoints=["tagged-text-translation"],
        standard_option_groups=["job"],
    ),
    SmokeScenario(
        name="audio-stem-separation",
        type="workflow",
        acceptance_class="business_e2e",
        dependencies=["api", "worker", "db", "redis", "oss", "ffmpeg", "model_runtime"],
        entrypoints=["audio-stem-separation run"],
        supports_resume=True,
        standard_option_groups=["job", "artifact"],
    ),
    SmokeScenario(
        name="adapter-image-probe",
        type="provider_probe",
        acceptance_class="exploratory",
        dependencies=["image_provider"],
        entrypoints=["adapter-image-probe"],
        standard_option_groups=["provider"],
    ),
    SmokeScenario(
        name="oss-upload-image",
        type="provider_probe",
        acceptance_class="plumbing",
        dependencies=["oss"],
        entrypoints=["oss-upload-image"],
        standard_option_groups=["upload"],
    ),
]


def scenario_payloads() -> list[dict[str, Any]]:
    return [scenario.to_payload() for scenario in SCENARIOS]
