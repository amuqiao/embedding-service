import hashlib
import json

from fastapi.testclient import TestClient

from app.main import app


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer test-token",
        "X-AI-Service-Caller-ID": "mock-tester",
    }


def _rs_translation_job_params(target_languages: list[str] | None = None) -> dict:
    return {
        "labels": [
            {
                "label_id": "65f0a1b2c3d4e5f6a7b8c901",
                "source_language": "zh",
                "target_languages": target_languages or ["en", "es", "pt"],
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
        ]
    }


def _hash_json(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def test_cpp_mock_ai_job_create_and_status(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = TestClient(app)

    create_response = client.post(
        "/api/v1/mock/cpp/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "cpp:204200150000004872:initial:20260615",
            "job_type": "short_drama.tagging.initial",
            "job_params": {
                "t_book_id": "204200150000004872",
                "work_context": {"title": "Acting for Real-He Fell First"},
                "assets": [{"asset_type": "subtitle_srt", "format": "srt", "text": "1\nHello."}],
            },
            "callback": {"url": "https://cpp.example.com/ai-jobs/callback"},
            "metadata": {"source_service": "cpp"},
        },
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
    client = TestClient(app)

    create_response = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:tag-schema-default:en",
            "job_type": "short_drama.tag_labels.translation",
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
    assert body["job_type"] == "short_drama.tag_labels.translation"
    assert body["result"]["artifacts"][0]["key"] == "translated_labels"
    translated_labels = body["result"]["artifacts"][0]["content"]
    assert translated_labels[0]["label_id"] == "65f0a1b2c3d4e5f6a7b8c901"
    assert translated_labels[0]["langs"]["en"]["name"] == "Male-oriented"
    assert translated_labels[0]["langs"]["es"]["name"] == "Orientado a hombres"
    assert translated_labels[0]["langs"]["pt"]["name"] == "Voltado ao publico masculino"
    assert translated_labels[1]["langs"]["ko"]["name"] == "여성향"
    assert body["result"]["signals"]["source_schema_hash"].startswith("sha256:")
    assert body["result"]["signals"]["translated_schemas_hash"].startswith("sha256:")
    assert body["metadata"]["source_service"] == "rs"
    assert body["metadata"]["business_scene"] == "tag_labels_translation"
    assert body["metadata"]["api_version"] == "v1"
    assert body["metadata"]["mock_translation"] == {
        "source_languages": ["zh"],
        "target_languages": ["en", "es", "pt", "ko"],
        "label_count": 2,
        "artifact_keys": ["translated_labels"],
    }


def test_rs_mock_translation_result_is_derived_from_request_labels(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = TestClient(app)
    job_params = {
        "labels": [
            {
                "label_id": "custom-label",
                "source_language": "zh",
                "target_languages": ["en"],
                "display_name": "自定义标签",
                "definition": "自定义释义。",
            }
        ]
    }
    create_response = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:custom-labels:en",
            "job_type": "short_drama.tag_labels.translation",
            "job_params": job_params,
        },
    )
    status_response = client.get(create_response.json()["status_url"], headers=_headers())

    body = status_response.json()
    translated = body["result"]["artifacts"][0]["content"][0]
    assert translated == {
        "label_id": "custom-label",
        "langs": {
            "en": {
                "name": "自定义标签",
                "definition": "自定义释义。",
            }
        },
    }
    assert body["result"]["signals"]["source_schema_hash"] == _hash_json(job_params["labels"])
    assert body["metadata"]["mock_translation"]["label_count"] == 1


def test_rs_mock_translation_accepts_independent_label_language_sets(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = TestClient(app)
    job_params = {
        "labels": [
            {
                "label_id": "65f0a1b2c3d4e5f6a7b8c901",
                "source_language": "zh",
                "target_languages": ["en", "es", "pt"],
                "display_name": "男频",
                "definition": "核心受众为男性群体。",
            },
            {
                "label_id": "65f0a1b2c3d4e5f6a7b8c902",
                "source_language": "zh",
                "target_languages": ["en", "es", "ko"],
                "display_name": "女频",
                "definition": "核心受众为女性群体。",
            },
        ]
    }
    response = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:independent-labels",
            "job_type": "short_drama.tag_labels.translation",
            "job_params": job_params,
        },
    )

    assert response.status_code == 202


def test_rs_mock_rejects_list_job_params(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = TestClient(app)

    response = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:tag-schema-default:en,es,pt",
            "job_type": "short_drama.tag_labels.translation",
            "job_params": [{"source_language": "zh", "target_languages": ["en", "es", "pt"]}],
            "metadata": {"source_service": "rs"},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_rs_mock_reuses_translation_param_validation(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = TestClient(app)

    bad_language_order = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:bad-language-order",
            "job_type": "short_drama.tag_labels.translation",
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
            "job_type": "short_drama.tag_labels.translation",
            "job_params": bad_language_params,
        },
    )
    assert bad_language.status_code == 422
    assert bad_language.json()["error"]["code"] == "INVALID_INPUT"


def test_cpp_mock_failed_status_returns_contract_error_details(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = TestClient(app)

    create_response = client.post(
        "/api/v1/mock/cpp/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "cpp:failed",
            "job_type": "short_drama.tagging.initial",
            "job_params": {"t_book_id": "204200150000004872"},
        },
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
    client = TestClient(app)

    create_response = client.post(
        "/api/v1/mock/rs/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "rs:failed",
            "job_type": "short_drama.tag_labels.translation",
            "job_params": _rs_translation_job_params(),
        },
    )
    created = create_response.json()

    response = client.get(f"{created['status_url']}?status=failed", headers=_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == {
        "code": "INVALID_LABEL_TRANSLATION_INPUT",
        "message": "labels contains invalid translation input",
        "details": {
            "label_id": "65f0a1b2c3d4e5f6a7b8c901",
            "source_languages": ["zh"],
            "target_languages": ["en", "es", "pt", "ko"],
        },
    }


def test_cpp_mock_rejects_rs_translation_job_type(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = TestClient(app)

    response = client.post(
        "/api/v1/mock/cpp/ai-jobs/jobs",
        headers=_headers(),
        json={
            "job_type": "short_drama.tag_labels.translation",
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
    client = TestClient(app)

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
    assert body["error"]["details"]["supported_job_types"] == ["short_drama.tag_labels.translation"]


def test_mock_job_query_rejects_cross_prefix_job_id(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = TestClient(app)

    create_response = client.post(
        "/api/v1/mock/cpp/ai-jobs/jobs",
        headers=_headers(),
        json={
            "client_request_id": "cpp:cross-prefix",
            "job_type": "short_drama.tagging.incremental",
            "job_params": {},
        },
    )
    created = create_response.json()
    wrong_status_url = created["status_url"].replace("/mock/cpp/", "/mock/rs/")

    response = client.get(wrong_status_url, headers=_headers())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_mock_job_query_rejects_unknown_status(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = TestClient(app)

    response = client.get(
        "/api/v1/mock/cpp/ai-jobs/jobs/0a9be3fb-f01b-4f5d-90b5-4148c4a61df1?status=done",
        headers=_headers(),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_old_mock_fixture_routes_are_not_exposed(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = TestClient(app)

    assert client.get("/api/v1/mock/tag-schemas/default", headers=_headers()).status_code == 404
    assert client.post("/api/v1/mock/ai-tag-results", headers=_headers(), json={}).status_code == 404
    assert client.post("/api/v1/mock/ai-jobs/jobs", headers=_headers(), json={}).status_code == 404


def test_openapi_declares_cpp_and_rs_mock_job_interfaces():
    schema = app.openapi()

    assert "/api/v1/mock/cpp/ai-jobs/jobs" in schema["paths"]
    assert "/api/v1/mock/cpp/ai-jobs/jobs/{job_id}" in schema["paths"]
    assert "/api/v1/mock/rs/ai-jobs/jobs" in schema["paths"]
    assert "/api/v1/mock/rs/ai-jobs/jobs/{job_id}" in schema["paths"]
    assert "/api/v1/mock/tag-schemas/default" not in schema["paths"]
    assert "/api/v1/mock/ai-tag-results" not in schema["paths"]
    assert "/api/v1/mock/ai-jobs/jobs" not in schema["paths"]


def test_openapi_provides_mock_request_and_response_examples():
    schema = app.openapi()

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
        "rs_tag_labels_translation"
    ]["value"]
    rs_response_example = rs_post["responses"]["202"]["content"]["application/json"]["example"]
    assert rs_request_example["job_type"] == "short_drama.tag_labels.translation"
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
    assert rs_get_example["result"]["artifacts"][0]["key"] == "translated_labels"
    assert rs_get_example["result"]["artifacts"][0]["content"][0]["langs"]["en"]["name"] == "Male-oriented"
