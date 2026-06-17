from fastapi.testclient import TestClient

from app.main import app


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer test-token",
        "X-AI-Service-Caller-ID": "mock-tester",
    }


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
            "job_type": "short_drama.tag_schema.translation",
            "job_params": [
                {
                    "label_id": "bihuihuigu76576585",
                    "source_language": "zh",
                    "target_languages": ["en", "es", "pt"],
                    "display_name": "男频",
                    "definition": "核心受众为男性群体，叙事视角、人物塑造、价值观以男性主角为核心...",
                },
                {
                    "label_id": "bihuihuigu76576585211212",
                    "source_language": "zh",
                    "target_languages": ["en", "es", "kr"],
                    "display_name": "男频",
                    "definition": "核心受众为男性群体，叙事视角、人物塑造、价值观以男性主角为核心...",
                },
            ],
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
    assert body["job_type"] == "short_drama.tag_schema.translation"
    assert body["result"]["artifacts"][0]["key"] == "translated_schemas"
    translated_schemas = body["result"]["artifacts"][0]["content"]
    assert [schema["language"] for schema in translated_schemas] == ["en", "es", "pt"]
    assert translated_schemas[0]["categories"][0]["name"] == "Audience"
    assert translated_schemas[1]["categories"][1]["labels"][0]["name"] == "Etica familiar"
    assert translated_schemas[2]["categories"][2]["labels"][1]["name"] == "Vinganca satisfatoria"
    mutual_exclusion_rules = body["result"]["artifacts"][1]["content"]
    assert mutual_exclusion_rules[0]["label_id"] == "65f0a1b2c3d4e5f6a7b8c9f1"
    assert body["result"]["signals"]["source_schema_hash"].startswith("sha256:")
    assert body["result"]["signals"]["translated_schemas_hash"].startswith("sha256:")
    assert body["metadata"]["source_service"] == "rs"
    assert body["metadata"]["business_scene"] == "tag_schema_translation"
    assert body["metadata"]["api_version"] == "v1"
    assert body["metadata"]["mock_translation"] == {
        "source_language": "zh",
        "target_languages": ["en", "es", "pt", "kr"],
        "category_count": 3,
        "artifact_keys": ["translated_schemas", "mutual_exclusion_rules"],
    }


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
        "code": "RS_RESULT_WRITE_FAILED",
        "message": "AI generated tagging result, but RS rejected the write request.",
        "details": {
            "t_book_id": "204200150000004872",
            "rs_error_code": "INVALID_TAG_RESULT",
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
            "job_type": "short_drama.tag_schema.translation",
            "job_params": [
                {
                    "label_id": "bihuihuigu76576585",
                    "source_language": "zh",
                    "target_languages": ["en", "es", "pt"],
                    "display_name": "男频",
                    "definition": "核心受众为男性群体，叙事视角、人物塑造、价值观以男性主角为核心...",
                },
                {
                    "label_id": "bihuihuigu76576585211212",
                    "source_language": "zh",
                    "target_languages": ["en", "es", "kr"],
                    "display_name": "男频",
                    "definition": "核心受众为男性群体，叙事视角、人物塑造、价值观以男性主角为核心...",
                },
            ],
        },
    )
    created = create_response.json()

    response = client.get(f"{created['status_url']}?status=failed", headers=_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == {
        "code": "INVALID_SOURCE_SCHEMA",
        "message": "source_schema contains duplicate label_id",
        "details": {
            "label_id": "65f0a1b2c3d4e5f6a7b8c901",
            "source_language": "zh",
            "target_languages": ["en", "es", "pt", "kr"],
        },
    }


def test_cpp_mock_rejects_rs_translation_job_type(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    client = TestClient(app)

    response = client.post(
        "/api/v1/mock/cpp/ai-jobs/jobs",
        headers=_headers(),
        json={
            "job_type": "short_drama.tag_schema.translation",
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
            "job_params": {},
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "INVALID_JOB_TYPE"
    assert body["error"]["details"]["supported_job_types"] == ["short_drama.tag_schema.translation"]


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
        "rs_tag_schema_translation"
    ]["value"]
    rs_response_example = rs_post["responses"]["202"]["content"]["application/json"]["example"]
    assert rs_request_example["job_type"] == "short_drama.tag_schema.translation"
    assert rs_request_example["job_params"][0]["label_id"] == "bihuihuigu76576585"
    assert rs_request_example["job_params"][1]["target_languages"] == ["en", "es", "kr"]
    assert rs_response_example["status_url"].startswith("/api/v1/mock/rs/ai-jobs/jobs/")

    cpp_get_example = schema["paths"]["/api/v1/mock/cpp/ai-jobs/jobs/{job_id}"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["example"]
    rs_get_example = schema["paths"]["/api/v1/mock/rs/ai-jobs/jobs/{job_id}"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["example"]
    assert cpp_get_example["metadata"]["mock_tagging"]["rs_write"]["label_count"] == 4
    assert rs_get_example["result"]["artifacts"][0]["content"][0]["categories"][0]["name"] == "Audience"
