import json

import pytest
from typer.testing import CliRunner

from scripts.real_flow.cli import app
from scripts.real_flow.flows import llm_job_billing


runner = CliRunner()


def test_real_flow_cli_requires_confirm_cost():
    result = runner.invoke(app, ["llm-job-billing"])

    assert result.exit_code == 2
    assert "real LLM flow requires --confirm-cost" in result.stderr


def test_real_flow_builds_job_payload_for_real_llm_job():
    payload = llm_job_billing.build_job_payload(
        model_id="gpt-5.4-mini",
        input_text="hello",
        instruction="reply once",
        client_request_id="client-1",
    )

    assert payload["client_request_id"] == "client-1"
    assert payload["job_type"] == "job_real_llm_echo"
    assert payload["job_params"] == {
        "model_id": "gpt-5.4-mini",
        "instruction": "reply once",
        "source": {"inline": {"text": "hello"}},
    }


def test_real_flow_builds_double_job_payload():
    payload = llm_job_billing.build_double_job_payload(
        model_id="gpt-5.4-mini",
        input_text="hello",
        first_instruction="first",
        second_instruction="second",
        client_request_id="client-2",
    )

    assert payload["client_request_id"] == "client-2"
    assert payload["job_type"] == "job_real_llm_double_echo"
    assert payload["job_params"] == {
        "model_id": "gpt-5.4-mini",
        "first_instruction": "first",
        "second_instruction": "second",
        "source": {"inline": {"text": "hello"}},
    }


def test_real_flow_headers_require_service_key_when_auth_enabled(monkeypatch):
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    with pytest.raises(llm_job_billing.FlowError) as exc:
        llm_job_billing.build_headers({}, caller_id="caller-1")

    assert exc.value.exit_code == 2
    assert "SERVICE_API_KEY is required" in str(exc.value)


def test_real_flow_headers_use_auth_and_caller_id(monkeypatch):
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    headers = llm_job_billing.build_headers(
        {
            "SERVICE_API_KEY": "secret",
            "DISABLE_HTTP_AUTH_HEADER": "false",
            "DISABLE_CALLER_ID_HEADER": "false",
        },
        caller_id="caller-1",
    )

    assert headers["Authorization"] == "Bearer secret"
    assert headers["X-AI-Service-Caller-ID"] == "caller-1"


def test_real_flow_resolves_api_url_from_script_env():
    api_url = llm_job_billing.resolved_api_url(None, {}, {"API_HOST": "127.0.0.1", "API_PORT": "18200"})

    assert api_url == "http://127.0.0.1:18200"


def test_real_flow_rejects_non_local_api_url():
    with pytest.raises(llm_job_billing.FlowError) as exc:
        llm_job_billing.resolved_api_url("https://api.example.com", {}, {})

    assert exc.value.exit_code == 2
    assert "only targets local API URLs" in str(exc.value)


@pytest.mark.parametrize("api_url", ["https://127.example.com", "https://127.0.0.1.nip.io"])
def test_real_flow_rejects_loopback_prefix_hostnames(api_url):
    with pytest.raises(llm_job_billing.FlowError) as exc:
        llm_job_billing.resolved_api_url(api_url, {}, {})

    assert exc.value.exit_code == 2
    assert "only targets local API URLs" in str(exc.value)


def test_real_flow_accepts_loopback_ip_url():
    api_url = llm_job_billing.resolved_api_url("http://127.0.0.1:18200", {}, {})

    assert api_url == "http://127.0.0.1:18200"


def test_real_flow_run_uses_http_job_and_billing_flow(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_MODEL_ID", raising=False)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    app_env = tmp_path / ".env"
    scripts_env = tmp_path / "scripts.env"
    app_env.write_text(
        "DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=false\nDEFAULT_MODEL_ID=gpt-5.4-mini\n",
        encoding="utf-8",
    )
    scripts_env.write_text("API_HOST=127.0.0.1\nAPI_PORT=18200\n", encoding="utf-8")
    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
    (tmp_path / "scripts").mkdir()
    scripts_env.rename(tmp_path / "scripts/.env")

    calls = []

    def fake_request_json(url, *, method, headers, payload=None, timeout_seconds=10):
        calls.append({"url": url, "method": method, "headers": headers, "payload": payload})
        if method == "POST":
            return {"code": "0", "data": {"job": {"job_id": "job-1", "job_status": "queued"}}}
        if url.endswith("/billing"):
            return {
                "code": "0",
                "data": {
                    "billing": {
                        "status": "estimated",
                        "currency": "USD",
                        "total_cost_amount": "0.00000100",
                        "usage_units": {"input_tokens": 1, "output_tokens": 1},
                        "pricing_refs": ["openai:gpt-5.4-mini@2026-06-23"],
                        "ai_call_count": 1,
                        "billable_call_count": 1,
                        "failed_call_count": 0,
                        "diagnostic_reason": None,
                        "finalized_at": "2026-06-23T00:00:00Z",
                    }
                },
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    def fake_poll_job_envelope(**kwargs):
        assert kwargs["jobs_url"] == "http://127.0.0.1:18200/api/v1/ai-jobs/jobs"
        return {
            "code": "0",
            "data": {"job": {"job_id": "job-1", "job_status": "succeeded", "job_type": "job_real_llm_echo"}},
        }

    monkeypatch.setattr(llm_job_billing, "request_json", fake_request_json)
    monkeypatch.setattr(llm_job_billing, "poll_job_envelope", fake_poll_job_envelope)

    llm_job_billing.run(
        confirm_cost=True,
        job_type="job_real_llm_echo",
        api_url=None,
        model_id=None,
        input_text="hello",
        instruction="reply once",
        second_instruction=None,
        caller_id="caller-1",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        client_request_id="client-1",
        json_output=True,
    )

    assert calls[0]["method"] == "POST"
    assert calls[0]["payload"]["job_type"] == "job_real_llm_echo"
    assert calls[0]["payload"]["job_params"]["model_id"] == "gpt-5.4-mini"
    assert calls[1]["method"] == "GET"
    assert calls[1]["url"] == "http://127.0.0.1:18200/api/v1/ai-jobs/jobs/job-1/billing"


def test_real_flow_run_uses_double_job_type(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_MODEL_ID", raising=False)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text("DISABLE_HTTP_AUTH_HEADER=true\nDEFAULT_MODEL_ID=gpt-5.4-mini\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/.env").write_text("API_PORT=18200\n", encoding="utf-8")
    calls = []

    def fake_request_json(url, *, method, headers, payload=None, timeout_seconds=10):
        calls.append({"url": url, "method": method, "payload": payload})
        if method == "POST":
            return {"code": "0", "data": {"job": {"job_id": "job-2", "job_status": "queued"}}}
        return {
            "code": "0",
            "data": {
                "billing": {
                    "status": "estimated",
                    "currency": "USD",
                    "total_cost_amount": "0.00000200",
                    "usage_units": {},
                    "pricing_refs": [],
                    "ai_call_count": 2,
                    "billable_call_count": 2,
                    "failed_call_count": 0,
                    "diagnostic_reason": None,
                    "finalized_at": None,
                }
            },
        }

    monkeypatch.setattr(llm_job_billing, "request_json", fake_request_json)
    monkeypatch.setattr(
        llm_job_billing,
        "poll_job_envelope",
        lambda **_kwargs: {
            "code": "0",
            "data": {"job": {"job_id": "job-2", "job_status": "succeeded", "job_type": "job_real_llm_double_echo"}},
        },
    )

    llm_job_billing.run(
        confirm_cost=True,
        job_type="job_real_llm_double_echo",
        api_url="http://127.0.0.1:18200",
        model_id=None,
        input_text="hello",
        instruction="first",
        second_instruction="second",
        caller_id="caller-1",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        client_request_id="client-2",
        json_output=True,
    )

    assert calls[0]["payload"]["job_type"] == "job_real_llm_double_echo"
    assert calls[0]["payload"]["job_params"]["first_instruction"] == "first"
    assert calls[0]["payload"]["job_params"]["second_instruction"] == "second"


def test_real_flow_json_output_is_machine_readable(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text("DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=true\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/.env").write_text("API_PORT=18200\n", encoding="utf-8")

    monkeypatch.setattr(
        llm_job_billing,
        "request_json",
        lambda url, **kwargs: (
            {"code": "0", "data": {"job": {"job_id": "job-1"}}}
            if kwargs["method"] == "POST"
            else {
                "code": "0",
                "data": {
                    "billing": {
                        "status": "estimated",
                        "currency": "USD",
                        "total_cost_amount": "0.00000100",
                        "usage_units": {},
                        "pricing_refs": [],
                        "ai_call_count": 1,
                        "billable_call_count": 1,
                        "failed_call_count": 0,
                        "diagnostic_reason": None,
                        "finalized_at": None,
                    }
                },
            }
        ),
    )
    monkeypatch.setattr(
        llm_job_billing,
        "poll_job_envelope",
        lambda **_kwargs: {
            "code": "0",
            "msg": "success",
            "data": {"job": {"job_id": "job-1", "job_status": "succeeded", "job_type": "job_real_llm_echo"}},
        },
    )

    llm_job_billing.run(
        confirm_cost=True,
        job_type="job_real_llm_echo",
        api_url="http://127.0.0.1:18200",
        model_id="gpt-5.4-mini",
        input_text="hello",
        instruction="reply once",
        second_instruction=None,
        caller_id="caller-1",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        client_request_id="client-1",
        json_output=True,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["conclusion"] == "job=succeeded billing=estimated cost=0.00000100 USD ai_call_count=1"
    assert payload["summary"]["job_id"] == "job-1"
    assert payload["summary"]["billing_status"] == "estimated"
    assert "generated by scripts/real-flow.sh" in payload["summary"]["note"]
    assert payload["responses"]["create_job"]["data"]["job"]["job_id"] == "job-1"
    assert payload["responses"]["get_job"]["data"]["job"]["job_status"] == "succeeded"
    assert payload["responses"]["get_billing"]["data"]["billing"]["status"] == "estimated"
