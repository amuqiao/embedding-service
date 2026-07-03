import sys
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient


def _dashboard_settings(*, enabled=True, require_auth=False, timeout=2, mock_data=False):
    return SimpleNamespace(
        ops_dashboard=SimpleNamespace(
            enabled=enabled,
            require_auth=require_auth,
            refresh_seconds=15,
            max_window_seconds=86_400,
            query_timeout_seconds=timeout,
            mock_data_enabled=mock_data,
        ),
        job=SimpleNamespace(max_active_jobs=1000),
    )


def test_include_optional_ops_dashboard_disabled_does_not_import_router(monkeypatch):
    from app import main

    sys.modules.pop("app.ops_dashboard.router", None)
    monkeypatch.setattr(main, "settings", _dashboard_settings(enabled=False))

    application = FastAPI()
    main.include_optional_ops_dashboard(application)

    paths = {getattr(route, "path", "") for route in application.routes}
    assert "/internal/jobs-dashboard" not in paths
    assert "app.ops_dashboard.router" not in sys.modules


def test_include_optional_ops_dashboard_enabled_adds_internal_routes(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "settings", _dashboard_settings(enabled=True))

    application = FastAPI()
    main.include_optional_ops_dashboard(application)

    paths = {getattr(route, "path", "") for route in application.routes}
    assert "/internal/jobs-dashboard" in paths
    assert "/internal/jobs-dashboard/config" in paths
    assert "/internal/jobs-dashboard/health" in paths


@pytest.mark.asyncio
async def test_require_ops_access_allows_hidden_route_mode(monkeypatch):
    from app.ops_dashboard import router as ops_router

    monkeypatch.setattr(ops_router, "settings", _dashboard_settings(require_auth=False))

    assert await ops_router.require_ops_access(credentials=None, caller_id=None) == "ops-dashboard"


@pytest.mark.asyncio
async def test_require_ops_access_reuses_service_auth_by_default(monkeypatch):
    from app.core.exceptions import UnauthorizedError
    from app.ops_dashboard import router as ops_router

    monkeypatch.setattr(ops_router, "settings", _dashboard_settings(require_auth=True))

    with pytest.raises(UnauthorizedError):
        await ops_router.require_ops_access(credentials=None, caller_id=None)


def test_ops_dashboard_page_and_config_routes_work_without_auth(monkeypatch):
    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    application = FastAPI()
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        page = client.get("/internal/jobs-dashboard")
        config = client.get("/internal/jobs-dashboard/config")

    assert page.status_code == 200
    assert "Job 观测面板" in page.text
    assert config.status_code == 200
    assert config.json()["route_base"] == "/internal/jobs-dashboard"
    assert config.json()["mock_data_enabled"] is False
    assert config.json()["data_source"] == "live"


def test_ops_dashboard_routes_are_hidden_from_openapi_and_get_only(monkeypatch):
    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    application = FastAPI()
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        schema = client.get("/openapi.json")
        post_response = client.post("/internal/jobs-dashboard")

    assert "/internal/jobs-dashboard" not in schema.json()["paths"]
    assert post_response.status_code == 405


def test_ops_dashboard_static_file_rejects_traversal():
    from app.ops_dashboard import router as ops_router

    with pytest.raises(HTTPException) as exc_info:
        ops_router._static_file("../router.py")

    assert exc_info.value.status_code == 404


def test_ops_dashboard_overview_route_returns_read_model_payload(monkeypatch):
    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import read_model
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    async def fake_db():
        yield object()

    async def fake_overview_data(_db, filters, *, max_active_jobs):
        assert filters.window == "1h"
        assert max_active_jobs == 1000
        return {
            "generated_at": datetime(2026, 7, 3, tzinfo=UTC),
            "health": {"status": "ok", "reasons": [], "next_checks": []},
            "summary": {"jobs": {"failed": 0}},
        }

    monkeypatch.setattr(read_model, "overview_data", fake_overview_data)

    application = FastAPI()
    application.dependency_overrides[ops_router.get_dashboard_db] = fake_db
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        response = client.get("/internal/jobs-dashboard/sections/overview/data?window=1h&bucket=1m")

    assert response.status_code == 200
    assert response.json()["health"]["status"] == "ok"


def test_ops_dashboard_job_trace_returns_404_when_job_missing(monkeypatch):
    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import read_model
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    async def fake_db():
        yield object()

    async def fake_job_trace_data(_db, _job_id, *, limit):
        assert limit == 100
        return None

    monkeypatch.setattr(read_model, "job_trace_data", fake_job_trace_data)

    application = FastAPI()
    application.dependency_overrides[ops_router.get_dashboard_db] = fake_db
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        response = client.get(f"/internal/jobs-dashboard/jobs/{uuid.uuid4()}/data")

    assert response.status_code == 404


def test_ops_dashboard_failures_route_uses_live_read_model_when_mock_disabled(monkeypatch):
    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import read_model
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False, mock_data=False)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    async def fake_db():
        yield object()

    async def fake_failures_data(_db, filters):
        assert filters.window == "1h"
        return {
            "generated_at": datetime(2026, 7, 3, tzinfo=UTC),
            "failure_groups": [{"error_code": "LIVE_ONLY", "count": 1}],
            "failed_samples": [],
            "callbacks": [],
            "stuck": {"count": 0, "sample": []},
        }

    monkeypatch.setattr(read_model, "failures_data", fake_failures_data)

    application = FastAPI()
    application.dependency_overrides[ops_router.get_dashboard_db] = fake_db
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        response = client.get("/internal/jobs-dashboard/sections/failures/data?window=1h&bucket=1m")

    assert response.status_code == 200
    assert response.json()["failure_groups"][0]["error_code"] == "LIVE_ONLY"
    assert "mock_data" not in response.json()


def test_ops_dashboard_health_route_uses_live_read_model_when_mock_disabled(monkeypatch):
    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import read_model
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False, mock_data=False)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    async def fake_db():
        yield object()

    async def fake_overview_data(_db, filters, *, max_active_jobs):
        assert filters.window == "1h"
        assert max_active_jobs == 1000
        return {
            "generated_at": datetime(2026, 7, 3, tzinfo=UTC),
            "health": {"status": "ok", "reasons": ["live"], "next_checks": []},
            "summary": {"jobs": {"total": 1}},
        }

    monkeypatch.setattr(read_model, "overview_data", fake_overview_data)

    application = FastAPI()
    application.dependency_overrides[ops_router.get_dashboard_db] = fake_db
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        response = client.get("/internal/jobs-dashboard/health?window=1h&bucket=1m")

    assert response.status_code == 200
    assert response.json()["health"]["reasons"] == ["live"]
    assert "mock_data" not in response.json()


def test_ops_dashboard_rejects_invalid_filter(monkeypatch):
    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    async def fake_db():
        yield object()

    application = FastAPI()
    application.dependency_overrides[ops_router.get_dashboard_db] = fake_db
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        response = client.get("/internal/jobs-dashboard/sections/overview/data?window=7d")

    assert response.status_code == 400


def test_ops_dashboard_mock_data_routes_skip_db_dependency(monkeypatch):
    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False, mock_data=True)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    application = FastAPI()
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        config = client.get("/internal/jobs-dashboard/config")
        overview = client.get("/internal/jobs-dashboard/sections/overview/data")
        failures = client.get("/internal/jobs-dashboard/sections/failures/data")
        trace = client.get(f"/internal/jobs-dashboard/jobs/{uuid.uuid4()}/data")
        health = client.get("/internal/jobs-dashboard/health")

    assert config.status_code == 200
    assert config.json()["mock_data_enabled"] is True
    assert config.json()["data_source"] == "mock"
    assert overview.status_code == 200
    assert overview.json()["mock_data"] is True
    assert overview.json()["ingress"]
    assert failures.status_code == 200
    assert failures.json()["mock_data"] is True
    assert failures.json()["failure_groups"]
    assert trace.status_code == 200
    assert trace.json()["mock_data"] is True
    assert trace.json()["job"]["status"] == "failed"
    assert health.status_code == 200
    assert health.json()["mock_data"] is True


class _RowsResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _RecordingDB:
    def __init__(self):
        self.statements = []
        self.params = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        return _RowsResult()


@pytest.mark.asyncio
async def test_ops_dashboard_failure_groups_sql_uses_summarized_error_fields():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB()

    await read_model.failure_groups(db, DashboardFilters())

    sql = db.statements[0]
    assert "j.error->>'message'" not in sql
    assert "detail_message" not in sql
    assert "COALESCE(j.error->>'code'" in sql
    assert "GROUP BY 1, 2, 3, 4" in sql


@pytest.mark.asyncio
async def test_ops_dashboard_stuck_sql_does_not_return_raw_error_detail():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB()

    await read_model.stuck(db, DashboardFilters())

    sql = db.statements[0]
    assert "detail_code" in sql
    assert "a.error AS detail" not in sql
    assert "c.last_error AS detail" not in sql
    assert "d.last_error AS detail" not in sql
    assert "last_error AS detail" not in sql
