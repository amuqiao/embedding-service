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
    config_json = config.json()
    assert config_json["route_base"] == "/internal/jobs-dashboard"
    assert "mock_data_enabled" not in config_json
    assert "data_source" not in config_json
    data_sources = {source["key"]: source for source in config_json["data_sources"]}
    assert list(data_sources) == ["overview", "recent_jobs", "flow_capacity", "failures_callbacks", "job_trace"]
    assert data_sources["overview"]["route"] == "/internal/jobs-dashboard/sections/overview/data"
    assert data_sources["recent_jobs"]["route"] == "/internal/jobs-dashboard/sections/recent_jobs/data"
    assert data_sources["flow_capacity"]["route"] == "/internal/jobs-dashboard/sections/flow_capacity/data"
    assert data_sources["failures_callbacks"]["route"] == "/internal/jobs-dashboard/sections/failures_callbacks/data"
    assert data_sources["job_trace"]["route"] == "/internal/jobs-dashboard/jobs/{job_id}/data"
    assert data_sources["recent_jobs"]["controls"] == [
        {
            "key": "status",
            "type": "select",
            "binding": "query",
            "param": "status",
            "label": "status",
            "default": "all",
            "options": ["all", "queued", "running", "succeeded", "failed"],
            "min": None,
            "max": None,
        },
        {
            "key": "client_request_id",
            "type": "text",
            "binding": "query",
            "param": "client_request_id",
            "label": "client_request_id",
            "default": None,
            "options": [],
            "min": None,
            "max": None,
        },
        {
            "key": "limit",
            "type": "number",
            "binding": "query",
            "param": "limit",
            "label": "limit",
            "default": 20,
            "options": [],
            "min": 1,
            "max": 100,
        },
    ]
    assert data_sources["job_trace"]["controls"] == [
        {
            "key": "job_id",
            "type": "text",
            "binding": "route",
            "param": "job_id",
            "label": "job_id",
            "default": None,
            "options": [],
            "min": None,
            "max": None,
        },
        {
            "key": "limit",
            "type": "number",
            "binding": "query",
            "param": "limit",
            "label": "limit",
            "default": 100,
            "options": [],
            "min": 1,
            "max": 200,
        },
    ]
    assert config_json["data_sources"] == config_json["sections"]
    for source in config_json["data_sources"]:
        assert set(source) == {"key", "title", "route", "refresh_seconds", "default_enabled", "controls"}


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


def test_ops_dashboard_static_dashboard_js_declares_renderer_widget_layout_contract():
    contract = Path("app/ops_dashboard/static/chart_contract.js").read_text(encoding="utf-8")
    script = Path("app/ops_dashboard/static/dashboard.js").read_text(encoding="utf-8")
    page = Path("app/ops_dashboard/static/index.html").read_text(encoding="utf-8")

    assert "const RENDERER_TYPES = Object.freeze" in contract
    assert "const RENDERERS = Object.freeze" in contract
    assert "function renderWidgetLayout" in contract
    assert "function metricValue" in contract
    assert "const DATA_SOURCE_REGISTRY = Object.freeze" in script
    assert "const PAGE_CONTROL_REGISTRY = Object.freeze" in script
    assert "const WIDGET_REGISTRY = Object.freeze" in script
    assert "const LAYOUT_REGISTRY = Object.freeze" in script
    assert "const WIDGET_DATA_ADAPTERS = Object.freeze" in script
    assert "Unknown rendererType" in contract
    assert "/internal/jobs-dashboard/static/chart_contract.js" in page

    expected_renderer_types = {
        "status_line",
        "metric_cards",
        "echarts.line",
        "echarts.stacked_bar",
        "echarts.horizontal_bar",
        "html.table",
        "html.signal_list",
        "html.summary_table",
        "html.json_block",
    }
    renderer_body = re.search(r"const RENDERERS = Object\.freeze\(\{(?P<body>.*?)\n  \}\);", contract, re.S)
    assert renderer_body is not None
    renderer_matches = re.findall(r'^\s{4}(?:"([^"]+)"|([a-z_]+)):', renderer_body.group("body"), re.M)
    renderer_types = {quoted or bare for quoted, bare in renderer_matches}
    assert renderer_types == expected_renderer_types
    type_body = re.search(r"const RENDERER_TYPES = Object\.freeze\(\{(?P<body>.*?)\n  \}\);", contract, re.S)
    assert type_body is not None
    type_matches = re.findall(r'^\s{4}(?:"([^"]+)"|([a-z_]+)):', type_body.group("body"), re.M)
    assert {quoted or bare for quoted, bare in type_matches} == renderer_types

    control_source = script[
        script.index("const PAGE_CONTROL_REGISTRY = Object.freeze") : script.index("const WIDGET_REGISTRY = Object.freeze")
    ]
    widget_source = script[script.index("const WIDGET_REGISTRY = Object.freeze") : script.index("const LAYOUT_REGISTRY = Object.freeze")]
    layout_source = script[script.index("const LAYOUT_REGISTRY = Object.freeze") : script.index("const WIDGET_DATA_ADAPTERS = Object.freeze")]
    data_source_source = script[
        script.index("const DATA_SOURCE_REGISTRY = Object.freeze") : script.index("const WIDGET_REGISTRY = Object.freeze")
    ]

    widget_renderer_types = set(re.findall(r'rendererType:\s*"([^"]+)"', widget_source))
    assert widget_renderer_types <= renderer_types
    assert {"echarts.line", "echarts.horizontal_bar", "html.table", "status_line"} <= widget_renderer_types

    data_source_keys = set(re.findall(r"^\s{4}([a-z_]+):", data_source_source, re.M))
    assert data_source_keys == {"overview", "recent_jobs", "flow_capacity", "failures_callbacks", "job_trace"}
    assert 'route: `${BASE}/sections/overview/data`' in data_source_source
    assert 'route: `${BASE}/sections/recent_jobs/data`' in data_source_source
    assert 'route: `${BASE}/sections/flow_capacity/data`' in data_source_source
    assert 'route: `${BASE}/sections/failures_callbacks/data`' in data_source_source
    assert 'route: `${BASE}/jobs/{job_id}/data`' in data_source_source
    assert "usesJobId" not in data_source_source
    assert "job_search" not in script
    widget_data_sources = set(re.findall(r'dataSource:\s*"([^"]+)"', widget_source))
    assert widget_data_sources <= data_source_keys
    assert "recent_jobs:" in control_source
    assert "job_trace:" in control_source
    assert 'binding: "route"' in control_source
    assert 'binding: "query"' in control_source
    for snippet in [
        'key: "status"',
        'type: "select"',
        'default: "all"',
        'options: ["all", "queued", "running", "succeeded", "failed"]',
        'key: "client_request_id"',
        'key: "limit"',
        "default: 20",
        "max: 100",
        'key: "job_id"',
        "default: 100",
        "max: 200",
    ]:
        assert snippet in control_source
    assert "PAGE_CONTROL_REGISTRY[section]" in script
    assert "route.replace(`{${control.param}}`" in script
    assert "configured?.refresh_seconds ?? state.config?.refresh_seconds ?? 15" in script
    assert '"recent_jobs.table"' in widget_source
    assert 'dataPath: "jobs"' in widget_source
    assert 'getPath(payload, "summary.jobs.success_rate")' in widget_source
    for widget_id in [
        '"flow_capacity.capacity_cards"',
        '"flow_capacity.drain_cards"',
        '"flow_capacity.ingress_drain"',
        '"flow_capacity.status_composition"',
        '"flow_capacity.latency_p95"',
        '"flow_capacity.job_type_hotspots"',
        '"flow_capacity.next_checks"',
    ]:
        assert widget_id in widget_source
    assert 'dataPath: "status_composition"' in widget_source
    assert 'dataPath: "job_type_hotspots"' in widget_source
    assert 'valuePath: "drain.status"' in widget_source
    assert 'valuePath: "drain.stuck.total"' in widget_source
    for widget_id in [
        '"failures_callbacks.summary_cards"',
        '"failures_callbacks.failure_groups_rank"',
        '"failures_callbacks.failure_groups_table"',
        '"failures_callbacks.failed_samples"',
        '"failures_callbacks.callback_outbox"',
        '"failures_callbacks.callback_composition"',
        '"failures_callbacks.callback_samples"',
        '"failures_callbacks.next_checks"',
    ]:
        assert widget_id in widget_source
    assert 'adapter: "callback_composition_rows"' in widget_source
    assert 'dataPath: "callback_samples"' in widget_source
    assert 'valuePath: "callback_summary.due"' in widget_source

    widget_keys = set(re.findall(r'^\s{4}"([^"]+)":\s*\{', widget_source, re.M))
    layout_widget_id_list = re.findall(r'widgetId:\s*"([^"]+)"', layout_source)
    layout_widget_ids = set(layout_widget_id_list)
    assert len(layout_widget_id_list) == len(layout_widget_ids)
    assert layout_widget_ids == widget_keys
    assert "target:" not in widget_source
    layout_data_sources = set(re.findall(r'dataSource:\s*"([^"]+)"', layout_source))
    assert layout_data_sources == data_source_keys
    for widget_id in layout_widget_ids:
        assert widget_id.split(".", 1)[0] in layout_data_sources
    declared_groups = set(re.findall(r'key:\s*"([^"]+)"', layout_source))
    placement_groups = set(re.findall(r'group:\s*"([^"]+)"', layout_source))
    assert placement_groups <= declared_groups

    for field in ["rendererType:", "dataSource:", "dataPath:", "series:", "columns:", "adapter:", "groups:", "placements:"]:
        assert field in script

    for target in re.findall(r'target:\s*"([^"]+)"', layout_source):
        assert (
            f'id="{target}"' in page
            or f'id="{target}"' in script
            or 'id="${escapeHtml(layout.target)}"' in script
        )

    adapter_body = re.search(r"const WIDGET_DATA_ADAPTERS = Object\.freeze\(\{(?P<body>.*?)\n  \}\);", script, re.S)
    assert adapter_body is not None
    adapters = set(re.findall(r"^\s{4}([a-z0-9_]+):", adapter_body.group("body"), re.M))
    widget_adapters = set(re.findall(r'adapter:\s*"([^"]+)"', widget_source))
    assert widget_adapters <= adapters


def test_ops_dashboard_examples_page_declares_generic_renderer_fixtures(monkeypatch):
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
    assert "Renderer Contract Examples" in page.text
    assert script.status_code == 200
    assert contract.status_code == 200

    examples_html = Path("app/ops_dashboard/static/examples.html").read_text(encoding="utf-8")
    assert examples_html.index("chart_contract.js") < examples_html.index("examples.js")

    examples_script = Path("app/ops_dashboard/static/examples.js").read_text(encoding="utf-8")
    expected_renderer_types = {
        "status_line",
        "metric_cards",
        "echarts.line",
        "echarts.stacked_bar",
        "echarts.horizontal_bar",
        "html.table",
        "html.signal_list",
        "html.summary_table",
        "html.json_block",
    }
    example_renderer_types = set(re.findall(r'rendererType:\s*"([^"]+)"', examples_script))
    assert example_renderer_types == expected_renderer_types
    example_widget_keys = set(re.findall(r'^\s{4}"([^"]+)":\s*\{', examples_script, re.M))
    example_layout_widget_ids = set(re.findall(r'widgetId:\s*"([^"]+)"', examples_script))
    assert example_layout_widget_ids == example_widget_keys
    assert "dataSource:" not in examples_script
    assert "adapter:" not in examples_script
    assert "renderWidgetLayout(EXAMPLE_LAYOUT_REGISTRY.examples, EXAMPLE_WIDGET_REGISTRY, EXAMPLE_PAYLOAD)" in examples_script

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
        assert filters.sample_limit == 20
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
        response = client.get("/internal/jobs-dashboard/sections/overview/data?window=1h&bucket=1m&limit=7")

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


def test_ops_dashboard_job_trace_route_returns_read_model_payload(monkeypatch):
    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import read_model
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)
    job_id = uuid.uuid4()

    async def fake_db():
        yield object()

    async def fake_job_trace_data(_db, requested_job_id, *, limit):
        assert requested_job_id == job_id
        assert limit == 7
        return {
            "generated_at": datetime(2026, 7, 3, tzinfo=UTC),
            "job": {"job_id": str(requested_job_id), "status": "succeeded"},
            "attempts": [],
            "ai_calls": [],
            "workflow_children": [],
            "timeline": [],
            "callbacks": [],
        }

    monkeypatch.setattr(read_model, "job_trace_data", fake_job_trace_data)

    application = FastAPI()
    application.dependency_overrides[ops_router.get_dashboard_db] = fake_db
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        response = client.get(f"/internal/jobs-dashboard/jobs/{job_id}/data?limit=7")

    assert response.status_code == 200
    assert response.json()["job"]["job_id"] == str(job_id)
    assert response.json()["job"]["status"] == "succeeded"


def test_ops_dashboard_recent_jobs_route_returns_read_model_payload(monkeypatch):
    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import read_model
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    async def fake_db():
        yield object()

    async def fake_recent_jobs_data(_db, filters, *, status, client_request_id, limit):
        assert filters.window == "1h"
        assert filters.bucket == "1m"
        assert status == "failed"
        assert client_request_id == "req-1"
        assert limit == 7
        return {
            "generated_at": datetime(2026, 7, 3, tzinfo=UTC),
            "filters": filters.__dict__,
            "controls": {"status": status, "client_request_id": client_request_id, "limit": limit},
            "summary": {"total": 1, "failed": 1},
            "jobs": [{"job_id": "job-1", "status": "failed"}],
            "health": {"status": "ok", "reasons": [], "next_checks": []},
        }

    monkeypatch.setattr(read_model, "recent_jobs_data", fake_recent_jobs_data)

    application = FastAPI()
    application.dependency_overrides[ops_router.get_dashboard_db] = fake_db
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        response = client.get(
            "/internal/jobs-dashboard/sections/recent_jobs/data"
            "?window=1h&bucket=1m&status=failed&client_request_id=req-1&limit=7"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {"total": 1, "failed": 1}
    assert payload["jobs"] == [{"job_id": "job-1", "status": "failed"}]
    assert payload["controls"] == {"status": "failed", "client_request_id": "req-1", "limit": 7}


def test_ops_dashboard_recent_jobs_rejects_invalid_status(monkeypatch):
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
        response = client.get("/internal/jobs-dashboard/sections/recent_jobs/data?status=stuck")

    assert response.status_code == 400
    assert "status must be one of" in response.json()["detail"]


def test_ops_dashboard_recent_jobs_route_returns_504_on_timeout(monkeypatch):
    import asyncio

    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import read_model
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False, timeout=0.001)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    async def fake_db():
        yield object()

    async def slow_recent_jobs_data(_db, _filters, *, status, client_request_id, limit):
        await asyncio.sleep(0.01)
        return {
            "generated_at": datetime(2026, 7, 3, tzinfo=UTC),
            "controls": {"status": status, "client_request_id": client_request_id, "limit": limit},
            "summary": {},
            "jobs": [],
        }

    monkeypatch.setattr(read_model, "recent_jobs_data", slow_recent_jobs_data)

    application = FastAPI()
    application.dependency_overrides[ops_router.get_dashboard_db] = fake_db
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        response = client.get("/internal/jobs-dashboard/sections/recent_jobs/data")

    assert response.status_code == 504
    assert response.json()["detail"] == "ops dashboard query timed out"


def test_ops_dashboard_flow_capacity_route_returns_read_model_payload(monkeypatch):
    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import read_model
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    async def fake_db():
        yield object()

    async def fake_flow_capacity_data(_db, filters, *, max_active_jobs):
        assert filters.window == "1h"
        assert filters.bucket == "1m"
        assert max_active_jobs == 1000
        return {
            "generated_at": datetime(2026, 7, 3, tzinfo=UTC),
            "filters": filters.__dict__,
            "capacity": {"current": {"active_jobs": 1, "headroom": 999}},
            "ingress": [{"bucket_at": datetime(2026, 7, 3, tzinfo=UTC), "created": 1}],
            "status_composition": [],
            "latency": [],
            "job_type_hotspots": [],
            "health": {"status": "ok", "reasons": [], "next_checks": []},
        }

    monkeypatch.setattr(read_model, "flow_capacity_data", fake_flow_capacity_data)

    application = FastAPI()
    application.dependency_overrides[ops_router.get_dashboard_db] = fake_db
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        response = client.get("/internal/jobs-dashboard/sections/flow_capacity/data?window=1h&bucket=1m")

    assert response.status_code == 200
    payload = response.json()
    assert payload["capacity"]["current"]["active_jobs"] == 1
    assert payload["capacity"]["current"]["headroom"] == 999


def test_ops_dashboard_flow_capacity_route_returns_504_on_timeout(monkeypatch):
    import asyncio

    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import read_model
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False, timeout=0.001)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    async def fake_db():
        yield object()

    async def slow_flow_capacity_data(_db, _filters, *, max_active_jobs):
        await asyncio.sleep(0.01)
        return {
            "generated_at": datetime(2026, 7, 3, tzinfo=UTC),
            "capacity": {"current": {"max_active_jobs": max_active_jobs}},
            "ingress": [],
            "status_composition": [],
            "latency": [],
            "job_type_hotspots": [],
            "health": {"status": "ok", "reasons": [], "next_checks": []},
        }

    monkeypatch.setattr(read_model, "flow_capacity_data", slow_flow_capacity_data)

    application = FastAPI()
    application.dependency_overrides[ops_router.get_dashboard_db] = fake_db
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        response = client.get("/internal/jobs-dashboard/sections/flow_capacity/data")

    assert response.status_code == 504
    assert response.json()["detail"] == "ops dashboard query timed out"


def test_ops_dashboard_failures_callbacks_route_returns_read_model_payload(monkeypatch):
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
        assert filters.sample_limit == 20
        return {
            "generated_at": datetime(2026, 7, 3, tzinfo=UTC),
            "health": {"status": "warning", "reasons": ["failed_jobs"], "next_checks": []},
            "failure_summary": {"failed_records": 1, "failed_roots": 1},
            "failure_groups": [{"error_code": "LIVE_ONLY", "count": 1}],
            "failed_samples": [],
            "callback_summary": {"due": 0, "delivered": 0, "dead_letter": 0},
            "callbacks": [],
            "callback_samples": [],
            "stuck": {"total": 0, "count": 0, "sample": []},
        }

    monkeypatch.setattr(read_model, "failures_data", fake_failures_data)

    application = FastAPI()
    application.dependency_overrides[ops_router.get_dashboard_db] = fake_db
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        response = client.get(
            "/internal/jobs-dashboard/sections/failures_callbacks/data?window=1h&bucket=1m&limit=7"
        )

    assert response.status_code == 200
    assert response.json()["failure_groups"][0]["error_code"] == "LIVE_ONLY"
    assert response.json()["health"]["status"] == "warning"


def test_ops_dashboard_failures_callbacks_route_returns_504_on_timeout(monkeypatch):
    import asyncio

    from app.ops_dashboard import config as ops_config
    from app.ops_dashboard import read_model
    from app.ops_dashboard import router as ops_router

    settings = _dashboard_settings(require_auth=False, timeout=0.001)
    monkeypatch.setattr(ops_router, "settings", settings)
    monkeypatch.setattr(ops_config, "settings", settings)

    async def fake_db():
        yield object()

    async def slow_failures_data(_db, _filters):
        await asyncio.sleep(0.01)
        return {
            "generated_at": datetime(2026, 7, 3, tzinfo=UTC),
            "health": {"status": "ok", "reasons": [], "next_checks": []},
            "failure_groups": [],
            "failed_samples": [],
            "callbacks": [],
            "callback_samples": [],
        }

    monkeypatch.setattr(read_model, "failures_data", slow_failures_data)

    application = FastAPI()
    application.dependency_overrides[ops_router.get_dashboard_db] = fake_db
    application.include_router(ops_router.router)

    with TestClient(application) as client:
        response = client.get("/internal/jobs-dashboard/sections/failures_callbacks/data")

    assert response.status_code == 504
    assert response.json()["detail"] == "ops dashboard query timed out"


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
    def __init__(self, rows_by_call=None):
        self.statement_objects = []
        self.statements = []
        self.params = []
        self.rows_by_call = list(rows_by_call or [])

    async def execute(self, statement, params=None):
        self.statement_objects.append(statement)
        self.statements.append(str(statement))
        self.params.append(params or {})
        rows = self.rows_by_call.pop(0) if self.rows_by_call else []
        return _RowsResult(rows)


def _bind_type_name(statement, key):
    bind = statement._bindparams[key]  # noqa: SLF001 - test verifies SQLAlchemy text bind contract.
    return bind.type.__class__.__name__


def test_ops_dashboard_typed_text_only_binds_existing_filter_params():
    from app.ops_dashboard import read_model

    no_filter = read_model._typed_text("select 1")  # noqa: SLF001 - test locks internal read-model helper.
    partial = read_model._typed_text("select :job_type")  # noqa: SLF001

    assert no_filter._bindparams == {}  # noqa: SLF001
    assert sorted(partial._bindparams) == ["job_type"]  # noqa: SLF001
    assert _bind_type_name(partial, "job_type") == "String"


def test_ops_dashboard_optional_filter_binds_are_all_typed():
    from app.ops_dashboard import read_model

    source = Path(read_model.__file__).read_text(encoding="utf-8")
    optional_params = set(re.findall(r":([a-z_]+)\s+IS\s+NULL", source))

    assert optional_params
    assert optional_params <= set(read_model.OPTIONAL_FILTER_BIND_TYPES)


@pytest.mark.asyncio
async def test_ops_dashboard_read_model_binds_optional_filter_types_for_asyncpg():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB()

    await read_model.summary(db, DashboardFilters())
    await read_model.ingress(db, DashboardFilters())

    for statement in db.statement_objects:
        assert _bind_type_name(statement, "job_type") == "String"
        assert _bind_type_name(statement, "caller_id") == "String"
        assert _bind_type_name(statement, "since_at") == "DateTime"

    combined_sql = "\n".join(db.statements)
    assert ":job_type IS NULL" in combined_sql
    assert ":caller_id IS NULL" in combined_sql
    assert ":since_at IS NULL" in combined_sql


@pytest.mark.asyncio
async def test_ops_dashboard_recent_jobs_sql_matches_public_root_list_scope():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB()

    await read_model.recent_jobs(
        db,
        DashboardFilters(caller_id="caller", job_type="job_type"),
        status="failed",
        client_request_id="req-1",
        limit=7,
    )

    sql = db.statements[0]
    params = db.params[0]
    assert "j.root_job_id IS NULL" in sql
    assert "j.workflow_node_key IS NULL" in sql
    assert "j.client_request_id IS NOT NULL" in sql
    assert "j.deleted_at IS NULL" in sql
    assert "(:status IS NULL OR j.status = :status)" in sql
    assert "(:client_request_id IS NULL OR j.client_request_id = :client_request_id)" in sql
    assert "LIMIT :limit" in sql
    assert params["status"] == "failed"
    assert params["client_request_id"] == "req-1"
    assert params["limit"] == 7
    assert _bind_type_name(db.statement_objects[0], "status") == "String"
    assert _bind_type_name(db.statement_objects[0], "client_request_id") == "String"


@pytest.mark.asyncio
async def test_ops_dashboard_recent_jobs_data_all_status_uses_unfiltered_root_scope():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB()

    payload = await read_model.recent_jobs_data(
        db,
        DashboardFilters(),
        status="all",
        client_request_id=None,
        limit=20,
    )

    assert payload["controls"] == {"status": "all", "client_request_id": None, "limit": 20}
    assert len(db.statements) == 2
    for statement, params in zip(db.statements, db.params, strict=True):
        assert "j.root_job_id IS NULL" in statement
        assert "j.workflow_node_key IS NULL" in statement
        assert "j.client_request_id IS NOT NULL" in statement
        assert "(:status IS NULL OR j.status = :status)" in statement
        assert params["status"] is None
        assert params["client_request_id"] is None


@pytest.mark.asyncio
async def test_ops_dashboard_latency_sql_exposes_success_rate():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB()

    await read_model.latency(db, DashboardFilters())

    sql = db.statements[0]
    assert "AS success_rate" in sql
    assert "j.status = 'succeeded'" in sql
    assert "j.finished_at IS NOT NULL" in sql


@pytest.mark.asyncio
async def test_ops_dashboard_global_gate_preserves_negative_headroom():
    from app.ops_dashboard import read_model

    db = _RecordingDB(
        rows_by_call=[
            [
                {
                    "active_jobs": 1005,
                    "queued": 900,
                    "running_active": 105,
                }
            ]
        ]
    )

    payload = await read_model.global_gate(db, max_active_jobs=1000)

    assert payload["active_jobs"] == 1005
    assert payload["headroom"] == -5
    assert payload["active_ratio"] == 1.005


@pytest.mark.asyncio
async def test_ops_dashboard_flow_capacity_data_runs_expected_read_model_queries():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB()

    payload = await read_model.flow_capacity_data(
        db,
        DashboardFilters(window="24h", bucket="5m", caller_id="caller-a", job_type="job_echo"),
        max_active_jobs=1000,
    )

    assert payload["capacity"]["current"]["max_active_jobs"] == 1000
    assert "capacity" in payload
    assert "drain" in payload
    assert "ingress" in payload
    assert "status_composition" in payload
    assert "latency" in payload
    assert "job_type_hotspots" in payload
    assert "broker" not in payload
    assert "runtime" not in payload
    assert "db_connection_budget" not in payload
    assert payload["health"]["next_checks"] == [
        "./scripts/jobs.sh capacity --since 24h --job-type job_echo --caller-id caller-a",
        "./scripts/jobs.sh ingress --since 24h --bucket 5m --job-type job_echo --caller-id caller-a",
        "./scripts/jobs.sh drain --since 24h --older-than 10m --job-type job_echo --caller-id caller-a",
        "./scripts/jobs.sh latency --since 24h --group-by job_type --job-type job_echo --caller-id caller-a",
        "./scripts/jobs.sh broker",
        "./scripts/jobs.sh runtime",
    ]
    assert payload["query_scopes"] == {
        "capacity.current": "global_gate current active; ignores window/job_type/caller_id",
        "capacity.window": "root scope created_at window; applies job_type/caller_id",
        "drain.current": "family scope current active; applies root job_type/caller_id, ignores window",
        "drain.window": "family scope created_at window; applies root job_type/caller_id",
        "drain.stuck": "family scope stuck total/sample/truncated; applies root created_at window and root job_type/caller_id",
        "ingress": "root event-time buckets for created/started/finished events; applies job_type/caller_id",
        "status_composition": "dashboard root created_at buckets; applies job_type/caller_id",
        "latency": "root scope created_at window; applies job_type/caller_id",
        "job_type_hotspots": "root scope created_at window; applies job_type/caller_id; grouped by job_type",
    }
    combined_sql = "\n".join(db.statements)
    assert "accepted_submit_rps" not in combined_sql
    assert "GROUP BY bucket_at" in combined_sql
    assert "GROUP BY j.job_type" in combined_sql
    assert "lifecycle_p95_seconds" in combined_sql


@pytest.mark.asyncio
async def test_ops_dashboard_drain_status_sql_uses_family_scope_and_drained_verdict():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB()

    payload = await read_model.drain_status(db, DashboardFilters())

    assert payload["status"] == "drained"
    combined_sql = "\n".join(db.statements)
    assert "FROM job_aggregates root" in combined_sql
    assert "(j.id = root.id OR j.root_job_id = root.id)" in combined_sql
    assert "AS running_inactive" in combined_sql
    assert "AS active_jobs" in combined_sql
    assert "count(*) FILTER (WHERE j.status = 'failed') AS failed" in combined_sql


@pytest.mark.asyncio
async def test_ops_dashboard_drain_status_uses_stuck_total_not_sample_count():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    stuck_sample = [
        {
            "issue": "published_dispatch_not_claimed",
            "job_id": str(uuid.uuid4()),
            "job_status": "queued",
        }
        for _ in range(20)
    ]
    db = _RecordingDB(
        rows_by_call=[
            [
                {
                    "queued": 0,
                    "running": 0,
                    "running_active": 0,
                    "running_inactive": 0,
                    "active_jobs": 0,
                }
            ],
            [
                {
                    "total": 0,
                    "queued": 0,
                    "running": 0,
                    "running_active": 0,
                    "running_inactive": 0,
                    "active_jobs": 0,
                    "succeeded": 0,
                    "failed": 0,
                }
            ],
            [{"total": 25}],
            stuck_sample,
        ]
    )

    payload = await read_model.drain_status(db, DashboardFilters())

    assert payload["status"] == "not_drained"
    assert payload["stuck"]["total"] == 25
    assert payload["stuck"]["count"] == 25
    assert payload["stuck"]["truncated"] is True
    assert len(payload["stuck"]["sample"]) == 20


@pytest.mark.asyncio
async def test_ops_dashboard_status_composition_sql_uses_root_created_buckets():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB()

    await read_model.status_composition(db, DashboardFilters())

    sql = db.statements[0]
    assert "j.root_job_id IS NULL" in sql
    assert "j.workflow_node_key IS NULL" in sql
    assert "j.client_request_id IS NOT NULL" in sql
    assert "j.created_at >= :since_at" in sql
    assert "floor(EXTRACT(EPOCH FROM j.created_at) / :bucket_seconds)" in sql
    assert "count(*) FILTER (WHERE j.status = 'queued') AS queued" in sql
    assert "count(*) FILTER (WHERE j.status = 'running') AS running" in sql
    assert "count(*) FILTER (WHERE j.status = 'succeeded') AS succeeded" in sql
    assert "count(*) FILTER (WHERE j.status = 'failed') AS failed" in sql


@pytest.mark.asyncio
async def test_ops_dashboard_job_type_hotspots_sql_uses_root_scope_and_lifecycle_p95():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB()

    await read_model.job_type_hotspots(db, DashboardFilters())

    sql = db.statements[0]
    assert "j.root_job_id IS NULL" in sql
    assert "j.workflow_node_key IS NULL" in sql
    assert "j.client_request_id IS NOT NULL" in sql
    assert "GROUP BY j.job_type" in sql
    assert "AS active_jobs" in sql
    assert "AS queue_wait_p95_seconds" in sql
    assert "AS run_p95_seconds" in sql
    assert "AS lifecycle_p95_seconds" in sql
    assert "LIMIT :limit" in sql


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
async def test_ops_dashboard_failed_samples_sql_uses_family_scope_and_trace_fields():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB()

    await read_model.failed_samples(db, DashboardFilters())

    sql = db.statements[0]
    assert "FROM job_aggregates root" in sql
    assert "(j.id = root.id OR j.root_job_id = root.id)" in sql
    assert "CASE WHEN j.root_job_id IS NULL THEN 'root' ELSE 'child' END AS record_scope" in sql
    assert "j.root_job_id::text AS root_job_id" in sql
    assert "j.workflow_node_key" in sql
    assert "AS callback_status" in sql
    assert "a.status AS attempt_status" in sql
    assert "d.status AS dispatch_status" in sql
    assert "duration_seconds" in sql
    assert "LEFT JOIN job_aggregates root_job" not in sql
    assert "WHERE c.job_id = j.id" in sql
    assert "j.error->>'message'" not in sql


@pytest.mark.asyncio
async def test_ops_dashboard_failed_samples_preserves_zero_duration_seconds():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB(rows_by_call=[[{"duration_seconds": 0, "age_seconds": 12}]])

    rows = await read_model.failed_samples(db, DashboardFilters())

    assert rows[0]["duration_or_age_seconds"] == 0


@pytest.mark.asyncio
async def test_ops_dashboard_callbacks_summary_sql_uses_cli_order_and_sanitized_error_code():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB()

    await read_model.callbacks_summary(db, DashboardFilters())

    sql = db.statements[0]
    assert "max(c.last_error->>'code')" in sql
    assert "AS sample_last_error_code" in sql
    assert "AS oldest_age_seconds" in sql
    assert "WHEN 'pending' THEN 1" in sql
    assert "WHEN 'dead_letter' THEN 6" in sql
    assert "last_error::text" not in sql
    assert "last_error AS" not in sql
    assert "c.last_response" not in sql


@pytest.mark.asyncio
async def test_ops_dashboard_callback_samples_sql_returns_due_and_dead_letter_without_raw_payload():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB()

    await read_model.callback_samples(db, DashboardFilters())

    sql = db.statements[0]
    assert "c.status IN ('leased', 'dead_letter')" in sql
    assert "c.status IN ('pending', 'failed', 'retrying')" in sql
    assert "c.next_attempt_at <= now()" in sql
    assert "COALESCE(c.last_error->>'code', '-') AS last_error_code" in sql
    assert "c.last_response" not in sql
    assert "c.last_error AS" not in sql
    assert "c.payload" not in sql


@pytest.mark.asyncio
async def test_ops_dashboard_failures_data_exposes_health_next_checks_and_query_scopes():
    from app.ops_dashboard import read_model
    from app.ops_dashboard.schemas import DashboardFilters

    db = _RecordingDB(
        rows_by_call=[
            [{"status": "dead_letter", "count": 2, "due": 0}],
            [{"failed_records": 3, "failed_roots": 2}],
        ]
    )

    payload = await read_model.failures_data(
        db,
        DashboardFilters(window="24h", caller_id="caller-a", job_type="job_echo"),
    )

    assert payload["health"]["status"] == "critical"
    assert payload["health"]["reasons"] == ["callback_dead_letter", "failed_jobs"]
    assert payload["health"]["next_checks"] == [
        "./scripts/jobs.sh failures --since 24h --job-type job_echo --caller-id caller-a",
        "./scripts/jobs.sh callbacks-summary --since 24h --job-type job_echo --caller-id caller-a",
        "./scripts/jobs.sh list --status failed --scope family --since 24h --job-type job_echo --caller-id caller-a --limit 20",
    ]
    assert payload["callback_summary"]["dead_letter"] == 2
    assert payload["callback_samples"] == []
    assert payload["query_scopes"]["callbacks"] == "root scope callback_outbox grouped by status"


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
