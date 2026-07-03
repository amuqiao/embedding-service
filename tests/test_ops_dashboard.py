import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from starlette.testclient import TestClient


def _dashboard_settings(*, enabled=True, require_auth=False, timeout=2):
    return SimpleNamespace(
        ops_dashboard=SimpleNamespace(
            enabled=enabled,
            require_auth=require_auth,
            refresh_seconds=15,
            max_window_seconds=86_400,
            query_timeout_seconds=timeout,
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
    assert "/internal/jobs-dashboard/examples" in paths
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
    assert "mock_data_enabled" not in config.json()
    assert "data_source" not in config.json()


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


def test_ops_dashboard_static_dashboard_js_declares_chart_contract():
    contract = Path("app/ops_dashboard/static/chart_contract.js").read_text(encoding="utf-8")
    script = Path("app/ops_dashboard/static/dashboard.js").read_text(encoding="utf-8")
    page = Path("app/ops_dashboard/static/index.html").read_text(encoding="utf-8")

    assert "const CHART_TYPES = Object.freeze" in contract
    assert "const CHART_RENDERERS = Object.freeze" in contract
    assert "const PANEL_REGISTRY = Object.freeze" in script
    assert "const PANEL_DATA_ADAPTERS = Object.freeze" in script
    assert "Unknown chartType" in contract
    assert "/internal/jobs-dashboard/static/chart_contract.js" in page

    expected_chart_types = {"stat_card", "line", "stacked_bar", "horizontal_bar", "table"}
    renderer_body = re.search(r"const CHART_RENDERERS = Object\.freeze\(\{(?P<body>.*?)\n  \}\);", contract, re.S)
    assert renderer_body is not None
    renderer_types = set(re.findall(r"^\s{4}([a-z_]+):", renderer_body.group("body"), re.M))
    assert renderer_types == expected_chart_types

    panel_body = re.search(r"const PANEL_REGISTRY = Object\.freeze\(\{(?P<body>.*?)\n  \}\);", script, re.S)
    assert panel_body is not None
    panel_source = panel_body.group("body")
    panel_chart_types = set(re.findall(r'chartType:\s*"([^"]+)"', panel_source))
    assert panel_chart_types <= renderer_types

    assert panel_source.count('chartType: "stat_card"') == panel_source.count("cards:")
    assert panel_source.count('chartType: "line"') == panel_source.count("series:")
    assert panel_source.count('chartType: "table"') == panel_source.count("columns:")
    assert "rows:" not in panel_source

    for field in ["key:", "question:", "chartType:", "target:", "dataPath:", "series:", "columns:", "adapter:"]:
        assert field in script

    for target in re.findall(r'target:\s*"([^"]+)"', panel_source):
        assert f'id="{target}"' in page or f'id="{target}"' in script

    adapter_body = re.search(r"const PANEL_DATA_ADAPTERS = Object\.freeze\(\{(?P<body>.*?)\n  \}\);", script, re.S)
    assert adapter_body is not None
    adapters = set(re.findall(r"^\s{4}([a-z0-9_]+):", adapter_body.group("body"), re.M))
    panel_adapters = set(re.findall(r'adapter:\s*"([^"]+)"', panel_source))
    assert panel_adapters <= adapters


def test_ops_dashboard_examples_page_declares_generic_chart_fixtures(monkeypatch):
    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    application = FastAPI()
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        page = client.get("/internal/jobs-dashboard/examples")
        script = client.get("/internal/jobs-dashboard/static/examples.js")
        contract = client.get("/internal/jobs-dashboard/static/chart_contract.js")

    assert page.status_code == 200
    assert "Chart Contract Examples" in page.text
    assert script.status_code == 200
    assert contract.status_code == 200

    examples_html = Path("app/ops_dashboard/static/examples.html").read_text(encoding="utf-8")
    assert examples_html.index("chart_contract.js") < examples_html.index("examples.js")

    examples_script = Path("app/ops_dashboard/static/examples.js").read_text(encoding="utf-8")
    expected_chart_types = {"stat_card", "line", "stacked_bar", "horizontal_bar", "table"}
    example_chart_types = set(re.findall(r'chartType:\s*"([^"]+)"', examples_script))
    assert example_chart_types == expected_chart_types

    forbidden_business_terms = {"job_id", "attempt_id", "callback", "poster_title_image"}
    assert all(term not in examples_script for term in forbidden_business_terms)
    forbidden_live_data_calls = {"fetch(", "/sections/", "/jobs/"}
    assert all(term not in examples_script for term in forbidden_live_data_calls)


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


def test_ops_dashboard_failures_route_returns_read_model_payload(monkeypatch):
    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import read_model
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False)
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


def test_ops_dashboard_health_route_returns_read_model_payload(monkeypatch):
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
