import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Query, status
from pydantic import Field, model_validator

from app.core.exceptions import ValidationAppError
from app.core.security import require_service_auth
from app.schemas.common import StrictBaseModel
from app.schemas.jobs import CallbackConfig, CreateJobRequest, CreateJobResponse, JobOptions, JobStatusResponse
from app.workflows.short_drama_tagging.languages import SUPPORTED_BUSINESS_LANGUAGES, validate_business_language

router = APIRouter(tags=["mock-interfaces"], dependencies=[Depends(require_service_auth)])

MOCK_API_VERSION = "v1"
CPP_MOCK_JOB_TYPES = {
    "short_drama.tagging.initial",
    "short_drama.tagging.incremental",
}
RS_MOCK_JOB_TYPES = {
    "short_drama.tag_labels.translation",
}
MOCK_JOB_NAMESPACE = uuid.UUID("7f1e153a-98e2-4681-a883-52e827de7535")
MOCK_CREATED_AT = datetime(2026, 6, 15, 10, 0, 0, tzinfo=UTC)
MOCK_STARTED_AT = datetime(2026, 6, 15, 10, 0, 5, tzinfo=UTC)
MOCK_FINISHED_AT = datetime(2026, 6, 15, 10, 1, 30, tzinfo=UTC)
MOCK_JOBS: dict[uuid.UUID, dict[str, Any]] = {}
MOCK_TAGGING_RESULT = {
    "t_book_id": "300000000300000279",
    "tags": {
        "000001": [
            {
                "label_id": "65f0a1b2c3d4e5f6a7b8c902",
                "name": "女频",
                "weight": 1,
                "reason": "剧情以女主视角展开，核心冲突围绕女主遭遇与成长展开。",
                "definition": "核心受众为女性群体，叙事视角、人物塑造、情感逻辑以女性主角为核心。",
            }
        ],
        "000003": [
            {
                "label_id": "65f0a1b2c3d4e5f6a7b8c9f1",
                "name": "家庭伦理",
                "weight": 0.9,
                "reason": "核心冲突围绕家族关系、婚礼与亲属间误解展开。",
                "definition": "聚焦普通家庭内部的人际关系、责任、冲突、和解或背叛。",
            }
        ],
        "000006": [
            {
                "label_id": "65f0a1b2c3d4e5f6a7b8ca01",
                "name": "虐",
                "weight": 0.85,
                "reason": "女主遭受误解、羞辱和身体危机，形成持续压抑情绪。",
                "definition": "刻意营造悲伤、压抑、委屈或受伤害的情绪体验。",
            },
            {
                "label_id": "65f0a1b2c3d4e5f6a7b8ca02",
                "name": "爽",
                "weight": 0.75,
                "reason": "真相揭开后加害者受到惩罚，女主获得平反。",
                "definition": "通过反击、逆袭、惩罚加害者或获得补偿制造畅快感。",
            },
        ],
    },
}

MockClient = Literal["cpp", "rs"]
MockJobStatus = Literal["queued", "running", "succeeded", "failed"]
MockJobType = Literal[
    "short_drama.tagging.initial",
    "short_drama.tagging.incremental",
    "short_drama.tag_labels.translation",
]
MockJobParams = dict[str, Any] | list[dict[str, Any]]


class MockCreateJobRequest(StrictBaseModel):
    client_request_id: str | None = Field(default=None, max_length=255)
    job_type: str = Field(min_length=1)
    job_params: dict[str, Any]
    callback: CallbackConfig | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    options: JobOptions | None = None

    @model_validator(mode="after")
    def validate_mock_request(self):
        if self.job_type == "short_drama.tag_labels.translation":
            self.job_params = _validate_label_translation_params(self.job_params)
        return self


def mock_tag_schema() -> dict[str, Any]:
    return {
        "version": "v1.1",
        "generated_at": 1700000000,
        "categories": [
            {
                "category_id": "000001",
                "name": "受众",
                "required": True,
                "min_items": 1,
                "max_items": 1,
                "labels": [
                    {
                        "label_id": "65f0a1b2c3d4e5f6a7b8c901",
                        "name": "男频",
                        "definition": "核心受众为男性群体，叙事视角、人物塑造、价值观以男性主角为核心。",
                    },
                    {
                        "label_id": "65f0a1b2c3d4e5f6a7b8c902",
                        "name": "女频",
                        "definition": "核心受众为女性群体，叙事视角、人物塑造、情感逻辑以女性主角为核心。",
                    },
                ],
            },
            {
                "category_id": "000003",
                "name": "题材",
                "required": True,
                "min_items": 1,
                "max_items": 3,
                "labels": [
                    {
                        "label_id": "65f0a1b2c3d4e5f6a7b8c9f1",
                        "name": "家庭伦理",
                        "definition": "聚焦普通家庭内部的人际关系、责任、冲突、和解或背叛。",
                    },
                    {
                        "label_id": "65f0a1b2c3d4e5f6a7b8c9f2",
                        "name": "惊悚灵异",
                        "definition": "通过离奇事件、鬼魂传说、诅咒或异象营造恐惧与刺激。",
                    },
                ],
            },
            {
                "category_id": "000006",
                "name": "情绪",
                "required": True,
                "min_items": 1,
                "max_items": 3,
                "labels": [
                    {
                        "label_id": "65f0a1b2c3d4e5f6a7b8ca01",
                        "name": "虐",
                        "definition": "刻意营造悲伤、压抑、委屈或受伤害的情绪体验。",
                    },
                    {
                        "label_id": "65f0a1b2c3d4e5f6a7b8ca02",
                        "name": "爽",
                        "definition": "通过反击、逆袭、惩罚加害者或获得补偿制造畅快感。",
                    },
                ],
            },
        ],
        "mutual_exclusion_rules": [
            {
                "label_id": "65f0a1b2c3d4e5f6a7b8c9f1",
                "mutex_label_ids": ["65f0a1b2c3d4e5f6a7b8c9f2"],
            },
            {
                "label_id": "65f0a1b2c3d4e5f6a7b8c9f2",
                "mutex_label_ids": ["65f0a1b2c3d4e5f6a7b8c9f1"],
            },
        ],
    }


def _job_types_for_client(mock_client: MockClient) -> set[str]:
    return CPP_MOCK_JOB_TYPES if mock_client == "cpp" else RS_MOCK_JOB_TYPES


def _mock_job_path(mock_client: MockClient, job_id: uuid.UUID) -> str:
    return f"/api/{MOCK_API_VERSION}/mock/{mock_client}/ai-jobs/jobs/{job_id}"


def _business_scene_for_job_type(job_type: str) -> str:
    return "tag_labels_translation" if job_type == "short_drama.tag_labels.translation" else "short_drama_tagging"


def _is_translation_job(job_type: str) -> bool:
    return job_type == "short_drama.tag_labels.translation"


def _validate_mock_job_type(mock_client: MockClient, job_type: str) -> None:
    supported_job_types = _job_types_for_client(mock_client)
    if job_type not in supported_job_types:
        raise ValidationAppError(
            "INVALID_JOB_TYPE",
            f"Unsupported {mock_client.upper()} mock job_type: {job_type}",
            {"supported_job_types": sorted(supported_job_types)},
        )


def _job_id_for_payload(mock_client: MockClient, payload: CreateJobRequest | MockCreateJobRequest) -> uuid.UUID:
    fingerprint = json.dumps(payload.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return uuid.uuid5(MOCK_JOB_NAMESPACE, f"{MOCK_API_VERSION}:{mock_client}:{fingerprint}")


def _hash_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _translated_category(
    category_id: str,
    name: str,
    labels: list[dict[str, Any]],
    *,
    required: bool = True,
    min_items: int = 1,
    max_items: int | None = None,
) -> dict[str, Any]:
    return {
        "category_id": category_id,
        "name": name,
        "required": required,
        "min_items": min_items,
        "max_items": max_items,
        "labels": labels,
    }


def _translated_schema_by_language() -> dict[str, dict[str, Any]]:
    return {
        "en": {
            "language": "en",
            "categories": [
                _translated_category(
                    "000001",
                    "Audience",
                    [
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8c901",
                            "name": "Male-oriented",
                            "definition": "The story is primarily written for male audiences, with the narrative viewpoint and character arcs centered on a male lead.",
                        },
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8c902",
                            "name": "Female-oriented",
                            "definition": "The story is primarily written for female audiences, with the narrative viewpoint, characterization, and emotional logic centered on a female lead.",
                        },
                    ],
                    max_items=1,
                ),
                _translated_category(
                    "000003",
                    "Genre",
                    [
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8c9f1",
                            "name": "Family ethics",
                            "definition": "Focuses on relationships, responsibilities, conflicts, reconciliation, or betrayal within an ordinary family.",
                        },
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8c9f2",
                            "name": "Thriller and supernatural",
                            "definition": "Creates fear and tension through strange events, ghost stories, curses, or supernatural signs.",
                        },
                    ],
                    max_items=3,
                ),
                _translated_category(
                    "000006",
                    "Emotion",
                    [
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8ca01",
                            "name": "Angst",
                            "definition": "Deliberately creates sadness, repression, grievance, or emotional injury.",
                        },
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8ca02",
                            "name": "Satisfying revenge",
                            "definition": "Creates a sense of pleasure through counterattack, comeback, punishment of wrongdoers, or compensation.",
                        },
                    ],
                    max_items=3,
                ),
            ],
        },
        "es": {
            "language": "es",
            "categories": [
                _translated_category(
                    "000001",
                    "Audiencia",
                    [
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8c901",
                            "name": "Orientado a hombres",
                            "definition": "La historia se dirige principalmente a una audiencia masculina, con el punto de vista narrativo y los arcos de personajes centrados en un protagonista masculino.",
                        },
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8c902",
                            "name": "Orientado a mujeres",
                            "definition": "La historia se dirige principalmente a una audiencia femenina, con el punto de vista, la caracterización y la lógica emocional centrados en una protagonista femenina.",
                        },
                    ],
                    max_items=1,
                ),
                _translated_category(
                    "000003",
                    "Genero",
                    [
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8c9f1",
                            "name": "Etica familiar",
                            "definition": "Se centra en relaciones, responsabilidades, conflictos, reconciliacion o traicion dentro de una familia comun.",
                        },
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8c9f2",
                            "name": "Suspenso sobrenatural",
                            "definition": "Crea miedo y tension mediante sucesos extranos, historias de fantasmas, maldiciones o senales sobrenaturales.",
                        },
                    ],
                    max_items=3,
                ),
                _translated_category(
                    "000006",
                    "Emocion",
                    [
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8ca01",
                            "name": "Sufrimiento",
                            "definition": "Crea deliberadamente tristeza, represion, agravio o dano emocional.",
                        },
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8ca02",
                            "name": "Venganza satisfactoria",
                            "definition": "Crea placer mediante contraataque, remontada, castigo a los agresores o compensacion.",
                        },
                    ],
                    max_items=3,
                ),
            ],
        },
        "pt": {
            "language": "pt",
            "categories": [
                _translated_category(
                    "000001",
                    "Publico",
                    [
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8c901",
                            "name": "Voltado ao publico masculino",
                            "definition": "A historia e voltada principalmente ao publico masculino, com o ponto de vista narrativo e os arcos dos personagens centrados em um protagonista homem.",
                        },
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8c902",
                            "name": "Voltado ao publico feminino",
                            "definition": "A historia e voltada principalmente ao publico feminino, com ponto de vista, caracterizacao e logica emocional centrados em uma protagonista mulher.",
                        },
                    ],
                    max_items=1,
                ),
                _translated_category(
                    "000003",
                    "Genero",
                    [
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8c9f1",
                            "name": "Etica familiar",
                            "definition": "Foca em relacoes, responsabilidades, conflitos, reconciliacao ou traicao dentro de uma familia comum.",
                        },
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8c9f2",
                            "name": "Suspense sobrenatural",
                            "definition": "Cria medo e tensao por meio de eventos estranhos, historias de fantasmas, maldicoes ou sinais sobrenaturais.",
                        },
                    ],
                    max_items=3,
                ),
                _translated_category(
                    "000006",
                    "Emocao",
                    [
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8ca01",
                            "name": "Sofrimento",
                            "definition": "Cria deliberadamente tristeza, repressao, injustica ou ferida emocional.",
                        },
                        {
                            "label_id": "65f0a1b2c3d4e5f6a7b8ca02",
                            "name": "Vinganca satisfatoria",
                            "definition": "Cria prazer por meio de contra-ataque, virada, punicao dos ofensores ou compensacao.",
                        },
                    ],
                    max_items=3,
                ),
            ],
        },
    }


def _validate_label_translation_params(job_params: dict[str, Any]) -> dict[str, Any]:
    labels = job_params.get("labels")
    if not isinstance(labels, list) or not labels:
        raise ValueError("job_params.labels must be a non-empty array")
    normalized_labels: list[dict[str, Any]] = []
    for index, label in enumerate(labels):
        if not isinstance(label, dict):
            raise ValueError(f"job_params.labels[{index}] must be an object")
        label_id = label.get("label_id")
        if not isinstance(label_id, str) or not label_id.strip():
            raise ValueError(f"job_params.labels[{index}].label_id must be a non-empty string")
        source_language = label.get("source_language")
        if not isinstance(source_language, str):
            raise ValueError(f"job_params.labels[{index}].source_language must be a string")
        validate_business_language(source_language)
        target_languages = label.get("target_languages")
        if not isinstance(target_languages, list) or not target_languages:
            raise ValueError(f"job_params.labels[{index}].target_languages must be a non-empty array")
        for language in target_languages:
            if not isinstance(language, str):
                raise ValueError(f"job_params.labels[{index}].target_languages must contain strings")
            validate_business_language(language)
        if len(target_languages) != len(set(target_languages)):
            raise ValueError(f"job_params.labels[{index}].target_languages must not contain duplicates")
        expected = sorted(target_languages, key=SUPPORTED_BUSINESS_LANGUAGES.index)
        if target_languages != expected:
            raise ValueError(f"job_params.labels[{index}].target_languages must follow business language order")
        display_name = label.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValueError(f"job_params.labels[{index}].display_name must be a non-empty string")
        definition = label.get("definition")
        if not isinstance(definition, str) or not definition.strip():
            raise ValueError(f"job_params.labels[{index}].definition must be a non-empty string")
        normalized_labels.append(
            {
                "label_id": label_id,
                "source_language": source_language,
                "target_languages": target_languages,
                "display_name": display_name,
                "definition": definition,
            }
        )
    return {"labels": normalized_labels}


def _default_translation_job_params() -> dict[str, Any]:
    return {
        "labels": [
            {
                "label_id": "65f0a1b2c3d4e5f6a7b8c901",
                "source_language": "zh",
                "target_languages": ["en", "es", "pt"],
                "display_name": "男频",
                "definition": "核心受众为男性群体，叙事视角、人物塑造、价值观以男性主角为核心。",
            },
            {
                "label_id": "65f0a1b2c3d4e5f6a7b8c902",
                "source_language": "zh",
                "target_languages": ["en", "es", "ko"],
                "display_name": "女频",
                "definition": "核心受众为女性群体，叙事视角、人物塑造、情感逻辑以女性主角为核心。",
            },
        ],
    }


def _translation_lookup(language: str) -> tuple[dict[str, Any], dict[str, Any]]:
    translated_schema = _translated_schema_by_language().get(language, {"categories": []})
    categories_by_id = {
        category["category_id"]: category
        for category in translated_schema.get("categories", [])
        if isinstance(category, dict) and isinstance(category.get("category_id"), str)
    }
    labels_by_id: dict[str, Any] = {}
    for category in categories_by_id.values():
        for label in category.get("labels", []):
            if isinstance(label, dict) and isinstance(label.get("label_id"), str):
                labels_by_id[label["label_id"]] = label
    return categories_by_id, labels_by_id


def _ko_label_translations() -> dict[str, dict[str, str]]:
    return {
        "65f0a1b2c3d4e5f6a7b8c901": {
            "name": "남성향",
            "definition": "핵심 독자는 남성이며, 서사 시점과 인물 전개가 남성 주인공을 중심으로 전개됩니다.",
        },
        "65f0a1b2c3d4e5f6a7b8c902": {
            "name": "여성향",
            "definition": "핵심 독자는 여성이며, 서사 시점과 인물 설정, 감정선이 여성 주인공을 중심으로 전개됩니다.",
        },
    }


def _translation_labels(job_params: MockJobParams) -> list[dict[str, Any]]:
    object_job_params = _object_job_params(job_params)
    return object_job_params["labels"]


def _translated_label_text(label: dict[str, Any], language: str) -> dict[str, str]:
    if language == "ko":
        translated = _ko_label_translations().get(label["label_id"])
        if translated:
            return translated
    translated = _translation_lookup(language)[1].get(label["label_id"])
    if translated:
        return {"name": translated["name"], "definition": translated["definition"]}
    return {"name": label["display_name"], "definition": label["definition"]}


def _translated_labels(job_params: MockJobParams) -> list[dict[str, Any]]:
    translated_labels: list[dict[str, Any]] = []
    for label in _translation_labels(job_params):
        translated_labels.append(
            {
                "label_id": label["label_id"],
                "langs": {
                    language: _translated_label_text(label, language)
                    for language in label["target_languages"]
                },
            }
        )
    return translated_labels


def _translated_label_result(job_params: MockJobParams) -> dict[str, Any]:
    object_job_params = _object_job_params(job_params)
    labels = _translation_labels(object_job_params)
    translated_labels = _translated_labels(object_job_params)
    return {
        "artifacts": [
            {
                "key": "translated_labels",
                "type": "json",
                "label": "翻译后的标签",
                "content": translated_labels,
            },
        ],
        "signals": {
            "source_schema_hash": _hash_json(labels),
            "translated_schemas_hash": _hash_json(translated_labels),
        },
    }


def _mock_progress(job_type: str, job_status: str) -> dict[str, Any]:
    if job_status == "queued":
        return {"percent": 0, "message": "job accepted and waiting for mock execution", "stage": "queued"}
    if job_status == "running":
        if _is_translation_job(job_type):
            return {"percent": 55, "message": "translating tag schema names and definitions", "stage": "translating"}
        return {"percent": 65, "message": "generating short drama tags and preparing RS write payload", "stage": "tagging"}
    if job_status == "succeeded":
        return {"percent": 100, "message": "finished", "stage": "finished"}
    return {"percent": 100, "message": "mock failure generated for integration testing", "stage": "failed"}


def _object_job_params(job_params: MockJobParams) -> dict[str, Any]:
    if isinstance(job_params, list):
        raise ValidationAppError("INVALID_INPUT", "Mock job_params must be an object for this job_type.", {})
    return job_params


def _translation_source_languages(job_params: MockJobParams) -> list[str]:
    return sorted(
        {label["source_language"] for label in _translation_labels(job_params)},
        key=SUPPORTED_BUSINESS_LANGUAGES.index,
    )


def _translation_target_languages(job_params: MockJobParams) -> list[str]:
    languages = {
        language
        for label in _translation_labels(job_params)
        for language in label["target_languages"]
    }
    return sorted(languages, key=SUPPORTED_BUSINESS_LANGUAGES.index)


def _mock_error(job_type: str, job_params: MockJobParams) -> dict[str, Any]:
    if _is_translation_job(job_type):
        return {
            "code": "INVALID_LABEL_TRANSLATION_INPUT",
            "message": "labels contains invalid translation input",
            "details": {
                "label_id": "65f0a1b2c3d4e5f6a7b8c901",
                "source_languages": _translation_source_languages(job_params),
                "target_languages": _translation_target_languages(job_params),
            },
        }
    object_job_params = _object_job_params(job_params)
    return {
        "code": "MODEL_OUTPUT_INVALID",
        "message": "AI generated tagging result is not valid for the RS tag schema.",
        "details": {
            "t_book_id": object_job_params.get("t_book_id", MOCK_TAGGING_RESULT["t_book_id"]),
            "reason": "selected tag label name is not in schema",
            "rejected_category_id": "000006",
        },
    }


def _mock_metadata(
    mock_client: MockClient,
    job_type: str,
    job_status: str,
    job_params: MockJobParams,
    request_metadata: dict[str, Any],
) -> dict[str, Any]:
    metadata = dict(request_metadata)
    metadata.update(
        {
            "source_service": mock_client,
            "business_scene": _business_scene_for_job_type(job_type),
            "api_version": MOCK_API_VERSION,
        }
    )
    if _is_translation_job(job_type):
        metadata["mock_translation"] = {
            "source_languages": _translation_source_languages(job_params),
            "target_languages": _translation_target_languages(job_params),
            "label_count": len(_translation_labels(job_params)),
            "artifact_keys": ["translated_labels"],
        }
    else:
        object_job_params = _object_job_params(job_params)
        work_context = object_job_params.get("work_context", {})
        tags = MOCK_TAGGING_RESULT["tags"]
        metadata["mock_tagging"] = {
            "t_book_id": object_job_params.get("t_book_id", MOCK_TAGGING_RESULT["t_book_id"]),
            "title": work_context.get("title", "Acting for Real-He Fell First"),
            "rs_write": {
                "saved": job_status == "succeeded",
                "source": "ai_auto",
                "category_count": len(tags),
                "label_count": sum(len(items) for items in tags.values()),
            },
        }
    return metadata


def _mock_callback(job_type: str, job_status: str, has_callback: bool) -> dict[str, Any]:
    if not has_callback or _is_translation_job(job_type):
        return {"status": "not_configured", "attempts": 0}
    if job_status in {"succeeded", "failed"}:
        return {"status": "delivered", "attempts": 1}
    return {"status": "pending", "attempts": 0}


def _mock_job_view(
    job_id: uuid.UUID,
    mock_client: MockClient,
    job_type: str,
    job_status: str,
) -> dict[str, Any]:
    created_job = MOCK_JOBS.get(job_id)
    if created_job:
        if created_job["mock_client"] != mock_client:
            raise ValidationAppError(
                "INVALID_INPUT",
                f"Mock job {job_id} does not belong to {mock_client.upper()} mock interface.",
                {"expected_mock_client": created_job["mock_client"], "actual_mock_client": mock_client},
            )
        job_type = created_job["job_type"]
        client_request_id = created_job["client_request_id"]
        job_params = created_job["job_params"]
        request_metadata = created_job["metadata"]
        has_callback = created_job["has_callback"]
    else:
        _validate_mock_job_type(mock_client, job_type)
        client_request_id = f"mock:{mock_client}:{job_id}"
        job_params = _default_translation_job_params() if _is_translation_job(job_type) else {}
        request_metadata = {}
        has_callback = False
    progress = _mock_progress(job_type, job_status)
    result = None
    if job_status == "succeeded" and _is_translation_job(job_type):
        result = _translated_label_result(job_params)
    error = None
    if job_status == "failed":
        error = _mock_error(job_type, job_params)
    return {
        "job_id": job_id,
        "client_request_id": client_request_id,
        "job_type": job_type,
        "status": job_status,
        "progress": progress,
        "result": result,
        "error": error,
        "callback": _mock_callback(job_type, job_status, has_callback),
        "metadata": _mock_metadata(mock_client, job_type, job_status, job_params, request_metadata),
        "created_at": MOCK_CREATED_AT,
        "started_at": MOCK_STARTED_AT if job_status != "queued" else None,
        "finished_at": MOCK_FINISHED_AT if job_status in {"succeeded", "failed"} else None,
    }


def _create_mock_ai_job(mock_client: MockClient, payload: CreateJobRequest | MockCreateJobRequest) -> dict[str, Any]:
    _validate_mock_job_type(mock_client, payload.job_type)
    job_id = _job_id_for_payload(mock_client, payload)
    MOCK_JOBS[job_id] = {
        "mock_client": mock_client,
        "client_request_id": payload.client_request_id,
        "job_type": payload.job_type,
        "job_params": payload.job_params,
        "metadata": payload.metadata,
        "has_callback": payload.callback is not None,
    }
    return {
        "job_id": job_id,
        "client_request_id": payload.client_request_id,
        "job_type": payload.job_type,
        "status": "queued",
        "status_url": _mock_job_path(mock_client, job_id),
        "created_at": MOCK_CREATED_AT,
    }


CPP_CREATE_REQUEST_EXAMPLE = {
    "client_request_id": "cpp:204200150000004872:initial:20260615",
    "job_type": "short_drama.tagging.initial",
    "job_params": {
        "t_book_id": "204200150000004872",
        "work_context": {
            "title": "Acting for Real-He Fell First",
            "synopsis": "To change her fate and pay off her debts, the heroine is drawn into a family conflict around a staged wedding.",
            "subtitle_language": "en",
            "series_structure": "continuous_series",
            "content_type": "短剧",
            "episode_count": 80,
        },
        "assets": [
            {
                "asset_type": "subtitle_srt",
                "episode_no": 1,
                "format": "srt",
                "text": "1\n00:00:01,000 --> 00:00:03,000\nI will not let them decide my life.",
            }
        ],
    },
    "callback": {"url": "https://cpp.example.com/ai-jobs/callback"},
    "metadata": {"source_service": "cpp", "business_scene": "short_drama_tagging"},
}

RS_CREATE_REQUEST_EXAMPLE = {
    "client_request_id": "rs:tag-labels:batch:20260617",
    "job_type": "short_drama.tag_labels.translation",
    "job_params": _default_translation_job_params(),
    "metadata": {"source_service": "rs", "business_scene": "tag_labels_translation"},
}

CPP_CREATE_RESPONSE_EXAMPLE = {
    "job_id": "7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
    "client_request_id": "cpp:204200150000004872:initial:20260615",
    "job_type": "short_drama.tagging.initial",
    "status": "queued",
    "status_url": "/api/v1/mock/cpp/ai-jobs/jobs/7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
    "created_at": "2026-06-15T10:00:00Z",
}

RS_CREATE_RESPONSE_EXAMPLE = {
    "job_id": "0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
    "client_request_id": "rs:tag-labels:batch:20260617",
    "job_type": "short_drama.tag_labels.translation",
    "status": "queued",
    "status_url": "/api/v1/mock/rs/ai-jobs/jobs/0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
    "created_at": "2026-06-15T10:00:00Z",
}

CPP_STATUS_RESPONSE_EXAMPLE = {
    "job_id": "7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
    "client_request_id": "cpp:204200150000004872:initial:20260615",
    "job_type": "short_drama.tagging.initial",
    "status": "succeeded",
    "progress": {"percent": 100, "message": "finished", "stage": "finished"},
    "result": None,
    "error": None,
    "callback": {"status": "delivered", "attempts": 1, "next_retry_at": None, "last_error": None},
    "metadata": {
        "source_service": "cpp",
        "business_scene": "short_drama_tagging",
        "api_version": "v1",
        "mock_tagging": {
            "t_book_id": "204200150000004872",
            "title": "Acting for Real-He Fell First",
            "rs_write": {"saved": True, "source": "ai_auto", "category_count": 3, "label_count": 4},
        },
    },
    "created_at": "2026-06-15T10:00:00Z",
    "started_at": "2026-06-15T10:00:05Z",
    "finished_at": "2026-06-15T10:01:30Z",
}

RS_STATUS_RESPONSE_EXAMPLE = {
    "job_id": "0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
    "client_request_id": "rs:tag-labels:batch:20260617",
    "job_type": "short_drama.tag_labels.translation",
    "status": "succeeded",
    "progress": {"percent": 100, "message": "finished", "stage": "finished"},
    "result": _translated_label_result(RS_CREATE_REQUEST_EXAMPLE["job_params"]),
    "error": None,
    "callback": {"status": "not_configured", "attempts": 0, "next_retry_at": None, "last_error": None},
    "metadata": {
        "source_service": "rs",
        "business_scene": "tag_labels_translation",
        "api_version": "v1",
        "mock_translation": {
            "source_languages": ["zh"],
            "target_languages": ["en", "es", "pt", "ko"],
            "label_count": 2,
            "artifact_keys": ["translated_labels"],
        },
    },
    "created_at": "2026-06-15T10:00:00Z",
    "started_at": "2026-06-15T10:00:05Z",
    "finished_at": "2026-06-15T10:01:30Z",
}


@router.post(
    "/api/v1/mock/cpp/ai-jobs/jobs",
    response_model=CreateJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "CPP mock job accepted.",
            "content": {"application/json": {"example": CPP_CREATE_RESPONSE_EXAMPLE}},
        }
    },
)
async def create_cpp_mock_ai_job(
    payload: CreateJobRequest = Body(
        openapi_examples={
            "cpp_tagging_initial": {
                "summary": "CPP 首次短剧打标 mock",
                "value": CPP_CREATE_REQUEST_EXAMPLE,
            }
        }
    ),
):
    return _create_mock_ai_job("cpp", payload)


@router.get(
    "/api/v1/mock/cpp/ai-jobs/jobs/{job_id}",
    response_model=JobStatusResponse,
    responses={
        status.HTTP_200_OK: {
            "description": "CPP mock job status.",
            "content": {"application/json": {"example": CPP_STATUS_RESPONSE_EXAMPLE}},
        }
    },
)
async def get_cpp_mock_ai_job(
    job_id: uuid.UUID,
    job_type: MockJobType = Query(default="short_drama.tagging.initial"),
    job_status: MockJobStatus = Query(default="succeeded", alias="status"),
):
    return _mock_job_view(job_id, "cpp", job_type, job_status)


@router.post(
    "/api/v1/mock/rs/ai-jobs/jobs",
    response_model=CreateJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "RS mock job accepted.",
            "content": {"application/json": {"example": RS_CREATE_RESPONSE_EXAMPLE}},
        }
    },
)
async def create_rs_mock_ai_job(
    payload: MockCreateJobRequest = Body(
        openapi_examples={
            "rs_tag_labels_translation": {
                "summary": "RS 多标签翻译 mock",
                "value": RS_CREATE_REQUEST_EXAMPLE,
            }
        }
    ),
):
    return _create_mock_ai_job("rs", payload)


@router.get(
    "/api/v1/mock/rs/ai-jobs/jobs/{job_id}",
    response_model=JobStatusResponse,
    responses={
        status.HTTP_200_OK: {
            "description": "RS mock job status.",
            "content": {"application/json": {"example": RS_STATUS_RESPONSE_EXAMPLE}},
        }
    },
)
async def get_rs_mock_ai_job(
    job_id: uuid.UUID,
    job_status: MockJobStatus = Query(default="succeeded", alias="status"),
):
    return _mock_job_view(job_id, "rs", "short_drama.tag_labels.translation", job_status)
