import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.routes import mock_interfaces as mock_routes
from app.main import app
from app.schemas.jobs import CallbackEnvelope, CreateJobRequest
from app.services.job_runtime import payload_hash
from app.services.jobs import validate_create_contract, validate_job_status_payload

RS_TRANSLATION_JOB_TYPE = "short_drama.tag_schema.translation"
MOCK_DATA_DIR = Path("docs/接口层/mock-data/short_drama_tagging")


def _ensure_mock_routes() -> None:
    if any(getattr(route, "path", "") == "/api/v1/mock/cpp/ai-jobs/jobs" for route in app.routes):
        return
    app.include_router(mock_routes.router)
    app.openapi_schema = None


def _mock_client() -> TestClient:
    _ensure_mock_routes()
    return TestClient(app)


def _mock_openapi() -> dict:
    _ensure_mock_routes()
    return app.openapi()


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer test-token",
        "X-AI-Service-Caller-ID": "mock-tester",
    }


def _rs_translation_job_params(target_languages: list[str] | None = None) -> dict:
    first_targets = target_languages or ["en", "es", "pt"]
    second_targets = target_languages or ["en", "es", "ko"]
    return {
        "labels": [
            {
                "label_id": "65f0a1b2c3d4e5f6a7b8c901",
                "source_language": "zh",
                "target_languages": first_targets,
                "display_name": "男频",
                "definition": "核心受众为男性群体，叙事视角、人物塑造、价值观以男性主角为核心。",
            },
            {
                "label_id": "65f0a1b2c3d4e5f6a7b8c902",
                "source_language": "zh",
                "target_languages": second_targets,
                "display_name": "女频",
                "definition": "核心受众为女性群体，叙事视角、人物塑造、情感逻辑以女性主角为核心。",
            },
        ],
    }


def _cpp_tagging_request(
    *,
    job_type: str = "short_drama.tagging.initial",
    client_request_id: str = "cpp:204200150000004872:initial:20260615",
) -> dict:
    return {
        "client_request_id": client_request_id,
        "job_type": job_type,
        "job_params": {
            "t_book_id": "204200150000004872",
            "work_context": {
                "title": "Acting for Real-He Fell First",
                "synopsis": "To change her fate and pay off her debts, the heroine enters a staged wedding conflict.",
                "subtitle_language": "en",
                "series_structure": "continuous_series",
                "content_type": "短剧",
                "episode_count": 1,
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
        "metadata": {"source_service": "cpp"},
    }


def test_cpp_mock_ai_job_create_and_status(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()

    create_response = client.post(
        "/api/v1/mock/cpp/ai-jobs/jobs",
        headers=_headers(),
        json=_cpp_tagging_request(),
    )

    assert create_response.status_code == 202
    created = create_response.json()
    assert created["status"] == "queued"
    assert created["status_url"].startswith("/api/v1/mock/cpp/ai-jobs/jobs/")

    status_response = client.get(created["status_url"], headers=_headers())

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == "succeeded"
    assert body["client_request_id"] == "cpp:204200150000004872:initial:20260615"
    assert body["job_type"] == "short_drama.tagging.initial"
    assert body["result"] is None
    assert body["metadata"]["source_service"] == "cpp"
    assert body["metadata"]["business_scene"] == "short_drama_tagging"
    assert body["metadata"]["api_version"] == "v1"
    assert body["metadata"]["mock_tagging"]["t_book_id"] == "204200150000004872"
    assert body["metadata"]["mock_tagging"]["title"] == "Acting for Real-He Fell First"
    assert body["metadata"]["mock_tagging"]["rs_write"] == {
        "saved": True,
        "source": "ai_auto",
        "category_count": 3,
        "label_count": 4,
    }
    assert body["callback"] == {"status": "delivered", "attempts": 1, "next_retry_at": None, "last_error": None}


def test_rs_mock_ai_job_create_and_translation_status(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()

    create_response = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:tag-schema-default:en",
            "job_type": RS_TRANSLATION_JOB_TYPE,
            "job_params": _rs_translation_job_params(),
            "metadata": {"source_service": "rs"},
        },
    )

    assert create_response.status_code == 202
    created = create_response.json()
    assert created["status"] == "queued"
    assert created["status_url"].startswith("/api/v1/mock/rs/ai-jobs/jobs/")

    status_response = client.get(created["status_url"], headers=_headers())

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == "succeeded"
    assert body["client_request_id"] == "rs:tag-schema-default:en"
    assert body["job_type"] == RS_TRANSLATION_JOB_TYPE
    artifacts = body["result"]["artifacts"]
    assert artifacts[0]["label_id"] == "65f0a1b2c3d4e5f6a7b8c901"
    assert artifacts[0]["langs"]["en"]["name"] == "Male-oriented"
    assert artifacts[0]["langs"]["es"]["name"] == "Orientado a hombres"
    assert artifacts[0]["langs"]["pt"]["name"] == "Voltado ao publico masculino"
    assert artifacts[1]["label_id"] == "65f0a1b2c3d4e5f6a7b8c902"
    assert artifacts[1]["langs"]["ko"]["name"] == "여성향"
    assert body["result"]["signals"]["source_schema_hash"].startswith("sha256:")
    assert body["result"]["signals"]["translated_schemas_hash"].startswith("sha256:")
    assert body["metadata"]["source_service"] == "rs"
    assert body["metadata"]["business_scene"] == "tag_schema_translation"
    assert body["metadata"]["api_version"] == "v1"
    assert body["metadata"]["mock_translation"] == {
        "source_languages": ["zh"],
        "target_languages": ["en", "es", "pt", "ko"],
        "label_count": 2,
        "artifact_shape": "label_translations",
    }


def test_rs_mock_translation_result_is_derived_from_request_schema(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()
    job_params = {
        "labels": [
            {
                "label_id": "custom-label",
                "source_language": "zh",
                "target_languages": ["en"],
                "display_name": "自定义标签",
                "definition": "自定义释义。",
            }
        ],
    }
    create_response = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:custom-labels:en",
            "job_type": RS_TRANSLATION_JOB_TYPE,
            "job_params": job_params,
        },
    )
    status_response = client.get(create_response.json()["status_url"], headers=_headers())

    body = status_response.json()
    artifact = body["result"]["artifacts"][0]
    assert artifact["label_id"] == "custom-label"
    assert artifact["langs"]["en"]["name"] == "自定义标签"
    assert artifact["langs"]["en"]["definition"] == "自定义释义。"
    assert body["result"]["signals"]["source_schema_hash"] == payload_hash({"labels": job_params["labels"]})
    assert body["metadata"]["mock_translation"]["label_count"] == 1


def test_rs_mock_translation_accepts_schema_translation_languages(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()
    response = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:tag-schema:four-languages",
            "job_type": RS_TRANSLATION_JOB_TYPE,
            "job_params": _rs_translation_job_params(["en", "es", "pt", "ko"]),
        },
    )

    assert response.status_code == 202


def test_rs_mock_rejects_list_job_params(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()

    response = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:tag-schema-default:en,es,pt",
            "job_type": RS_TRANSLATION_JOB_TYPE,
            "job_params": [{"labels": _rs_translation_job_params()["labels"]}],
            "metadata": {"source_service": "rs"},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_rs_mock_reuses_translation_param_validation(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()

    bad_language_order = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:bad-language-order",
            "job_type": RS_TRANSLATION_JOB_TYPE,
            "job_params": _rs_translation_job_params(["pt", "en"]),
        },
    )
    assert bad_language_order.status_code == 422
    assert bad_language_order.json()["error"]["code"] == "INVALID_INPUT"

    bad_language_params = _rs_translation_job_params(["en"])
    bad_language_params["labels"][0]["target_languages"] = ["kr"]
    bad_language = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:bad-language",
            "job_type": RS_TRANSLATION_JOB_TYPE,
            "job_params": bad_language_params,
        },
    )
    assert bad_language.status_code == 422
    assert bad_language.json()["error"]["code"] == "INVALID_INPUT"


def test_cpp_mock_reuses_tagging_param_validation(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()
    payload = _cpp_tagging_request()
    payload["job_params"]["work_context"].pop("subtitle_language")

    response = client.post("/api/v1/mock/cpp/ai-jobs/jobs", headers=_headers(), json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_rs_mock_rejects_callback_by_translation_contract(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()

    response = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:callback-not-supported",
            "job_type": RS_TRANSLATION_JOB_TYPE,
            "job_params": _rs_translation_job_params(),
            "callback": {"url": "https://rs.example.com/callback"},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_cpp_mock_failed_status_returns_contract_error_details(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()

    create_response = client.post(
        "/api/v1/mock/cpp/ai-jobs/jobs",
        headers=_headers(),
        json=_cpp_tagging_request(client_request_id="cpp:failed"),
    )
    created = create_response.json()

    response = client.get(f"{created['status_url']}?status=failed", headers=_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["progress"]["message"] == "mock failure generated for integration testing"
    assert body["error"] == {
        "code": "MODEL_OUTPUT_INVALID",
        "message": "AI generated tagging result is not valid for the RS tag schema.",
        "details": {
            "t_book_id": "204200150000004872",
            "reason": "selected tag label name is not in schema",
            "rejected_category_id": "000006",
        },
    }


def test_rs_mock_failed_status_returns_contract_error_details(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()

    create_response = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:failed",
            "job_type": RS_TRANSLATION_JOB_TYPE,
            "job_params": _rs_translation_job_params(),
        },
    )
    created = create_response.json()

    response = client.get(f"{created['status_url']}?status=failed", headers=_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == {
        "code": "TRANSLATION_FAILED",
        "message": "tag schema translation mock failed",
        "details": {
            "source_languages": ["zh"],
            "target_languages": ["en", "es", "pt", "ko"],
        },
    }


def test_cpp_mock_rejects_rs_translation_job_type(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()

    response = client.post(
        "/api/v1/mock/cpp/ai-jobs/jobs",
        headers=_headers(),
        json={
            "job_type": RS_TRANSLATION_JOB_TYPE,
            "job_params": {},
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "INVALID_JOB_TYPE"
    assert body["error"]["details"]["supported_job_types"] == [
        "short_drama.tagging.incremental",
        "short_drama.tagging.initial",
    ]


def test_rs_mock_rejects_cpp_tagging_job_type(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()

    response = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "job_type": "short_drama.tagging.initial",
            "job_params": _rs_translation_job_params(),
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "INVALID_JOB_TYPE"
    assert body["error"]["details"]["supported_job_types"] == [RS_TRANSLATION_JOB_TYPE]


def test_mock_job_query_rejects_cross_prefix_job_id(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()

    create_response = client.post(
        "/api/v1/mock/cpp/ai-jobs/jobs",
        headers=_headers(),
        json=_cpp_tagging_request(
            job_type="short_drama.tagging.incremental",
            client_request_id="cpp:cross-prefix",
        ),
    )
    created = create_response.json()
    wrong_status_url = created["status_url"].replace("/mock/cpp/", "/mock/rs/")

    response = client.get(wrong_status_url, headers=_headers())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_mock_job_query_rejects_unknown_status(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()

    response = client.get(
        "/api/v1/mock/cpp/ai-jobs/jobs/0a9be3fb-f01b-4f5d-90b5-4148c4a61df1?status=done",
        headers=_headers(),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_old_mock_fixture_routes_are_not_exposed(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = _mock_client()

    assert client.get("/api/v1/mock/tag-schemas/default", headers=_headers()).status_code == 404
    assert client.post("/api/v1/mock/ai-tag-results", headers=_headers(), json={}).status_code == 404
    assert client.post("/api/v1/mock/ai-jobs/jobs", headers=_headers(), json={}).status_code == 404


def test_openapi_declares_cpp_and_rs_mock_job_interfaces():
    schema = _mock_openapi()

    assert "/api/v1/mock/cpp/ai-jobs/jobs" in schema["paths"]
    assert "/api/v1/mock/cpp/ai-jobs/jobs/{job_id}" in schema["paths"]
    assert "/api/v1/mock/rs/ai-jobs/jobs" in schema["paths"]
    assert "/api/v1/mock/rs/ai-jobs/jobs/{job_id}" in schema["paths"]
    assert "/api/v1/mock/tag-schemas/default" not in schema["paths"]
    assert "/api/v1/mock/ai-tag-results" not in schema["paths"]
    assert "/api/v1/mock/ai-jobs/jobs" not in schema["paths"]


def test_openapi_provides_mock_request_and_response_examples():
    schema = _mock_openapi()

    cpp_post = schema["paths"]["/api/v1/mock/cpp/ai-jobs/jobs"]["post"]
    cpp_request_example = cpp_post["requestBody"]["content"]["application/json"]["examples"][
        "cpp_tagging_initial"
    ]["value"]
    cpp_response_example = cpp_post["responses"]["202"]["content"]["application/json"]["example"]
    assert cpp_request_example["job_type"] == "short_drama.tagging.initial"
    assert cpp_request_example["job_params"]["t_book_id"] == "204200150000004872"
    assert cpp_response_example["status_url"].startswith("/api/v1/mock/cpp/ai-jobs/jobs/")

    rs_post = schema["paths"]["/api/v1/mock/rs/ai-jobs/jobs"]["post"]
    rs_request_example = rs_post["requestBody"]["content"]["application/json"]["examples"][
        "rs_tag_schema_translation"
    ]["value"]
    rs_response_example = rs_post["responses"]["202"]["content"]["application/json"]["example"]
    assert rs_request_example["job_type"] == RS_TRANSLATION_JOB_TYPE
    assert rs_request_example["job_params"]["labels"][0]["source_language"] == "zh"
    assert rs_request_example["job_params"]["labels"][0]["target_languages"] == ["en", "es", "pt"]
    assert rs_response_example["status_url"].startswith("/api/v1/mock/rs/ai-jobs/jobs/")

    cpp_get_example = schema["paths"]["/api/v1/mock/cpp/ai-jobs/jobs/{job_id}"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["example"]
    rs_get_example = schema["paths"]["/api/v1/mock/rs/ai-jobs/jobs/{job_id}"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["example"]
    assert cpp_get_example["metadata"]["mock_tagging"]["rs_write"]["label_count"] == 4
    assert rs_get_example["result"]["artifacts"][0]["label_id"] == "65f0a1b2c3d4e5f6a7b8c901"
    assert rs_get_example["result"]["artifacts"][0]["langs"]["en"]["name"] == "Male-oriented"


def test_mock_data_examples_validate_against_job_contracts():
    cpp_create = CreateJobRequest.model_validate(
        json.loads((MOCK_DATA_DIR / "cpp_create_tagging_job_request.json").read_text(encoding="utf-8"))
    )
    rs_create = CreateJobRequest.model_validate(
        json.loads((MOCK_DATA_DIR / "rs_create_tag_schema_translation_job_request.json").read_text(encoding="utf-8"))
    )

    validate_create_contract(cpp_create)
    validate_create_contract(rs_create)
    validate_job_status_payload(mock_routes.CPP_STATUS_RESPONSE_EXAMPLE)
    validate_job_status_payload(mock_routes.RS_STATUS_RESPONSE_EXAMPLE)

    callback_fixture = json.loads(
        (MOCK_DATA_DIR / "cpp_callback_request.succeeded.json").read_text(encoding="utf-8")
    )
    envelope = CallbackEnvelope.model_validate(callback_fixture["body"])
    assert str(envelope.job_id) == "0a9be3fb-f01b-4f5d-90b5-4148c4a61df1"
    assert envelope.job_type == "short_drama.tagging.initial"
    assert envelope.status == "succeeded"
    assert envelope.error is None
    assert envelope.data["t_book_id"] == "300000000300000279"
    assert envelope.data["result_status"] == "success"
    assert envelope.data["validation_issue_count"] == 0
    assert envelope.data["validation_issues"] == []
    assert envelope.data["reason_codes"] == []
    assert envelope.data["subtitle_language"] == "zh"
    assert envelope.data["requested_schema_language"] == "zh"
    assert "business_scene" not in envelope.data
    assert "job" not in callback_fixture["body"]
    assert "result" not in callback_fixture["body"]
    assert "callback" not in callback_fixture["body"]
