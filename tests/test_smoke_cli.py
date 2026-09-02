import json
import io
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from typer.testing import CliRunner

from smoke.cli import app
from app.object_storage import AliyunOSSConfig, PutObjectResult
from app.ai.adapters.base import ImageGenerationResult
from smoke.flows.asset import search_eval as asset_search_eval
from smoke.flows.audio import stem_separation as audio_stem_separation
from smoke.flows.image import adapter_probe as adapter_image_probe
from smoke.flows.image import poster_title_image
from smoke.flows.llm import billing as llm_job_billing
from smoke.flows.oss import image_upload as oss_image_upload
from smoke.flows.translation import tagged_text_translation
from smoke.flows.examples import lifecycle_probe as example_lifecycle_probe
from smoke.flows.examples import reconciler_probe as example_reconciler_probe
from smoke.harness import callback_capture
from smoke.harness import cli_contract
from smoke.harness import env_runtime
from smoke.harness import http_runtime
from smoke.harness import service_runtime
from smoke.harness.errors import FlowError
from smoke.jobs import cli_contract as job_cli_contract
from smoke.jobs import reconciler_faults
from smoke.jobs import runtime as job_runtime


runner = CliRunner()
STORAGE_ENV_KEYS = [
    "STORAGE_BACKEND",
    "LOCAL_OBJECT_STORAGE_PATH",
    "OSS_INPUT_MAX_BYTES",
    "OSS_BUCKET",
    "OSS_REGION",
    "OSS_ACCESS_KEY_ID",
    "OSS_ACCESS_KEY_SECRET",
    "OSS_PROJECT_ROOT",
    "OSS_OUTPUT_PREFIX",
    "OSS_PUBLIC_ENDPOINT",
    "OSS_ENDPOINT",
]
API_ENV_KEYS = ["API_URL", "API_HOST", "API_PORT", "ENV_FILE"]


@pytest.fixture(autouse=True)
def clear_inherited_env_file(monkeypatch):
    monkeypatch.delenv("ENV_FILE", raising=False)


def _transparent_png_bytes() -> bytes:
    image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    for x in range(16, 24):
        for y in range(16, 24):
            image.putpixel((x, y), (255, 0, 0, 255))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes() -> bytes:
    image = Image.new("RGB", (40, 40), (255, 255, 255))
    for x in range(16, 24):
        for y in range(16, 24):
            image.putpixel((x, y), (255, 0, 0))
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


def clear_storage_env(monkeypatch):
    for key in STORAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def clear_api_env(monkeypatch):
    for key in API_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def append_root_env(root: Path, *lines: str) -> None:
    env_path = root / ".env"
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    prefix = existing.rstrip("\n")
    suffix = "\n".join(lines)
    env_path.write_text(f"{prefix}\n{suffix}\n" if prefix else f"{suffix}\n", encoding="utf-8")


def write_smoke_model_catalog(root: Path, *, default_model_id: str = "gpt-5.4-mini") -> Path:
    path = root / "models.yaml"
    path.write_text(
        "\n".join(
            [
                'version: "2"',
                "default_model_ids:",
                f"  text_generation: {default_model_id}",
                "models: []",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _job_options(
    *,
    confirm_run: bool = True,
    client_request_id: str | None = None,
    expect_status: str = "auto",
) -> job_cli_contract.JobSmokeOptions:
    return job_cli_contract.job_smoke_options(
        confirm_run=confirm_run,
        client_request_id=client_request_id,
        expect_status=expect_status,
    )


def _callback_options(
    *,
    callback_url: str | None = None,
    local_callback: bool = False,
    callback_event: str = "both",
    wait_callback: bool = True,
    callback_timeout_seconds: int | None = None,
) -> cli_contract.CallbackSmokeOptions:
    return cli_contract.callback_smoke_options(
        callback_url=callback_url,
        local_callback=local_callback,
        callback_event=callback_event,
        wait_callback=wait_callback,
        callback_timeout_seconds=callback_timeout_seconds,
    )


def test_smoke_main_rejects_unsupported_global_option_for_scenario():
    result = runner.invoke(app, ["--output-dir", "smoke/results", "tagged-text-translation", "--confirm-cost"])

    assert result.exit_code == 2
    assert "--output-dir is not supported by smoke scenario 'tagged-text-translation'" in result.stderr


def test_smoke_cli_requires_confirm_cost():
    result = runner.invoke(app, ["llm-job-billing"])

    assert result.exit_code == 2
    assert "real LLM smoke scenario requires --confirm-cost" in result.stderr


def test_example_lifecycle_probe_cli_requires_confirm_run():
    result = runner.invoke(app, ["example-lifecycle-probe"])

    assert result.exit_code == 2
    assert "example lifecycle probe smoke requires --confirm-run" in result.stderr


def test_example_lifecycle_probe_cli_forwards_options(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(example_lifecycle_probe, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "--base-url",
            "http://127.0.0.1:18200",
            "--env-file",
            ".env",
            "--service-api-key",
            "test-token",
            "--caller-id",
            "cms-test",
            "--timeout",
            "30",
            "--poll-interval",
            "0.25",
            "--json",
            "example-lifecycle-probe",
            "--confirm-run",
            "--probe-id",
            "probe-1",
            "--message",
            "hello",
            "--sleep-seconds",
            "1.5",
            "--fail",
            "--fail-after-seconds",
            "0.5",
            "--expect-status",
            "failed",
            "--callback-url",
            "http://127.0.0.1:19000/callback",
            "--callback-event",
            "failed",
            "--callback-timeout-seconds",
            "7",
            "--no-wait-callback",
            "--client-request-id",
            "client-probe-1",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "job_options": job_cli_contract.JobSmokeOptions(
            confirm_run=True,
            client_request_id="client-probe-1",
            expect_status="failed",
        ),
        "callback_options": cli_contract.CallbackSmokeOptions(
            callback_url="http://127.0.0.1:19000/callback",
            local_callback=False,
            callback_event="failed",
            wait_callback=False,
            callback_timeout_seconds=7,
        ),
        "api_url": "http://127.0.0.1:18200",
        "env_file": ".env",
        "allow_remote_api": False,
        "service_api_key": "test-token",
        "caller_id": "cms-test",
        "timeout_seconds": 30,
        "poll_interval_seconds": 0.25,
        "probe_id": "probe-1",
        "message": "hello",
        "sleep_seconds": 1.5,
        "fail": True,
        "fail_after_seconds": 0.5,
        "result_payload": None,
        "result_size_bytes": 0,
        "json_output": True,
    }


def test_example_reconciler_probe_cli_requires_confirm_run():
    result = runner.invoke(app, ["example-reconciler-probe"])

    assert result.exit_code == 2
    assert "example reconciler probe smoke requires --confirm-run" in result.stderr


def test_example_reconciler_probe_cli_requires_confirm_fault_injection():
    result = runner.invoke(app, ["example-reconciler-probe", "--confirm-run", "--local-callback"])

    assert result.exit_code == 2
    assert "example reconciler probe smoke requires --confirm-fault-injection" in result.stderr


def test_example_reconciler_probe_cli_forwards_options(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(example_reconciler_probe, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "--base-url",
            "http://127.0.0.1:18200",
            "--env-file",
            ".env",
            "--service-api-key",
            "test-token",
            "--caller-id",
            "cms-test",
            "--timeout",
            "30",
            "--poll-interval",
            "0.25",
            "--json",
            "example-reconciler-probe",
            "--confirm-run",
            "--confirm-fault-injection",
            "--probe-id",
            "reconcile-1",
            "--message",
            "hello",
            "--sleep-seconds",
            "1.5",
            "--fail",
            "--fail-after-seconds",
            "0.5",
            "--expect-status",
            "failed",
            "--local-callback",
            "--callback-event",
            "failed",
            "--callback-timeout-seconds",
            "7",
            "--client-request-id",
            "client-reconcile-1",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "job_options": job_cli_contract.JobSmokeOptions(
            confirm_run=True,
            client_request_id="client-reconcile-1",
            expect_status="failed",
        ),
        "callback_options": cli_contract.CallbackSmokeOptions(
            callback_url=None,
            local_callback=True,
            callback_event="failed",
            wait_callback=True,
            callback_timeout_seconds=7,
        ),
        "confirm_fault_injection": True,
        "api_url": "http://127.0.0.1:18200",
        "env_file": ".env",
        "allow_remote_api": False,
        "service_api_key": "test-token",
        "caller_id": "cms-test",
        "timeout_seconds": 30,
        "poll_interval_seconds": 0.25,
        "probe_id": "reconcile-1",
        "message": "hello",
        "sleep_seconds": 1.5,
        "fail": True,
        "fail_after_seconds": 0.5,
        "result_payload": None,
        "result_size_bytes": 0,
        "json_output": True,
    }


def test_reconciler_fault_injection_rejects_release_app_env():
    with pytest.raises(FlowError, match="only allowed for APP_ENV"):
        reconciler_faults.assert_local_fault_injection_app_env("test")


def test_reconciler_fault_terminal_callback_event_type_requires_terminal_status():
    with pytest.raises(FlowError, match="requires terminal job status"):
        reconciler_faults.terminal_callback_event_type("running")


def test_reconciler_fault_database_url_uses_selected_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://runtime/runtime")
    app_env = env_runtime.AppEnv(
        {"DATABASE_URL": "postgresql+asyncpg://file/file", "DB_SSL": "true"},
        profile_selected=True,
        profile_source="cli",
        path=tmp_path / ".env.test",
    )

    assert reconciler_faults.database_url_from_app_env(app_env) == "postgresql+asyncpg://file/file"
    assert reconciler_faults.database_ssl_from_app_env(app_env) is True


def test_example_reconciler_probe_requires_local_callback(monkeypatch):
    context = service_runtime.RuntimeContext(
        app_env=env_runtime.AppEnv(
            {
                "APP_ENV": "local",
                "DATABASE_URL": "postgresql+asyncpg://localhost/test",
                "CALLBACK_SIGNING_SECRET": "test-callback-signing-secret",
                "DISABLE_HTTP_AUTH_HEADER": "true",
                "DISABLE_CALLER_ID_HEADER": "true",
            },
            profile_selected=True,
            profile_source="cli",
            path=Path(".env.test"),
        ),
        summary={
            "api_url": "http://127.0.0.1:8100",
            "jobs_url": "http://127.0.0.1:8100/api/v1/ai-jobs/jobs",
            "ready": True,
        },
    )
    monkeypatch.setattr(job_runtime, "resolve_job_context", lambda **_kwargs: context)

    with pytest.raises(FlowError, match="requires --local-callback"):
        example_reconciler_probe.run(
            job_options=_job_options(),
            callback_options=_callback_options(),
            confirm_fault_injection=True,
            api_url=None,
            env_file=None,
            allow_remote_api=False,
            service_api_key=None,
            caller_id="smoke-cli",
            timeout_seconds=10,
            poll_interval_seconds=0.1,
            probe_id="probe",
            message="hello",
            sleep_seconds=0,
            fail=False,
            fail_after_seconds=0,
            result_payload=None,
            result_size_bytes=0,
            json_output=True,
        )


def test_example_reconciler_probe_rejects_remote_api(monkeypatch):
    context = service_runtime.RuntimeContext(
        app_env={
            "APP_ENV": "local",
            "DATABASE_URL": "postgresql+asyncpg://localhost/test",
            "CALLBACK_SIGNING_SECRET": "test-callback-signing-secret",
            "DISABLE_HTTP_AUTH_HEADER": "true",
            "DISABLE_CALLER_ID_HEADER": "true",
        },
        summary={
            "api_url": "http://remote.example.com",
            "jobs_url": "http://remote.example.com/api/v1/ai-jobs/jobs",
            "ready": True,
        },
    )
    monkeypatch.setattr(job_runtime, "resolve_job_context", lambda **_kwargs: context)

    with pytest.raises(FlowError, match="requires a loopback API URL"):
        example_reconciler_probe.run(
            job_options=_job_options(),
            callback_options=_callback_options(local_callback=True),
            confirm_fault_injection=True,
            api_url=None,
            env_file=None,
            allow_remote_api=True,
            service_api_key=None,
            caller_id="smoke-cli",
            timeout_seconds=10,
            poll_interval_seconds=0.1,
            probe_id="probe",
            message="hello",
            sleep_seconds=0,
            fail=False,
            fail_after_seconds=0,
            result_payload=None,
            result_size_bytes=0,
            json_output=True,
        )


def test_example_reconciler_probe_flow_injects_gap_then_waits_for_callback(monkeypatch, capsys):
    context = service_runtime.RuntimeContext(
        app_env=env_runtime.AppEnv(
            {
                "APP_ENV": "local",
                "DATABASE_URL": "postgresql+asyncpg://localhost/test",
                "CALLBACK_SIGNING_SECRET": "test-callback-signing-secret",
                "DISABLE_HTTP_AUTH_HEADER": "true",
                "DISABLE_CALLER_ID_HEADER": "true",
            },
            profile_selected=True,
            profile_source="cli",
            path=Path(".env.test"),
        ),
        summary={
            "api_url": "http://127.0.0.1:8100",
            "jobs_url": "http://127.0.0.1:8100/api/v1/ai-jobs/jobs",
            "ready": True,
        },
    )
    calls: list[str] = []

    class FakeReceiver:
        url = "http://127.0.0.1:19000/callback"

        def __enter__(self):
            calls.append("receiver.enter")
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            calls.append("receiver.exit")
            return None

        def wait_for_event(self, _expectation, *, timeout_seconds):
            calls.append("receiver.wait")
            assert timeout_seconds > 0
            return {
                "body": {
                    "event": "job.succeeded",
                    "job": {"job_id": "probe-job-reconciler", "job_status": "succeeded"},
                },
                "signature": {"checked": True, "valid": True},
            }

        def snapshot(self):
            return [{"body": {"event": "job.succeeded"}}]

    def fake_request_json(*_args, **kwargs):
        calls.append("create")
        payload = kwargs["payload"]
        assert "callback" not in payload
        return {
            "code": "0",
            "data": {"job": {"job_id": "probe-job-reconciler", "job_status": "queued"}},
        }

    def fake_poll_job_envelope(**_kwargs):
        calls.append("poll-terminal")
        return {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "probe-job-reconciler",
                    "job_status": "succeeded",
                    "job_result": {
                        "probe_id": "probe",
                        "message": "hello",
                        "worker_observed_at": "2026-08-28T00:00:00+00:00",
                    },
                    "callback": {"status": "not_configured", "attempt": 0},
                }
            },
        }

    def fake_inject_missing_callback_outbox(**kwargs):
        calls.append("inject")
        assert kwargs["database_url"] == "postgresql+asyncpg://localhost/test"
        assert kwargs["database_ssl"] is False
        assert kwargs["job_id"] == "probe-job-reconciler"
        assert kwargs["callback_url"] == "http://127.0.0.1:19000/callback"
        assert kwargs["callback_events"] == ["job.succeeded", "job.failed"]
        return reconciler_faults.CallbackGapInjection(
            job_id="probe-job-reconciler",
            job_status="succeeded",
            callback_event_type="job.succeeded",
            callback_url="http://127.0.0.1:19000/callback",
            injected_at="2026-08-28T00:00:00+00:00",
        )

    def fake_wait_for_callback_outbox(**kwargs):
        calls.append("wait-outbox")
        assert kwargs["database_url"] == "postgresql+asyncpg://localhost/test"
        assert kwargs["database_ssl"] is False
        assert kwargs["callback_event_type"] == "job.succeeded"
        return {
            "callback_outbox_id": "callback-outbox-1",
            "event_id": "event-1",
            "event_type": "job.succeeded",
            "status": "pending",
            "delivery_attempts": 0,
        }

    def fake_poll_callback_envelope(**_kwargs):
        calls.append("poll-callback")
        return {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "probe-job-reconciler",
                    "job_status": "succeeded",
                    "callback": {"status": "delivered", "attempt": 1},
                }
            },
        }

    monkeypatch.setattr(job_runtime, "resolve_job_context", lambda **_kwargs: context)
    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    monkeypatch.setattr(job_runtime, "poll_job_envelope", fake_poll_job_envelope)
    monkeypatch.setattr(example_lifecycle_probe, "local_callback_server", lambda _context: FakeReceiver())
    monkeypatch.setattr(reconciler_faults, "inject_missing_callback_outbox", fake_inject_missing_callback_outbox)
    monkeypatch.setattr(reconciler_faults, "wait_for_callback_outbox", fake_wait_for_callback_outbox)
    monkeypatch.setattr(reconciler_faults, "callback_outbox_evidence", lambda **_kwargs: None)
    monkeypatch.setattr(example_lifecycle_probe, "poll_callback_envelope", fake_poll_callback_envelope)

    example_reconciler_probe.run(
        job_options=_job_options(),
        callback_options=_callback_options(local_callback=True),
        confirm_fault_injection=True,
        api_url=None,
        env_file=None,
        allow_remote_api=False,
        service_api_key=None,
        caller_id="smoke-cli",
        timeout_seconds=10,
        poll_interval_seconds=0.1,
        probe_id="probe",
        message="hello",
        sleep_seconds=0,
        fail=False,
        fail_after_seconds=0,
        result_payload=None,
        result_size_bytes=0,
        json_output=True,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["summary"]["fault_injection"]["kind"] == "terminal_callback_outbox_missing"
    assert payload["summary"]["callback_status"] == "delivered"
    assert calls == [
        "receiver.enter",
        "create",
        "poll-terminal",
        "inject",
        "wait-outbox",
        "poll-callback",
        "receiver.wait",
        "receiver.exit",
    ]


def test_poster_title_image_cli_requires_confirm_cost():
    result = runner.invoke(app, ["poster-title-image"])

    assert result.exit_code == 2
    assert "poster title image smoke scenario requires --confirm-cost" in result.stderr


def test_poster_title_image_cli_requires_explicit_reference(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=true\n",
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")

    result = runner.invoke(app, ["poster-title-image", "--confirm-cost"])

    assert result.exit_code == 2
    assert "requires --reference, --reference-url-ref-json, or explicit OSS URL Ref options" in result.stderr


def test_poster_title_image_cli_accepts_reference_alias(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(poster_title_image, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "poster-title-image",
            "--confirm-cost",
            "--reference",
            ".data/title/标题2.png",
            "--language",
            "es",
            "--title-text",
            "Cuando el amor se alejo",
        ],
    )

    assert result.exit_code == 0
    assert captured["reference_image"] == ".data/title/标题2.png"
    assert captured["confirm_cost"] is True


def test_poster_title_image_cli_accepts_reference_url_ref_json(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(poster_title_image, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "poster-title-image",
            "--confirm-cost",
            "--reference-url-ref-json",
            ".run/reference-image.json",
        ],
    )

    assert result.exit_code == 0
    assert captured["reference_url_ref_json"] == ".run/reference-image.json"
    assert captured["reference_image"] is None


def test_tagged_text_translation_cli_forwards_default_source_language_as_none(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(tagged_text_translation, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "tagged-text-translation",
            "--confirm-cost",
            "--target-language",
            "zh",
            "--item-id",
            "homepage.title",
            "--text",
            "<b>Hello</b> world",
            "--max-target-chars-hint",
            "20",
            "--client-request-id",
            "req-translate-1",
        ],
    )

    assert result.exit_code == 0
    assert captured["confirm_cost"] is True
    assert captured["source_language"] is None
    assert captured["target_language"] == "zh"
    assert captured["item_id"] == "homepage.title"
    assert captured["text"] == "<b>Hello</b> world"
    assert captured["max_target_chars_hint"] == 20
    assert captured["client_request_id"] == "req-translate-1"


def test_tagged_text_translation_help_documents_json_and_preview_modes():
    result = runner.invoke(app, ["tagged-text-translation", "--help"])

    assert result.exit_code == 0
    assert "--json 是 smoke 全局参数，必须放在场景命令前" in result.stdout
    assert "翻译前后 preview" in result.stdout
    assert "完整 source_text / translated_text" in result.stdout


def test_tagged_text_translation_rejects_trailing_json_option():
    result = runner.invoke(app, ["tagged-text-translation", "--json", "--help"])

    assert result.exit_code == 2
    output = result.stdout + result.stderr
    assert "No such option '--json'" in output


def test_tagged_text_translation_human_preview_sanitizes_truncates_and_limits_items(capsys):
    long_text = "A\r\x1b[31mB\n\tC\x07" + ("x" * 600)
    evidence_items = [
        {
            "index": 1,
            "id": "item.one",
            "source_text": long_text,
            "translated_text": "译文" + ("y" * 600),
            "char_count": {"source": 604, "target": 602, "within_hint": True},
        },
        {"index": 2, "id": "item.two", "source_text": "two", "translated_text": "二", "char_count": {}},
        {"index": 3, "id": "item.three", "source_text": "three", "translated_text": "三", "char_count": {}},
        {"index": 4, "id": "item.four", "source_text": "four", "translated_text": "四", "char_count": {}},
    ]

    tagged_text_translation._print_translation_preview(
        evidence_items=evidence_items,
        source_language="en",
        target_language="zh",
    )

    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "\r" not in output
    assert "\x07" not in output
    assert "\\rB\\n\\tC" in output
    assert "<truncated>" in output
    assert "item[1]" in output
    assert "item[3]" in output
    assert "item[4]" not in output
    assert "omitted=1; use --json for complete texts" in output


def test_tagged_text_translation_json_output_includes_complete_translation_evidence(tmp_path, monkeypatch, capsys):
    clear_api_env(monkeypatch)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    append_root_env(
        tmp_path,
        "API_HOST=127.0.0.1",
        "API_PORT=18200",
        "DISABLE_HTTP_AUTH_HEADER=true",
        "DISABLE_CALLER_ID_HEADER=true",
    )

    def fake_request_json(url, *, method, headers, payload=None, timeout_seconds=10):
        if method == "POST":
            return {"code": "0", "data": {"job": {"job_id": "translate-job-1", "job_status": "queued"}}}
        if url.endswith("/billing"):
            return {
                "code": "0",
                "data": {
                    "billing": {
                        "status": "estimated",
                        "currency": "USD",
                        "total_cost_amount": "0.00000123",
                        "ai_call_count": 1,
                        "billable_call_count": 1,
                        "failed_call_count": 0,
                    }
                },
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    monkeypatch.setattr(
            job_runtime,
            "poll_job_envelope",
        lambda **_kwargs: {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "translate-job-1",
                    "job_status": "succeeded",
                    "job_type": "tagged_text_translation",
                    "job_result": {
                        "source_language": "en",
                        "target_language": "zh",
                        "items": [
                            {
                                "id": "homepage.title",
                                "source_text": "<span>Hello {user_name}</span>",
                                "translated_text": "<span>你好 {user_name}</span>",
                                "char_count": {
                                    "source": 6,
                                    "target": 3,
                                    "target_limit_hint": 30,
                                    "within_hint": True,
                                },
                            }
                        ],
                    },
                }
            },
        },
    )

    tagged_text_translation.run(
        confirm_cost=True,
        api_url=None,
        env_file=None,
        allow_remote_api=False,
        service_api_key=None,
        caller_id="smoke-cli",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        source_language="en",
        target_language="zh",
        item_id="homepage.title",
        text="<span>Hello {user_name}</span>",
        max_target_chars_hint=30,
        items_json=None,
        client_request_id="translate-json-1",
        json_output=True,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["scenario"] == "tagged-text-translation"
    assert payload["job"] == {"id": "translate-job-1", "status": "succeeded", "type": "tagged_text_translation"}
    assert payload["request"]["items"][0]["source_text"] == "<span>Hello {user_name}</span>"
    assert payload["result"]["items"][0]["source_text"] == "<span>Hello {user_name}</span>"
    assert payload["result"]["items"][0]["translated_text"] == "<span>你好 {user_name}</span>"
    assert payload["billing"]["mode"] == "estimated"
    assert payload["responses"]["get_job"]["data"]["job"]["job_result"]["items"][0]["translated_text"] == "<span>你好 {user_name}</span>"


def test_tagged_text_translation_human_output_prints_translation_preview(tmp_path, monkeypatch, capsys):
    clear_api_env(monkeypatch)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    append_root_env(
        tmp_path,
        "API_HOST=127.0.0.1",
        "API_PORT=18200",
        "DISABLE_HTTP_AUTH_HEADER=true",
        "DISABLE_CALLER_ID_HEADER=true",
    )

    def fake_request_json(url, *, method, headers, payload=None, timeout_seconds=10):
        if method == "POST":
            return {"code": "0", "data": {"job": {"job_id": "translate-job-2", "job_status": "queued"}}}
        return {
            "code": "0",
            "data": {
                "billing": {
                    "status": "estimated",
                    "currency": "USD",
                    "total_cost_amount": "0.00000456",
                    "ai_call_count": 1,
                    "billable_call_count": 1,
                    "failed_call_count": 0,
                }
            },
        }

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    monkeypatch.setattr(
            job_runtime,
            "poll_job_envelope",
        lambda **_kwargs: {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "translate-job-2",
                    "job_status": "succeeded",
                    "job_type": "tagged_text_translation",
                    "job_result": {
                        "source_language": "en",
                        "target_language": "zh",
                        "items": [
                            {
                                "id": "homepage.title",
                                "source_text": "<span>Hello {user_name}</span>",
                                "translated_text": "<span>你好 {user_name}</span>",
                                "char_count": {
                                    "source": 6,
                                    "target": 3,
                                    "target_limit_hint": 30,
                                    "within_hint": True,
                                },
                            }
                        ],
                    },
                }
            },
        },
    )

    tagged_text_translation.run(
        confirm_cost=True,
        api_url=None,
        env_file=None,
        allow_remote_api=False,
        service_api_key=None,
        caller_id="smoke-cli",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        source_language="en",
        target_language="zh",
        item_id="homepage.title",
        text="<span>Hello {user_name}</span>",
        max_target_chars_hint=30,
        items_json=None,
        client_request_id="translate-human-1",
        json_output=False,
    )

    output = capsys.readouterr().out
    assert "== Translation ==" in output
    assert "item[1] id=homepage.title source=en target=zh" in output
    assert "source: <span>Hello {user_name}</span>" in output
    assert "target: <span>你好 {user_name}</span>" in output
    assert "OK        billing" in output


def test_smoke_public_module_entry_accepts_global_json_before_list():
    result = subprocess.run(
        [sys.executable, "-m", "smoke", "--json", "list"],
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "tagged-text-translation" in {scenario["name"] for scenario in payload["scenarios"]}
    assert "example-lifecycle-probe" in {scenario["name"] for scenario in payload["scenarios"]}


def test_smoke_ready_prints_resolved_context(tmp_path, monkeypatch):
    for name in [
        "API_URL",
        "SERVICE_API_KEY",
        "STORAGE_BACKEND",
        "OSS_BUCKET",
        "OSS_REGION",
        "OSS_PROJECT_ROOT",
        "OSS_PUBLIC_ENDPOINT",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(http_runtime, "request_json", lambda *args, **kwargs: {"status": "ok", "db": "ok", "redis": "ok"})
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "\n".join(
            [
                "API_URL=http://test.example.com",
                "SERVICE_API_KEY=file-token",
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket-a",
                "OSS_REGION=cn-hangzhou",
                "OSS_PROJECT_ROOT=project-a/",
                "OSS_PUBLIC_ENDPOINT=cdn.example.com",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--env-file",
            "env_test/.env",
            "--allow-remote-api",
            "--caller-id",
            "default",
            "--json",
            "ready",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["api_url"] == "http://test.example.com"
    assert payload["api_url_source"] == "env_file"
    assert payload["env_file_source"] == "cli"
    assert payload["env_file_overrides_runtime"] is True
    assert payload["service_api_key_source"] == "env_file"
    assert payload["service_api_key_env_file_present"] is True
    assert payload["service_api_key_runtime_present"] is False
    assert payload["caller_id"] == "default"
    assert "jobs_url" not in payload
    assert "storage_backend" not in payload
    assert "oss_public_endpoint" not in payload
    assert payload["ready"] is True
    assert payload["problems"] == []
    assert payload["ready_response"] == {"status": "ok", "db": "ok", "redis": "ok"}


def test_smoke_health_env_file_overrides_runtime_api_url(tmp_path, monkeypatch):
    monkeypatch.setenv("API_URL", "http://runtime.example.com")
    monkeypatch.setenv("SERVICE_API_KEY", "runtime-token")
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    captured: dict[str, Any] = {}

    def fake_request_json(url, **_kwargs):
        captured["url"] = url
        return {"status": "ok"}

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "API_HOST=127.0.0.1\nAPI_PORT=18210\nSERVICE_API_KEY=file-token\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["--env-file", "env_test/.env", "--json", "health"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert captured["url"] == "http://127.0.0.1:18210/health"
    assert payload["base_url"] == "http://127.0.0.1:18210"
    assert payload["ready"] is True


def test_smoke_ready_env_file_overrides_runtime_auth_and_url(tmp_path, monkeypatch):
    monkeypatch.setenv("API_URL", "http://runtime.example.com")
    monkeypatch.setenv("SERVICE_API_KEY", "runtime-token")
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    captured: dict[str, Any] = {}

    def fake_request_json(url, **_kwargs):
        captured["url"] = url
        return {"status": "ok", "db": "ok", "redis": "ok"}

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "API_URL=http://env-file.example.com\nSERVICE_API_KEY=file-token\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--env-file",
            "env_test/.env",
            "--allow-remote-api",
            "--json",
            "ready",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert captured["url"] == "http://env-file.example.com/healthz"
    assert payload["api_url"] == "http://env-file.example.com"
    assert payload["api_url_source"] == "env_file"
    assert payload["service_api_key_source"] == "env_file"
    assert payload["service_api_key_env_file_present"] is True
    assert payload["service_api_key_runtime_present"] is True
    assert payload["env_file_overrides_runtime"] is True


def test_smoke_ready_env_file_host_port_override_runtime_api_url(tmp_path, monkeypatch):
    monkeypatch.setenv("API_URL", "http://runtime.example.com")
    monkeypatch.setenv("SERVICE_API_KEY", "runtime-token")
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    captured: dict[str, Any] = {}

    def fake_request_json(url, **_kwargs):
        captured["url"] = url
        return {"status": "ok", "db": "ok", "redis": "ok"}

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "API_HOST=127.0.0.1\nAPI_PORT=18210\nSERVICE_API_KEY=file-token\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--env-file",
            "env_test/.env",
            "--json",
            "ready",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert captured["url"] == "http://127.0.0.1:18210/healthz"
    assert payload["api_url"] == "http://127.0.0.1:18210"
    assert payload["api_url_source"] == "derived_from_api_host_port"
    assert payload["service_api_key_source"] == "env_file"
    assert payload["env_file_overrides_runtime"] is True


@pytest.mark.parametrize(
    ("runtime_host", "runtime_port", "env_file_content", "expected_url"),
    [
        (
            "runtime-host.example.com",
            "19191",
            "API_PORT=18210\nSERVICE_API_KEY=file-token\n",
            "http://runtime-host.example.com:18210",
        ),
        (
            "127.0.0.1",
            "19191",
            "API_HOST=env-file.example.com\nSERVICE_API_KEY=file-token\n",
            "http://env-file.example.com:19191",
        ),
    ],
)
def test_smoke_ready_env_file_partial_host_port_falls_back_to_runtime_env(
    tmp_path,
    monkeypatch,
    runtime_host,
    runtime_port,
    env_file_content,
    expected_url,
):
    monkeypatch.setenv("API_HOST", runtime_host)
    monkeypatch.setenv("API_PORT", runtime_port)
    monkeypatch.setenv("SERVICE_API_KEY", "runtime-token")
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    captured: dict[str, Any] = {}

    def fake_request_json(url, **_kwargs):
        captured["url"] = url
        return {"status": "ok", "db": "ok", "redis": "ok"}

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text(env_file_content, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--env-file",
            "env_test/.env",
            "--allow-remote-api",
            "--json",
            "ready",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert captured["url"] == f"{expected_url}/healthz"
    assert payload["api_url"] == expected_url
    assert payload["api_url_source"] == "derived_from_api_host_port"
    assert payload["service_api_key_source"] == "env_file"
    assert payload["env_file_overrides_runtime"] is True


def test_smoke_ready_cli_service_key_overrides_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "runtime-token")
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(http_runtime, "request_json", lambda *_args, **_kwargs: {"status": "ok", "db": "ok", "redis": "ok"})
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "API_URL=http://env-file.example.com\nSERVICE_API_KEY=file-token\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--env-file",
            "env_test/.env",
            "--allow-remote-api",
            "--service-api-key",
            "cli-token",
            "--json",
            "ready",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["service_api_key_source"] == "cli"
    assert payload["service_api_key_env_file_present"] is True
    assert payload["service_api_key_runtime_present"] is True
    assert payload["env_file_overrides_runtime"] is True


def test_smoke_ready_env_file_profile_does_not_leak_between_invocations(tmp_path, monkeypatch):
    monkeypatch.setenv("API_URL", "http://runtime.example.com")
    monkeypatch.setenv("SERVICE_API_KEY", "runtime-token")
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    captured_urls: list[str] = []

    def fake_request_json(url, **_kwargs):
        captured_urls.append(url)
        return {"status": "ok", "db": "ok", "redis": "ok"}

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "API_URL=http://env-file.example.com\nSERVICE_API_KEY=file-token\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "API_URL=http://default-file.example.com\nSERVICE_API_KEY=default-file-token\n",
        encoding="utf-8",
    )

    first = runner.invoke(
        app,
        [
            "--env-file",
            "env_test/.env",
            "--allow-remote-api",
            "--json",
            "ready",
        ],
    )
    second = runner.invoke(app, ["--allow-remote-api", "--json", "ready"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert captured_urls == ["http://env-file.example.com/healthz", "http://runtime.example.com/healthz"]
    assert json.loads(first.stdout)["env_file_overrides_runtime"] is True
    assert json.loads(second.stdout)["env_file_overrides_runtime"] is False
    assert json.loads(second.stdout)["api_url_source"] == "runtime_env"


def test_smoke_runtime_context_uses_env_file_profile_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("API_URL", "http://runtime.example.com")
    monkeypatch.setenv("SERVICE_API_KEY", "runtime-token")
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "API_URL=http://env-file.example.com\nSERVICE_API_KEY=file-token\n",
        encoding="utf-8",
    )

    context = service_runtime.resolve_runtime_context(
        env_file="env_test/.env",
        api_url=None,
        allow_remote_api=True,
        caller_id="smoke-cli",
        root_dir=tmp_path,
    )

    assert context.summary["api_url"] == "http://env-file.example.com"
    assert context.summary["api_url_source"] == "env_file"
    assert context.summary["service_api_key_source"] == "env_file"
    assert context.summary["env_file_overrides_runtime"] is True


def test_smoke_ready_rejects_missing_service_api_key(tmp_path, monkeypatch):
    for name in ["API_URL", "SERVICE_API_KEY", "DISABLE_HTTP_AUTH_HEADER"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text("API_URL=http://127.0.0.1:8100\n", encoding="utf-8")

    result = runner.invoke(app, ["--json", "ready"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ready"] is False
    assert len(payload["problems"]) == 1
    assert "SERVICE_API_KEY is required unless DISABLE_HTTP_AUTH_HEADER=true" in payload["problems"][0]


def test_smoke_list_outputs_standard_scenario_metadata():
    result = runner.invoke(app, ["--json", "list"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    scenario_names = {scenario["name"] for scenario in payload["scenarios"]}
    assert {
        "example-lifecycle-probe",
        "llm-job-billing",
        "poster-title-image",
        "audio-stem-separation",
        "tagged-text-translation",
    }.issubset(scenario_names)
    for scenario in payload["scenarios"]:
        assert {"name", "entrypoints", "type", "acceptance_class", "dependencies", "destructive", "supports_resume"} <= set(
            scenario
        )
    probe = next(scenario for scenario in payload["scenarios"] if scenario["name"] == "example-lifecycle-probe")
    assert {"api", "dispatcher", "taskiq_worker"} <= set(probe["dependencies"])
    assert probe["entrypoints"] == ["example-lifecycle-probe"]
    assert probe["conditional_dependencies"] == ["callbacker"]
    assert probe["contract_roles"] == ["reconciler"]
    assert probe["standard_option_groups"] == ["job", "callback"]
    reconciler_probe = next(
        scenario for scenario in payload["scenarios"] if scenario["name"] == "example-reconciler-probe"
    )
    assert reconciler_probe["destructive"] is True
    assert {"api", "dispatcher", "taskiq_worker", "reconciler", "callbacker", "db", "redis"} <= set(
        reconciler_probe["dependencies"]
    )
    assert reconciler_probe["entrypoints"] == ["example-reconciler-probe"]
    assert reconciler_probe["standard_option_groups"] == ["job", "callback", "fault-injection"]
    audio = next(scenario for scenario in payload["scenarios"] if scenario["name"] == "audio-stem-separation")
    assert audio["entrypoints"] == ["audio-stem-separation run"]
    asset_eval = next(scenario for scenario in payload["scenarios"] if scenario["name"] == "asset-search-eval")
    assert {"image_provider", "embedding_provider"} <= set(asset_eval["dependencies"])
    assert asset_eval["standard_option_groups"] == ["job", "artifact"]


def test_asset_search_eval_cli_forwards_dataset_and_output_options(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(asset_search_eval, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "--base-url",
            "http://127.0.0.1:18200",
            "--env-file",
            ".env",
            "--service-api-key",
            "test-token",
            "--caller-id",
            "default",
            "--timeout",
            "600",
            "--poll-interval",
            "0.5",
            "--output-dir",
            "poc/asset-vector/reports/evals/latest",
            "--json",
            "asset-search-eval",
            "--confirm-run",
            "--confirm-cost",
            "--confirm-full-batch",
            "--client-request-id",
            "eval-client-1",
            "--dataset",
            "regression",
            "--limit",
            "2",
            "--batch-size",
            "3",
            "--no-cleanup",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "confirm_run": True,
        "confirm_cost": True,
        "confirm_full_batch": True,
        "api_url": "http://127.0.0.1:18200",
        "env_file": ".env",
        "allow_remote_api": False,
        "service_api_key": "test-token",
        "caller_id": "default",
        "timeout_seconds": 600,
        "poll_interval_seconds": 0.5,
        "client_request_id": "eval-client-1",
        "dataset": "regression",
        "item_limit": 2,
        "batch_size": 3,
        "output_dir": "poc/asset-vector/reports/evals/latest",
        "cleanup": False,
        "json_output": True,
    }


def test_smoke_list_outputs_human_readable_table():
    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "name" in result.stdout
    assert "entrypoints" in result.stdout
    assert "example-lifecycle-probe" in result.stdout


def test_smoke_health_checks_service_health_endpoint(tmp_path, monkeypatch):
    captured = {}

    for name in ["API_URL", "API_HOST", "API_PORT"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text("API_URL=http://127.0.0.1:18123\n", encoding="utf-8")

    def fake_request_json(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return {"status": "ok", "service": "test", "version": "1.0.0"}

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)

    result = runner.invoke(app, ["--json", "health"])

    assert result.exit_code == 0
    assert captured["url"] == "http://127.0.0.1:18123/health"
    payload = json.loads(result.stdout)
    assert payload["ready"] is True
    assert payload["health"]["status"] == "ok"


def test_smoke_health_outputs_human_readable_table(tmp_path, monkeypatch):
    for name in ["API_URL", "API_HOST", "API_PORT"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text("API_URL=http://127.0.0.1:18123\n", encoding="utf-8")
    monkeypatch.setattr(
        http_runtime,
        "request_json",
        lambda *args, **kwargs: {"status": "ok", "service": "test", "version": "1.0.0"},
    )

    result = runner.invoke(app, ["health"])

    assert result.exit_code == 0
    assert "ready" in result.stdout
    assert "base_url" in result.stdout
    assert "http://127.0.0.1:18123" in result.stdout


def test_smoke_health_returns_3_when_service_is_not_ok(tmp_path, monkeypatch):
    for name in ["API_URL", "API_HOST", "API_PORT"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text("API_URL=http://127.0.0.1:18123\n", encoding="utf-8")
    monkeypatch.setattr(http_runtime, "request_json", lambda *args, **kwargs: {"status": "degraded"})

    result = runner.invoke(app, ["--json", "health"])

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["ready"] is False


def test_smoke_poll_timeout_uses_standard_exit_code_5():
    with pytest.raises(FlowError) as exc_info:
        job_runtime.poll_job_envelope(
            jobs_url="http://127.0.0.1:8100/jobs",
            job_id="job-timeout",
            headers={},
            timeout_seconds=0,
            poll_interval_seconds=0.1,
        )

    assert exc_info.value.exit_code == 5


def test_smoke_poll_progress_callback_skips_terminal_status(monkeypatch):
    monkeypatch.setattr(http_runtime, "request_json",
        lambda *args, **kwargs: {
            "code": "0",
            "data": {"job": {"job_id": "job-1", "job_status": "succeeded"}},
        },
    )
    progress_calls = []

    envelope = job_runtime.poll_job_envelope(
        jobs_url="http://127.0.0.1:8100/jobs",
        job_id="job-1",
        headers={},
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        progress_callback=lambda job, elapsed: progress_calls.append((job, elapsed)),
    )

    assert envelope["data"]["job"]["job_status"] == "succeeded"
    assert progress_calls == []


def test_job_runtime_poll_job_returns_data_job(monkeypatch):
    monkeypatch.setattr(http_runtime, "request_json",
        lambda *args, **kwargs: {
            "code": "0",
            "data": {"job": {"job_id": "job-1", "job_status": "succeeded"}},
        },
    )

    job = job_runtime.poll_job(
        jobs_url="http://127.0.0.1:8100/jobs",
        job_id="job-1",
        headers={},
        timeout_seconds=1,
        poll_interval_seconds=0.1,
    )

    assert job == {"job_id": "job-1", "job_status": "succeeded"}


def test_example_lifecycle_probe_flow_outputs_json_and_callback_payload(tmp_path, monkeypatch, capsys):
    clear_api_env(monkeypatch)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    append_root_env(
        tmp_path,
        "API_HOST=127.0.0.1",
        "API_PORT=18200",
        "DISABLE_HTTP_AUTH_HEADER=true",
        "DISABLE_CALLER_ID_HEADER=true",
    )
    captured = {}

    def fake_request_json(url, *, method, headers, payload=None, timeout_seconds=10):
        captured["url"] = url
        captured["method"] = method
        captured["headers"] = headers
        captured["payload"] = payload
        return {"code": "0", "data": {"job": {"job_id": "probe-job-1", "job_status": "queued"}}}

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    monkeypatch.setattr(
            job_runtime,
            "poll_job_envelope",
        lambda **_kwargs: {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "probe-job-1",
                    "job_status": "succeeded",
                    "job_type": "example_lifecycle_probe",
                    "job_result": {
                        "probe_id": "probe-json",
                        "message": "hello",
                        "requested_sleep_seconds": 0,
                        "fail": False,
                        "elapsed_ms": 1,
                        "worker_observed_at": "2026-08-27T00:00:00+00:00",
                    },
                    "callback": {"status": "pending", "attempt": 0},
                }
            },
        },
    )

    example_lifecycle_probe.run(
        job_options=_job_options(client_request_id="client-probe-json", expect_status="succeeded"),
        callback_options=_callback_options(
            callback_url="http://127.0.0.1:19000/callback",
            callback_event="succeeded",
            wait_callback=False,
        ),
        api_url=None,
        env_file=None,
        allow_remote_api=False,
        service_api_key=None,
        caller_id="smoke-cli",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        probe_id="probe-json",
        message="hello",
        sleep_seconds=0,
        fail=False,
        fail_after_seconds=0,
        result_payload="payload",
        result_size_bytes=0,
        json_output=True,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["scenario"] == "example-lifecycle-probe"
    assert payload["summary"]["job_status"] == "succeeded"
    assert payload["summary"]["callback_waited"] is False
    assert "reconciler" in payload["summary"]["mechanism_evidence"]
    assert captured["url"] == "http://127.0.0.1:18200/api/v1/ai-jobs/jobs"
    assert captured["payload"]["job_type"] == "example_lifecycle_probe"
    assert captured["payload"]["client_request_id"] == "client-probe-json"
    assert captured["payload"]["job_params"]["result_payload"] == "payload"
    assert captured["payload"]["callback"] == {
        "url": "http://127.0.0.1:19000/callback",
        "events": ["job.succeeded"],
    }


def test_example_lifecycle_probe_callback_poll_waits_until_delivered(monkeypatch):
    responses = iter(
        [
            {
                "code": "0",
                "data": {"job": {"job_id": "probe-job-2", "job_status": "succeeded", "callback": {"status": "pending"}}},
            },
            {
                "code": "0",
                "data": {
                    "job": {
                        "job_id": "probe-job-2",
                        "job_status": "succeeded",
                        "callback": {"status": "delivered", "attempt": 1},
                    }
                },
            },
        ]
    )

    monkeypatch.setattr(http_runtime, "request_json", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(example_lifecycle_probe.time, "sleep", lambda _seconds: None)

    envelope = example_lifecycle_probe.poll_callback_envelope(
        jobs_url="http://127.0.0.1:8100/api/v1/ai-jobs/jobs",
        job_id="probe-job-2",
        headers={},
        timeout_seconds=1,
        poll_interval_seconds=0.1,
    )

    assert envelope["data"]["job"]["callback"] == {"status": "delivered", "attempt": 1}


def test_example_lifecycle_probe_callback_poll_fails_on_callback_failed(monkeypatch):
    monkeypatch.setattr(http_runtime, "request_json",
        lambda *args, **kwargs: {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "probe-job-callback-failed",
                    "job_status": "succeeded",
                    "callback": {"status": "failed", "attempt": 3, "last_error": "receiver 500"},
                }
            },
        },
    )

    with pytest.raises(example_lifecycle_probe.FlowError) as exc_info:
        example_lifecycle_probe.poll_callback_envelope(
            jobs_url="http://127.0.0.1:8100/api/v1/ai-jobs/jobs",
            job_id="probe-job-callback-failed",
            headers={},
            timeout_seconds=1,
            poll_interval_seconds=0.1,
        )

    assert exc_info.value.exit_code == 1
    assert "callback for job probe-job-callback-failed failed" in str(exc_info.value)


def test_example_lifecycle_probe_build_payload_rejects_ambiguous_result_payload():
    with pytest.raises(example_lifecycle_probe.FlowError) as exc_info:
        example_lifecycle_probe.build_payload(
            probe_id="probe",
            message="hello",
            sleep_seconds=0,
            fail=False,
            fail_after_seconds=0,
            result_payload="payload",
            result_size_bytes=10,
            callback_url=None,
            callback_event="both",
            client_request_id=None,
        )

    assert exc_info.value.exit_code == 2


def test_example_lifecycle_probe_rejects_local_callback_without_wait():
    with pytest.raises(example_lifecycle_probe.FlowError) as exc_info:
        example_lifecycle_probe.run(
            job_options=_job_options(),
            callback_options=_callback_options(local_callback=True, wait_callback=False),
            api_url=None,
            env_file=None,
            allow_remote_api=False,
            service_api_key=None,
            caller_id="smoke-cli",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            probe_id="probe",
            message="hello",
            sleep_seconds=0,
            fail=False,
            fail_after_seconds=0,
            result_payload=None,
            result_size_bytes=0,
            json_output=True,
        )

    assert exc_info.value.exit_code == 2
    assert "--local-callback requires --wait-callback" in str(exc_info.value)


def test_example_lifecycle_probe_wraps_local_callback_bind_errors(monkeypatch):
    context = service_runtime.RuntimeContext(
        app_env={
            "CALLBACK_SIGNING_SECRET": "test-callback-signing-secret",
            "DISABLE_HTTP_AUTH_HEADER": "true",
            "DISABLE_CALLER_ID_HEADER": "true",
        },
        summary={
            "api_url": "http://127.0.0.1:8100",
            "jobs_url": "http://127.0.0.1:8100/api/v1/ai-jobs/jobs",
            "ready": True,
        },
    )

    def fake_server(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(job_runtime, "resolve_job_context", lambda **_kwargs: context)
    monkeypatch.setattr(callback_capture, "ThreadingHTTPServer", fake_server)

    with pytest.raises(example_lifecycle_probe.FlowError) as exc_info:
        example_lifecycle_probe.run(
            job_options=_job_options(),
            callback_options=_callback_options(local_callback=True),
            api_url=None,
            env_file=None,
            allow_remote_api=False,
            service_api_key=None,
            caller_id="smoke-cli",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            probe_id="probe",
            message="hello",
            sleep_seconds=0,
            fail=False,
            fail_after_seconds=0,
            result_payload=None,
            result_size_bytes=0,
            json_output=True,
        )

    assert exc_info.value.exit_code == 4
    assert "callback capture server failed to bind" in str(exc_info.value)


def test_example_lifecycle_probe_local_callback_requires_signing_secret(monkeypatch):
    monkeypatch.delenv("CALLBACK_SIGNING_SECRET", raising=False)
    context = service_runtime.RuntimeContext(
        app_env={"DISABLE_HTTP_AUTH_HEADER": "true", "DISABLE_CALLER_ID_HEADER": "true"},
        summary={
            "api_url": "http://127.0.0.1:8100",
            "jobs_url": "http://127.0.0.1:8100/api/v1/ai-jobs/jobs",
            "ready": True,
        },
    )
    monkeypatch.setattr(job_runtime, "resolve_job_context", lambda **_kwargs: context)

    with pytest.raises(example_lifecycle_probe.FlowError) as exc_info:
        example_lifecycle_probe.run(
            job_options=_job_options(),
            callback_options=_callback_options(local_callback=True),
            api_url=None,
            env_file=None,
            allow_remote_api=False,
            service_api_key=None,
            caller_id="smoke-cli",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            probe_id="probe",
            message="hello",
            sleep_seconds=0,
            fail=False,
            fail_after_seconds=0,
            result_payload=None,
            result_size_bytes=0,
            json_output=True,
        )

    assert exc_info.value.exit_code == 2
    assert "CALLBACK_SIGNING_SECRET is required" in str(exc_info.value)


def test_example_lifecycle_probe_rejects_local_callback_for_non_loopback_api(monkeypatch):
    context = service_runtime.RuntimeContext(
        app_env={
            "CALLBACK_SIGNING_SECRET": "test-callback-signing-secret",
            "DISABLE_HTTP_AUTH_HEADER": "true",
            "DISABLE_CALLER_ID_HEADER": "true",
        },
        summary={
            "api_url": "https://service.example.com",
            "jobs_url": "https://service.example.com/api/v1/ai-jobs/jobs",
            "ready": True,
        },
    )
    monkeypatch.setattr(job_runtime, "resolve_job_context", lambda **_kwargs: context)

    with pytest.raises(example_lifecycle_probe.FlowError) as exc_info:
        example_lifecycle_probe.run(
            job_options=_job_options(),
            callback_options=_callback_options(local_callback=True),
            api_url=None,
            env_file=None,
            allow_remote_api=True,
            service_api_key=None,
            caller_id="smoke-cli",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            probe_id="probe",
            message="hello",
            sleep_seconds=0,
            fail=False,
            fail_after_seconds=0,
            result_payload=None,
            result_size_bytes=0,
            json_output=True,
        )

    assert exc_info.value.exit_code == 2
    assert "--local-callback requires a loopback API URL" in str(exc_info.value)


def test_example_lifecycle_probe_rejects_local_callback_event_mismatch(monkeypatch):
    context = service_runtime.RuntimeContext(
        app_env={
            "CALLBACK_SIGNING_SECRET": "test-callback-signing-secret",
            "DISABLE_HTTP_AUTH_HEADER": "true",
            "DISABLE_CALLER_ID_HEADER": "true",
        },
        summary={
            "api_url": "http://127.0.0.1:8100",
            "jobs_url": "http://127.0.0.1:8100/api/v1/ai-jobs/jobs",
            "ready": True,
        },
    )
    monkeypatch.setattr(job_runtime, "resolve_job_context", lambda **_kwargs: context)

    class FakeReceiver:
        url = "http://127.0.0.1:19000/callback"

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return None

    monkeypatch.setattr(example_lifecycle_probe, "local_callback_server", lambda _context: FakeReceiver())

    with pytest.raises(example_lifecycle_probe.FlowError) as exc_info:
        example_lifecycle_probe.run(
            job_options=_job_options(expect_status="succeeded"),
            callback_options=_callback_options(local_callback=True, callback_event="failed"),
            api_url=None,
            env_file=None,
            allow_remote_api=False,
            service_api_key=None,
            caller_id="smoke-cli",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            probe_id="probe",
            message="hello",
            sleep_seconds=0,
            fail=False,
            fail_after_seconds=0,
            result_payload=None,
            result_size_bytes=0,
            json_output=True,
        )

    assert exc_info.value.exit_code == 2
    assert "--callback-event must include the expected terminal job status" in str(exc_info.value)


def test_example_lifecycle_probe_local_callback_uses_single_timeout_budget(monkeypatch, capsys):
    context = service_runtime.RuntimeContext(
        app_env={
            "CALLBACK_SIGNING_SECRET": "test-callback-signing-secret",
            "DISABLE_HTTP_AUTH_HEADER": "true",
            "DISABLE_CALLER_ID_HEADER": "true",
        },
        summary={
            "api_url": "http://127.0.0.1:8100",
            "jobs_url": "http://127.0.0.1:8100/api/v1/ai-jobs/jobs",
            "ready": True,
        },
    )
    timeout_values: dict[str, float] = {}
    monotonic_values = iter([100.0, 101.25, 105.5, 108.0])

    class FakeReceiver:
        url = "http://127.0.0.1:19000/callback"

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return None

        def wait_for_event(self, _expectation, *, timeout_seconds):
            timeout_values["receiver"] = timeout_seconds
            return {
                "body": {
                    "event": "job.succeeded",
                    "job": {"job_id": "probe-job-budget", "job_status": "succeeded"},
                },
                "signature": {"checked": True, "valid": True},
            }

        def snapshot(self):
            return [{"body": {"event": "job.succeeded"}}]

    def fake_poll_job_envelope(**kwargs):
        timeout_values["job"] = kwargs["timeout_seconds"]
        return {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "probe-job-budget",
                    "job_status": "succeeded",
                    "job_result": {
                        "probe_id": "probe",
                        "message": "hello",
                        "worker_observed_at": "2026-08-28T00:00:00+00:00",
                    },
                    "callback": {"status": "pending", "attempt": 0},
                }
            },
        }

    def fake_poll_callback_envelope(**kwargs):
        timeout_values["callback"] = kwargs["timeout_seconds"]
        return {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "probe-job-budget",
                    "job_status": "succeeded",
                    "callback": {"status": "delivered", "attempt": 1},
                }
            },
        }

    monkeypatch.setattr(job_runtime, "resolve_job_context", lambda **_kwargs: context)
    monkeypatch.setattr(http_runtime, "request_json", lambda *_args, **_kwargs: {
        "code": "0",
        "data": {"job": {"job_id": "probe-job-budget", "job_status": "queued"}},
    })
    monkeypatch.setattr(job_runtime, "poll_job_envelope", fake_poll_job_envelope)
    monkeypatch.setattr(example_lifecycle_probe, "poll_callback_envelope", fake_poll_callback_envelope)
    monkeypatch.setattr(example_lifecycle_probe, "local_callback_server", lambda _context: FakeReceiver())
    monkeypatch.setattr(example_lifecycle_probe.time, "monotonic", lambda: next(monotonic_values))

    example_lifecycle_probe.run(
        job_options=_job_options(),
        callback_options=_callback_options(local_callback=True),
        api_url=None,
        env_file=None,
        allow_remote_api=False,
        service_api_key=None,
        caller_id="smoke-cli",
        timeout_seconds=10,
        poll_interval_seconds=0.1,
        probe_id="probe",
        message="hello",
        sleep_seconds=0,
        fail=False,
        fail_after_seconds=0,
        result_payload=None,
        result_size_bytes=0,
        json_output=True,
    )

    json.loads(capsys.readouterr().out)
    assert timeout_values == {
        "job": pytest.approx(8.75),
        "callback": pytest.approx(4.5),
        "receiver": pytest.approx(2.0),
    }


def test_example_lifecycle_probe_forced_failure_requires_expected_error_reason():
    with pytest.raises(example_lifecycle_probe.FlowError) as exc_info:
        example_lifecycle_probe.assert_terminal_job(
            {
                "job_id": "probe-job-3",
                "job_status": "failed",
                "job_error": {"reason": "JOB_ATTEMPT_TIMEOUT", "details": {"fault": "forced_failure"}},
            },
            expected="failed",
            probe_id="probe",
            message="hello",
            forced_failure=True,
        )

    assert exc_info.value.exit_code == 1
    assert "reason mismatch" in str(exc_info.value)


def test_example_lifecycle_probe_forced_failure_requires_expected_fault():
    with pytest.raises(example_lifecycle_probe.FlowError) as exc_info:
        example_lifecycle_probe.assert_terminal_job(
            {
                "job_id": "probe-job-4",
                "job_status": "failed",
                "job_error": {
                    "reason": example_lifecycle_probe.FORCED_FAILURE_REASON,
                    "details": {"fault": "other"},
                },
            },
            expected="failed",
            probe_id="probe",
            message="hello",
            forced_failure=True,
        )

    assert exc_info.value.exit_code == 1
    assert "fault mismatch" in str(exc_info.value)


def test_example_lifecycle_probe_build_payload_rejects_invalid_wait_parameters():
    with pytest.raises(example_lifecycle_probe.FlowError, match="fail-after-seconds requires --fail"):
        example_lifecycle_probe.build_payload(
            probe_id="probe",
            message="hello",
            sleep_seconds=0,
            fail=False,
            fail_after_seconds=1,
            result_payload=None,
            result_size_bytes=0,
            callback_url=None,
            callback_event="both",
            client_request_id=None,
        )
    with pytest.raises(example_lifecycle_probe.FlowError, match=r"sleep-seconds \+ fail-after-seconds must be <= 600"):
        example_lifecycle_probe.build_payload(
            probe_id="probe",
            message="hello",
            sleep_seconds=500,
            fail=True,
            fail_after_seconds=101,
            result_payload=None,
            result_size_bytes=0,
            callback_url=None,
            callback_event="both",
            client_request_id=None,
        )


def test_smoke_failed_terminal_job_uses_standard_exit_code_1(monkeypatch):
    context = service_runtime.RuntimeContext(
        app_env={"DISABLE_HTTP_AUTH_HEADER": "true", "DISABLE_CALLER_ID_HEADER": "true"},
        summary={"jobs_url": "http://127.0.0.1:8100/jobs", "ready": True},
    )
    responses = iter(
        [
            {"code": "0", "data": {"job": {"job_id": "job-failed", "job_status": "queued"}}},
            {"code": "0", "data": {"job": {"job_id": "job-failed", "job_status": "failed", "job_type": "job_real_llm_echo"}}},
            {"code": "0", "data": {"billing": {"status": "not_billable"}}},
        ]
    )

    monkeypatch.setattr(job_runtime, "resolve_job_context", lambda **kwargs: context)
    monkeypatch.setattr(http_runtime, "request_json", lambda *args, **kwargs: next(responses))

    with pytest.raises(FlowError) as exc_info:
        llm_job_billing.run(
            confirm_cost=True,
            job_type="job_real_llm_echo",
            api_url=None,
            model_id="gpt-test",
            input_text="hello",
            instruction="reply",
            second_instruction=None,
            caller_id="smoke-cli",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            client_request_id="client-1",
            json_output=True,
            allow_remote_api=False,
            service_api_key=None,
            env_file=None,
        )

    assert exc_info.value.exit_code == 1


def test_llm_job_billing_cli_accepts_remote_api_and_auth_options(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(llm_job_billing, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "--allow-remote-api",
            "--base-url",
            "http://test-cms-poster-title.epubgame.com",
            "--env-file",
            "env_test/.env",
            "--service-api-key",
            "test-token",
            "--caller-id",
            "default",
            "llm-job-billing",
            "--confirm-cost",
        ],
    )

    assert result.exit_code == 0
    assert captured["allow_remote_api"] is True
    assert captured["api_url"] == "http://test-cms-poster-title.epubgame.com"
    assert captured["env_file"] == "env_test/.env"
    assert captured["service_api_key"] == "test-token"
    assert captured["caller_id"] == "default"


def test_llm_job_double_billing_cli_accepts_remote_api_and_auth_options(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(llm_job_billing, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "--allow-remote-api",
            "--base-url",
            "http://test-cms-poster-title.epubgame.com",
            "--env-file",
            "env_test/.env",
            "--service-api-key",
            "test-token",
            "--caller-id",
            "default",
            "llm-job-double-billing",
            "--confirm-cost",
        ],
    )

    assert result.exit_code == 0
    assert captured["allow_remote_api"] is True
    assert captured["api_url"] == "http://test-cms-poster-title.epubgame.com"
    assert captured["env_file"] == "env_test/.env"
    assert captured["service_api_key"] == "test-token"
    assert captured["caller_id"] == "default"


def test_poster_title_image_cli_accepts_remote_api_and_auth_options(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(poster_title_image, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "--allow-remote-api",
            "--base-url",
            "http://test-cms-poster-title.epubgame.com",
            "--env-file",
            "env_test/.env",
            "--service-api-key",
            "test-token",
            "--caller-id",
            "default",
            "poster-title-image",
            "--confirm-cost",
            "--confirm-upload",
            "--reference",
            ".data/title/标题2.png",
            "--language",
            "es",
            "--title-text",
            "Cuando el amor se alejo",
        ],
    )

    assert result.exit_code == 0
    assert captured["allow_remote_api"] is True
    assert captured["api_url"] == "http://test-cms-poster-title.epubgame.com"
    assert captured["env_file"] == "env_test/.env"
    assert captured["service_api_key"] == "test-token"
    assert captured["caller_id"] == "default"


def test_poster_title_image_cli_accepts_caller_id_option(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(poster_title_image, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "--caller-id",
            "smoke-caller",
            "poster-title-image",
            "--confirm-cost",
            "--reference",
            ".data/title/标题2.png",
        ],
    )

    assert result.exit_code == 0
    assert captured["caller_id"] == "smoke-caller"


def test_oss_upload_image_cli_accepts_env_file(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(oss_image_upload, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "--env-file",
            "env_test/.env",
            "oss-upload-image",
            "--confirm-upload",
            "--image",
            ".data/title/标题2.png",
        ],
    )

    assert result.exit_code == 0
    assert captured["env_file"] == "env_test/.env"
    assert captured["image"] == ".data/title/标题2.png"
    assert captured["output_mode"] == "table"


def test_oss_upload_image_cli_supports_url_ref_json_output(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(oss_image_upload, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "oss-upload-image",
            "--confirm-upload",
            "--image",
            ".data/title/标题2.png",
            "--json-ref-only",
        ],
    )

    assert result.exit_code == 0
    assert captured["output_mode"] == "url-ref-json"


def test_oss_upload_image_cli_supports_poster_args_output(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(oss_image_upload, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "oss-upload-image",
            "--confirm-upload",
            "--image",
            ".data/title/标题2.png",
            "--emit-poster-args",
        ],
    )

    assert result.exit_code == 0
    assert captured["output_mode"] == "poster-args"


def test_oss_upload_image_cli_requires_confirm_upload():
    result = runner.invoke(app, ["oss-upload-image", "--image", ".data/title/英语.png"])

    assert result.exit_code == 2
    assert "OSS image upload requires --confirm-upload" in result.stderr


def test_oss_upload_image_cli_requires_explicit_image():
    result = runner.invoke(app, ["oss-upload-image", "--confirm-upload"])

    assert result.exit_code == 2
    assert "OSS image upload requires --image" in result.stderr


def test_audio_stem_separation_build_payload_cli_forwards_options(monkeypatch):
    captured_build = {}
    captured_write = {}

    def fake_build_payload(**kwargs):
        captured_build.update(kwargs)
        return {"job_type": "audio_stem_separation"}, {"provider": "local"}

    def fake_write_or_print_payload(payload, *, output):
        captured_write["payload"] = payload
        captured_write["output"] = output

    monkeypatch.setattr(audio_stem_separation, "build_payload", fake_build_payload)
    monkeypatch.setattr(audio_stem_separation, "write_or_print_payload", fake_write_or_print_payload)

    result = runner.invoke(
        app,
        [
            "--env-file",
            "env_test/.env",
            "audio-stem-separation",
            "build-payload",
            "--input-file",
            ".data/misc/input.wav",
            "--job-type",
            "audio_stem_separation_triton",
            "--max-duration-seconds",
            "12.5",
            "--client-request-id",
            "audio-client-1",
            "--confirm-upload",
            "--key-prefix",
            "smoke/audio/input",
            "--signed-url-expires-seconds",
            "600",
            "--output",
            ".run/audio-payload.json",
        ],
    )

    assert result.exit_code == 0
    assert captured_build == {
        "env_file": "env_test/.env",
        "job_type": "audio_stem_separation_triton",
        "input_file": ".data/misc/input.wav",
        "input_url_ref_json": None,
        "input_public_url": None,
        "input_internal_url": None,
        "input_sha256": None,
        "max_duration_seconds": 12.5,
        "client_request_id": "audio-client-1",
        "confirm_upload": True,
        "key_prefix": "smoke/audio/input",
        "signed_url_expires_seconds": 600,
    }
    assert captured_write == {
        "payload": {"job_type": "audio_stem_separation"},
        "output": ".run/audio-payload.json",
    }


def test_audio_stem_separation_run_cli_forwards_payload_file_options(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(audio_stem_separation, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "--base-url",
            "http://127.0.0.1:18200",
            "--env-file",
            "env_test/.env",
            "--allow-remote-api",
            "--service-api-key",
            "test-token",
            "--caller-id",
            "default",
            "--timeout",
            "10",
            "--poll-interval",
            "0.5",
            "--output-dir",
            ".run/audio-stems",
            "--json",
            "audio-stem-separation",
            "run",
            "--confirm-run",
            "--confirm-upload",
            "--client-request-id",
            "audio-client-2",
            "--job-type",
            "audio_stem_separation_triton",
            "--payload-file",
            ".run/audio-payload.json",
            "--download-outputs",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "confirm_run": True,
        "confirm_upload": True,
        "api_url": "http://127.0.0.1:18200",
        "env_file": "env_test/.env",
        "allow_remote_api": True,
        "service_api_key": "test-token",
        "caller_id": "default",
        "timeout_seconds": 10,
        "poll_interval_seconds": 0.5,
        "job_type": "audio_stem_separation_triton",
        "client_request_id": "audio-client-2",
        "payload_file": ".run/audio-payload.json",
        "input_file": None,
        "input_url_ref_json": None,
        "input_public_url": None,
        "input_internal_url": None,
        "input_sha256": None,
        "max_duration_seconds": None,
        "key_prefix": None,
        "signed_url_expires_seconds": 3600,
        "download_outputs": True,
        "output_dir": ".run/audio-stems",
        "json_output": True,
    }


def test_adapter_image_probe_cli_requires_confirm_cost():
    result = runner.invoke(app, ["adapter-image-probe"])

    assert result.exit_code == 2
    assert "adapter image probe requires --confirm-cost" in result.stderr


def test_adapter_image_probe_cli_accepts_adapter_options(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(adapter_image_probe, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "--env-file",
            "env_test/.env",
            "--timeout",
            "45",
            "--json",
            "adapter-image-probe",
            "--confirm-cost",
            "--models-config",
            "app/business_packages/poster_title_image/models.yaml",
            "--prompt",
            "draw a title",
            "--reference",
            ".data/title/reference.png",
            "--reference-content-type",
            "image/png",
            "--provider-model",
            "gpt-image-2",
            "--response-model",
            "gpt-5.5",
            "--size",
            "1024x1024",
            "--quality",
            "low",
            "--background",
            "auto",
            "--output-format",
            "png",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "confirm_cost": True,
        "env_file": "env_test/.env",
        "models_config": "app/business_packages/poster_title_image/models.yaml",
        "prompt": "draw a title",
        "reference_image": ".data/title/reference.png",
        "reference_content_type": "image/png",
        "provider_model": "gpt-image-2",
        "response_model": "gpt-5.5",
        "size": "1024x1024",
        "quality": "low",
        "background": "auto",
        "output_format": "png",
        "timeout_seconds": 45,
        "json_output": True,
    }


def test_adapter_image_probe_collects_adapter_errors(monkeypatch, capsys, tmp_path):
    calls = []
    config_path = tmp_path / "poster-models.yaml"
    config_path.write_text(
        "\n".join(
            [
                "version: test",
                "job_type: poster_title_image",
                "model_slots:",
                "  generation:",
                "    visibility: public",
                "    default_model_id: gpt-image-from-config",
                "    allowed_model_ids:",
                "      - gpt-image-from-config",
                "    required_capabilities:",
                "      - image_generation",
                "  style_probe:",
                "    visibility: internal",
                "    default_model_id: gpt-response-from-config",
                "    allowed_model_ids:",
                "      - gpt-response-from-config",
                "    required_capabilities:",
                "      - multimodal_text_generation",
            ]
        ),
        encoding="utf-8",
    )

    async def fake_run_adapter(adapter_name, _request):
        calls.append(adapter_name)
        if adapter_name == "openai_responses":
            raise RuntimeError("responses failed")
        return adapter_image_probe._result_payload(
            adapter_name,
            ImageGenerationResult(images=[b"png"], usage={"provider_usage": {"total_tokens": 8}}),
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        adapter_image_probe,
        "_provider_model_for_model_id",
        lambda model_id: {
            "gpt-image-from-config": "provider-image-from-config",
            "gpt-response-from-config": "provider-response-from-config",
        }[model_id],
    )
    monkeypatch.setattr(adapter_image_probe, "_generation_adapter_for_model_id", lambda _model_id: "openai_responses")
    monkeypatch.setattr(adapter_image_probe, "_run_adapter", fake_run_adapter)

    with pytest.raises(adapter_image_probe.FlowError, match="one or more image adapters failed") as exc_info:
        adapter_image_probe.run(
            confirm_cost=True,
            env_file=None,
            models_config=str(config_path),
            prompt="draw a title",
            reference_image=None,
            reference_content_type=None,
            provider_model=None,
            response_model=None,
            size="1024x1024",
            quality="low",
            background="auto",
            output_format="png",
            timeout_seconds=30,
            json_output=True,
        )

    assert exc_info.value.exit_code == 4
    assert calls == ["openai_responses", "openai_images"]
    payload = json.loads(capsys.readouterr().out)
    assert [result["status"] for result in payload["results"]] == ["failed", "succeeded"]
    assert payload["summary"]["configured_image_adapter"] == "openai_responses"
    assert payload["summary"]["provider_model_id"] == "gpt-image-from-config"
    assert payload["summary"]["provider_model"] == "provider-image-from-config"
    assert payload["summary"]["response_model_id"] == "gpt-response-from-config"
    assert payload["summary"]["response_model"] == "provider-response-from-config"
    assert payload["results"][0]["error"]["message"] == "responses failed"
    assert payload["results"][1]["usage"] == {"provider_usage": {"total_tokens": 8}}


def test_oss_image_upload_accepts_standard_jpeg_mime_and_rejects_jpg_alias(tmp_path):
    image_path = tmp_path / "reference.jpg"
    image_path.write_bytes(_jpeg_bytes())

    assert oss_image_upload.image_content_type(image_path, None) == "image/jpeg"

    with pytest.raises(FlowError, match="image/jpg"):
        oss_image_upload.image_content_type(image_path, "image/jpg")


def test_smoke_builds_job_payload_for_real_llm_job():
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


def test_smoke_builds_poster_title_image_payload():
    reference = {
        "public_url": "https://local-dev.oss-local.aliyuncs.com/reference/title.png",
        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/reference/title.png",
        "content_type": "image/png",
        "sha256": "a" * 64,
    }
    item = {
        "item_id": "es",
        "language": "es",
        "title_text": "Cuando el amor se alejo",
        "model_options": {
            "size": "auto",
            "quality": "high",
            "draw_count": 1,
            "background": "transparent",
            "output_format": "png",
        },
        "reference_image": reference,
    }

    payload = poster_title_image.build_job_payload(
        items=[item],
        client_request_id="poster-client-1",
    )

    assert payload["client_request_id"] == "poster-client-1"
    assert payload["job_type"] == "poster_title_image"
    item = payload["job_params"]["items"][0]
    assert "model_id" not in item
    assert item["model_options"] == {
        "size": "auto",
        "quality": "high",
        "draw_count": 1,
        "background": "transparent",
        "output_format": "png",
    }
    assert item["reference_image"] == reference
    assert "prompt_overrides" not in item


def test_smoke_builds_poster_title_image_payload_with_caller_model_id():
    reference = {
        "public_url": "https://local-dev.oss-local.aliyuncs.com/reference/title.png",
        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/reference/title.png",
        "content_type": "image/png",
        "sha256": "a" * 64,
    }
    item = {
        "item_id": "es",
        "language": "es",
        "title_text": "Cuando el amor se alejo",
        "model_id": "gpt-image-custom",
        "model_options": {
            "size": "auto",
            "quality": "high",
            "draw_count": 1,
            "background": "transparent",
            "output_format": "png",
        },
        "reference_image": reference,
    }

    payload = poster_title_image.build_job_payload(
        items=[item],
        client_request_id="poster-client-2",
    )

    assert payload["job_params"]["items"][0]["model_id"] == "gpt-image-custom"


def test_smoke_builds_audio_stem_separation_payload():
    input_audio = {
        "public_url": "https://local-dev.oss-local.aliyuncs.com/audio/input.wav",
        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/audio/input.wav",
        "content_type": "audio/wav",
        "sha256": "a" * 64,
    }

    payload = audio_stem_separation.build_job_payload(
        input_audio=input_audio,
        job_type="audio_stem_separation",
        client_request_id="audio-client-1",
        max_duration_seconds=30.5,
    )

    assert payload == {
        "client_request_id": "audio-client-1",
        "job_type": "audio_stem_separation",
        "job_params": {
            "input_audio": input_audio,
            "max_duration_seconds": 30.5,
        },
        "metadata": {
            "source": "scripts/smoke.sh audio-stem-separation",
            "job_type": "audio_stem_separation",
        },
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }


def test_smoke_builds_audio_stem_separation_payload_with_mp3_ref():
    input_audio = {
        "public_url": "https://local-dev.oss-local.aliyuncs.com/audio/input.mp3",
        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/audio/input.mp3",
        "content_type": "audio/mpeg",
        "sha256": "a" * 64,
    }

    payload = audio_stem_separation.build_job_payload(
        input_audio=input_audio,
        job_type="audio_stem_separation",
        client_request_id="audio-client-mp3",
        max_duration_seconds=30.5,
    )

    assert payload["job_params"]["input_audio"]["content_type"] == "audio/mpeg"


def test_smoke_builds_audio_stem_separation_triton_payload():
    input_audio = {
        "public_url": "https://local-dev.oss-local.aliyuncs.com/audio/input.wav",
        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/audio/input.wav",
        "content_type": "audio/wav",
        "sha256": "a" * 64,
    }

    payload = audio_stem_separation.build_job_payload(
        input_audio=input_audio,
        job_type="audio_stem_separation_triton",
        client_request_id="audio-triton-client-1",
        max_duration_seconds=30.5,
    )

    assert payload["client_request_id"] == "audio-triton-client-1"
    assert payload["job_type"] == "audio_stem_separation_triton"
    assert payload["job_params"] == {
        "input_audio": input_audio,
        "max_duration_seconds": 30.5,
    }
    assert payload["metadata"] == {
        "source": "scripts/smoke.sh audio-stem-separation",
        "job_type": "audio_stem_separation_triton",
    }


def test_audio_stem_separation_rejects_mixed_input_sources():
    with pytest.raises(audio_stem_separation.FlowError, match="exactly one"):
        audio_stem_separation.resolve_input_audio(
            input_file="input.wav",
            input_url_ref_json=None,
            input_public_url="https://local-dev.oss-local.aliyuncs.com/audio/input.wav",
            input_internal_url="https://local-dev.oss-local-internal.aliyuncs.com/audio/input.wav",
            input_sha256="a" * 64,
            app_env={"STORAGE_BACKEND": "local"},
            max_duration_seconds=None,
            confirm_upload=False,
            key_prefix=None,
            signed_url_expires_seconds=3600,
        )


def test_poster_title_image_run_supports_items_json_with_multiple_references(tmp_path, monkeypatch, capsys):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DISABLE_HTTP_AUTH_HEADER=true",
                "DISABLE_CALLER_ID_HEADER=true",
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket-a",
                "OSS_REGION=cn-hangzhou",
                "OSS_ACCESS_KEY_ID=id",
                "OSS_ACCESS_KEY_SECRET=secret",
                "OSS_PROJECT_ROOT=project-a",
            ]
        ),
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")
    reference_path = tmp_path / "title-fr.png"
    reference_path.write_bytes(_transparent_png_bytes())
    items_path = tmp_path / "poster-items.json"
    existing_ref = {
        "public_url": "https://local-dev.oss-local.aliyuncs.com/reference/title-es.png",
        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/reference/title-es.png",
        "content_type": "image/png",
        "sha256": "a" * 64,
    }
    uploaded_ref = {
        "public_url": "https://local-dev.oss-local.aliyuncs.com/reference/title-fr.png",
        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/reference/title-fr.png",
        "content_type": "image/png",
        "sha256": "b" * 64,
    }
    uploaded_image = {
        "provider": "aliyun_oss",
        "bucket": "bucket-a",
        "region": "cn-hangzhou",
        "key": "project-a/reference/title-fr.png",
        "url_ref": uploaded_ref,
    }
    items_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "item_id": "es",
                        "language": "es",
                        "title_text": "Cuando el amor se alejo",
                        "reference": existing_ref,
                    },
                    {
                        "item_id": "fr",
                        "language": "fr",
                        "title_text": "Quand l'amour s'eloigne",
                        "draw_count": 2,
                        "reference": {"image": str(reference_path), "content_type": "image/png"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    upload_calls = []
    cleanup_calls = []
    http_calls = []

    def fake_upload_image(**kwargs):
        upload_calls.append(kwargs)
        return uploaded_image

    def fake_request_json(url, *, method, headers, payload=None):
        http_calls.append({"url": url, "method": method, "headers": headers, "payload": payload})
        if method == "POST":
            return {"code": "0", "data": {"job": {"job_id": "poster-job-items"}}}
        return {
            "code": "0",
            "data": {
                "billing": {
                    "status": "succeeded",
                    "currency": "USD",
                    "total_cost_amount": "0.00",
                    "usage_units": [],
                    "pricing_refs": [],
                    "ai_call_count": 0,
                    "billable_call_count": 0,
                    "failed_call_count": 0,
                }
            },
        }

    monkeypatch.setattr(oss_image_upload, "upload_image", fake_upload_image)
    monkeypatch.setattr(oss_image_upload, "delete_uploaded_image", lambda **kwargs: cleanup_calls.append(kwargs))
    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    monkeypatch.setattr(
            job_runtime,
            "poll_job_envelope",
        lambda **_kwargs: {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "poster-job-items",
                    "job_status": "succeeded",
                    "job_type": "poster_title_image",
                    "job_result": {"items": []},
                }
            },
        },
    )

    poster_title_image.run(
        confirm_cost=True,
        confirm_upload=True,
        api_url=None,
        items_json=str(items_path),
        reference_image=poster_title_image.DEFAULT_REFERENCE_IMAGE,
        reference_url_ref_json=None,
        reference_public_url=None,
        reference_internal_url=None,
        reference_sha256=None,
        reference_content_type="image/png",
        item_id="ignored",
        language="es",
        title_text="ignored",
        size="auto",
        quality="high",
        draw_count=1,
        caller_id="caller-1",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        client_request_id="poster-client-items",
        json_output=True,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["job_id"] == "poster-job-items"
    assert upload_calls[0]["image"] == str(reference_path)
    assert cleanup_calls == [{"upload_result": uploaded_image, "app_env": upload_calls[0]["app_env"]}]
    request_items = http_calls[0]["payload"]["job_params"]["items"]
    assert [item["item_id"] for item in request_items] == ["es", "fr"]
    assert [item["language"] for item in request_items] == ["es", "fr"]
    assert request_items[0]["reference_image"] == existing_ref
    assert request_items[1]["reference_image"] == uploaded_ref
    assert request_items[1]["model_options"]["draw_count"] == 2


def test_poster_title_image_items_json_rejects_explicit_invalid_values(monkeypatch):
    clear_storage_env(monkeypatch)
    reference = {
        "public_url": "https://local-dev.oss-local.aliyuncs.com/reference/title.png",
        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/reference/title.png",
        "content_type": "image/png",
        "sha256": "a" * 64,
    }
    base_item = {
        "item_id": "es",
        "language": "es",
        "title_text": "Cuando el amor se alejo",
        "reference": reference,
    }

    with pytest.raises(poster_title_image.FlowError, match="draw_count must be between 1 and 4"):
        poster_title_image.build_items_from_json(
            [{**base_item, "draw_count": 0}],
            app_env={"STORAGE_BACKEND": "local"},
            confirm_upload=False,
            size="auto",
            quality="high",
            draw_count=1,
        )

    with pytest.raises(poster_title_image.FlowError, match="model_id"):
        poster_title_image.build_items_from_json(
            [{**base_item, "model_id": ""}],
            app_env={"STORAGE_BACKEND": "local"},
            confirm_upload=False,
            size="auto",
            quality="high",
            draw_count=1,
        )


def test_oss_image_upload_builds_url_ref_with_fake_repository(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(oss_image_upload, "ROOT_DIR", tmp_path)
    source = tmp_path / "reference.png"
    source.write_bytes(b"png-reference")
    calls = []

    class FakeRepository:
        provider = "aliyun_oss"
        config = AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
            key_prefix="project-a",
        )

        def put_bytes(self, key, data, *, content_type, content_disposition=None):
            assert content_disposition is None
            object_key = f"project-a/{key.strip('/')}" if not key.startswith("project-a/") else key
            calls.append({"key": object_key, "data": data, "content_type": content_type})
            return PutObjectResult(
                provider="aliyun_oss",
                bucket="bucket-a",
                region="cn-hangzhou",
                key=object_key,
                content_type=content_type,
                size_bytes=len(data),
                sha256=oss_image_upload.bare_sha256(data),
            )

        def signed_get_url(self, ref, *, expires_seconds):
            assert ref.key == "project-a/inputs/reference.png"
            assert expires_seconds == 1800
            return "https://signed.example.com/project-a/inputs/reference.png?Signature=sig"

    result = oss_image_upload.upload_image(
        image="reference.png",
        content_type=None,
        app_env={"OSS_OUTPUT_PREFIX": "outputs", "OSS_PUBLIC_ENDPOINT": "aigc-datas.epubgame.com"},
        key="inputs/reference.png",
        signed_url_expires_seconds=1800,
        repository=FakeRepository(),
    )

    assert calls == [
        {
            "key": "project-a/inputs/reference.png",
            "data": b"png-reference",
            "content_type": "image/png",
        }
    ]
    assert result["bucket"] == "bucket-a"
    assert result["region"] == "cn-hangzhou"
    assert result["key"] == "project-a/inputs/reference.png"
    assert result["sha256"] == oss_image_upload.bare_sha256(b"png-reference")
    assert result["signed_url"] == "https://signed.example.com/project-a/inputs/reference.png?Signature=sig"
    assert result["signed_url_expires_seconds"] == 1800
    assert result["url_ref"] == {
        "public_url": "https://aigc-datas.epubgame.com/project-a/inputs/reference.png",
        "internal_url": "https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/project-a/inputs/reference.png",
        "content_type": "image/png",
        "sha256": oss_image_upload.bare_sha256(b"png-reference"),
    }


def test_oss_image_upload_cleanup_deletes_object_ref(monkeypatch):
    clear_storage_env(monkeypatch)
    deleted_refs = []

    class FakeRepository:
        provider = "aliyun_oss"
        config = AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
            key_prefix="project-a",
        )

        def delete(self, ref):
            deleted_refs.append(ref)

    oss_image_upload.delete_uploaded_image(
        upload_result={
            "provider": "aliyun_oss",
            "bucket": "bucket-a",
            "region": "cn-hangzhou",
            "key": "project-a/inputs/reference.png",
        },
        app_env={},
        repository=FakeRepository(),
    )

    assert len(deleted_refs) == 1
    assert deleted_refs[0].provider == "aliyun_oss"
    assert deleted_refs[0].bucket == "bucket-a"
    assert deleted_refs[0].region == "cn-hangzhou"
    assert deleted_refs[0].key == "project-a/inputs/reference.png"


def test_smoke_builds_double_job_payload():
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


def test_smoke_headers_require_service_key_when_auth_enabled(monkeypatch):
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    with pytest.raises(FlowError) as exc:
        service_runtime.build_headers({}, caller_id="caller-1")

    assert exc.value.exit_code == 2
    assert "SERVICE_API_KEY is required" in str(exc.value)


def test_smoke_headers_use_auth_and_caller_id(monkeypatch):
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    headers = service_runtime.build_headers(
        {
            "SERVICE_API_KEY": "secret",
            "DISABLE_HTTP_AUTH_HEADER": "false",
            "DISABLE_CALLER_ID_HEADER": "false",
        },
        caller_id="caller-1",
    )

    assert headers["Authorization"] == "Bearer secret"
    assert headers["X-AI-Service-Caller-ID"] == "caller-1"


def test_smoke_headers_use_explicit_service_key(monkeypatch):
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    headers = service_runtime.build_headers(
        {
            "SERVICE_API_KEY": "env-secret",
            "DISABLE_HTTP_AUTH_HEADER": "false",
            "DISABLE_CALLER_ID_HEADER": "false",
        },
        caller_id="caller-1",
        service_api_key="cli-secret",
    )

    assert headers["Authorization"] == "Bearer cli-secret"
    assert headers["X-AI-Service-Caller-ID"] == "caller-1"


def test_smoke_load_app_env_uses_explicit_file(tmp_path, monkeypatch):
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.delenv("ENV_FILE", raising=False)
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text("API_URL=http://test.example.com\nSERVICE_API_KEY=file-token\n", encoding="utf-8")

    values = env_runtime.load_app_env("env_test/.env")

    assert values["API_URL"] == "http://test.example.com"
    assert values["SERVICE_API_KEY"] == "file-token"


def test_smoke_load_app_env_rejects_missing_explicit_file(tmp_path, monkeypatch):
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.delenv("ENV_FILE", raising=False)

    with pytest.raises(FlowError) as exc:
        env_runtime.load_app_env("env_test/.env")

    assert exc.value.exit_code == 2
    assert "env file not found" in str(exc.value)


def test_smoke_load_app_env_uses_env_file_variable(tmp_path, monkeypatch):
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    env_dir = tmp_path / "config"
    env_dir.mkdir()
    (tmp_path / ".env").write_text("API_URL=http://root.example.com\n", encoding="utf-8")
    (env_dir / "smoke.env").write_text("API_URL=http://env-file.example.com\n", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", "config/smoke.env")

    values = env_runtime.load_app_env()

    assert values["API_URL"] == "http://env-file.example.com"


def test_smoke_load_app_env_rejects_missing_env_file_variable(tmp_path, monkeypatch):
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.setenv("ENV_FILE", "missing/.env")

    with pytest.raises(FlowError) as exc:
        env_runtime.load_app_env()

    assert exc.value.exit_code == 2
    assert "env file not found" in str(exc.value)


def test_smoke_env_value_prefers_runtime_env(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "runtime-token")

    value = env_runtime.env_value("SERVICE_API_KEY", {"SERVICE_API_KEY": "file-token"})

    assert value == "runtime-token"
    assert env_runtime.env_source("SERVICE_API_KEY", {"SERVICE_API_KEY": "file-token"}) == "runtime_env"


def test_smoke_env_value_prefers_env_file_when_profile_override_is_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "runtime-token")
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text("SERVICE_API_KEY=file-token\n", encoding="utf-8")

    app_env = env_runtime.load_app_env("env_test/.env", root_dir=tmp_path)

    value = env_runtime.env_value("SERVICE_API_KEY", app_env)

    assert value == "file-token"
    assert env_runtime.env_source("SERVICE_API_KEY", app_env) == "env_file"


def test_smoke_env_file_profile_falls_back_to_runtime_for_missing_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "runtime-token")
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text("API_URL=http://env-file.example.com\n", encoding="utf-8")

    app_env = env_runtime.load_app_env("env_test/.env", root_dir=tmp_path)

    value = env_runtime.env_value("SERVICE_API_KEY", app_env)

    assert value == "runtime-token"
    assert env_runtime.env_source("SERVICE_API_KEY", app_env) == "runtime_env"


def test_smoke_resolves_api_url_from_root_env(monkeypatch):
    clear_api_env(monkeypatch)

    api_url = service_runtime.resolved_api_url(None, {"API_HOST": "127.0.0.1", "API_PORT": "18200"})

    assert api_url == "http://127.0.0.1:18200"


def test_smoke_rejects_non_local_api_url():
    with pytest.raises(FlowError) as exc:
        service_runtime.resolved_api_url("https://api.example.com", {})

    assert exc.value.exit_code == 2
    assert "only targets local API URLs" in str(exc.value)


def test_smoke_accepts_remote_api_url_when_explicitly_allowed():
    api_url = service_runtime.resolved_api_url(
        "https://api.example.com",
        {},
        allow_remote_api=True,
    )

    assert api_url == "https://api.example.com"


@pytest.mark.parametrize("api_url", ["https://127.example.com", "https://127.0.0.1.nip.io"])
def test_smoke_rejects_loopback_prefix_hostnames(api_url):
    with pytest.raises(FlowError) as exc:
        service_runtime.resolved_api_url(api_url, {})

    assert exc.value.exit_code == 2
    assert "only targets local API URLs" in str(exc.value)


def test_smoke_accepts_loopback_ip_url():
    api_url = service_runtime.resolved_api_url("http://127.0.0.1:18200", {})

    assert api_url == "http://127.0.0.1:18200"


def test_smoke_run_uses_http_job_and_billing_flow(tmp_path, monkeypatch):
    clear_api_env(monkeypatch)
    monkeypatch.delenv("MODEL_CONFIG_PATH", raising=False)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    write_smoke_model_catalog(tmp_path, default_model_id="gpt-5.4-mini")
    app_env = tmp_path / ".env"
    app_env.write_text(
        "DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=false\nMODEL_CONFIG_PATH=models.yaml\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")

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

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    monkeypatch.setattr(job_runtime, "poll_job_envelope", fake_poll_job_envelope)

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


def test_smoke_run_uses_env_file_for_remote_api_and_service_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_CONFIG_PATH", raising=False)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    write_smoke_model_catalog(tmp_path, default_model_id="gpt-5.4-mini")
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "\n".join(
            [
                "API_URL=http://test-cms-poster-title.epubgame.com",
                "SERVICE_API_KEY=file-secret",
                "DISABLE_HTTP_AUTH_HEADER=false",
                "DISABLE_CALLER_ID_HEADER=false",
                "MODEL_CONFIG_PATH=models.yaml",
            ]
        ),
        encoding="utf-8",
    )

    calls = []

    def fake_request_json(url, *, method, headers, payload=None, timeout_seconds=10):
        calls.append({"url": url, "method": method, "headers": headers, "payload": payload})
        if method == "POST":
            return {"code": "0", "data": {"job": {"job_id": "job-remote", "job_status": "queued"}}}
        return {
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

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    monkeypatch.setattr(
            job_runtime,
            "poll_job_envelope",
        lambda **kwargs: {
            "code": "0",
            "data": {"job": {"job_id": "job-remote", "job_status": "succeeded", "job_type": "job_real_llm_echo"}},
        },
    )

    llm_job_billing.run(
        confirm_cost=True,
        job_type="job_real_llm_echo",
        api_url=None,
        model_id=None,
        input_text="hello",
        instruction="reply once",
        second_instruction=None,
        caller_id="default",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        client_request_id="client-remote",
        json_output=True,
        allow_remote_api=True,
        service_api_key=None,
        env_file="env_test/.env",
    )

    assert calls[0]["url"] == "http://test-cms-poster-title.epubgame.com/api/v1/ai-jobs/jobs"
    assert calls[0]["headers"]["Authorization"] == "Bearer file-secret"
    assert calls[0]["headers"]["X-AI-Service-Caller-ID"] == "default"
    assert calls[1]["url"] == "http://test-cms-poster-title.epubgame.com/api/v1/ai-jobs/jobs/job-remote/billing"


def test_audio_stem_separation_run_uses_payload_file_api_flow(tmp_path, monkeypatch, capsys):
    clear_api_env(monkeypatch)
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(audio_stem_separation, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=true\n",
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")
    input_audio = {
        "public_url": "https://local-dev.oss-local.aliyuncs.com/audio/input.wav",
        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/audio/input.wav",
        "content_type": "audio/wav",
        "sha256": "a" * 64,
    }
    payload_file = tmp_path / "audio-payload.json"
    payload_file.write_text(
        json.dumps(
            audio_stem_separation.build_job_payload(
                input_audio=input_audio,
                job_type="audio_stem_separation",
                client_request_id="audio-client-1",
                max_duration_seconds=60.0,
            )
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_request_json(url, *, method, headers, payload=None, timeout_seconds=10):
        calls.append({"url": url, "method": method, "headers": headers, "payload": payload})
        return {"code": "0", "data": {"job": {"job_id": "audio-job-1", "job_status": "queued"}}}

    def stem_ref(name: str) -> dict[str, str]:
        return {
            "public_url": f"https://local-dev.oss-local.aliyuncs.com/output/audio-job-1/{name}.wav",
            "internal_url": f"https://local-dev.oss-local-internal.aliyuncs.com/output/audio-job-1/{name}.wav",
            "content_type": "audio/wav",
            "sha256": "b" * 64,
        }

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    def fake_poll_job_envelope(**kwargs):
        kwargs["progress_callback"](
            {
                "job_id": "audio-job-1",
                "job_status": "running",
                "job_progress": {"stage": "calling_model", "percent": 30, "message": "running"},
                "callback": {"status": "pending", "attempt": 0},
            },
            3.4,
        )
        return {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "audio-job-1",
                    "job_status": "succeeded",
                    "job_type": "audio_stem_separation",
                    "callback": {
                        "status": "not_configured",
                        "attempt": 0,
                        "last_error": None,
                        "next_retry_at": None,
                    },
                    "job_result": {
                        "stems": {
                            "drums": stem_ref("drums"),
                            "bass": stem_ref("bass"),
                            "other": stem_ref("other"),
                            "vocals": stem_ref("vocals"),
                        }
                    },
                }
            },
        }

    monkeypatch.setattr(job_runtime, "poll_job_envelope", fake_poll_job_envelope)

    audio_stem_separation.run(
        confirm_run=True,
        confirm_upload=False,
        api_url=None,
        env_file=None,
        allow_remote_api=False,
        service_api_key=None,
        caller_id="caller-1",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        job_type="audio_stem_separation",
        client_request_id=None,
        payload_file=str(payload_file),
        input_file=None,
        input_url_ref_json=None,
        input_public_url=None,
        input_internal_url=None,
        input_sha256=None,
        max_duration_seconds=None,
        key_prefix=None,
        signed_url_expires_seconds=3600,
        download_outputs=False,
        output_dir=".data/audio-stems",
        json_output=True,
    )

    output = capsys.readouterr().out
    assert "WAIT" not in output
    result = json.loads(output)
    assert result["summary"]["job_id"] == "audio-job-1"
    assert result["summary"]["job_type"] == "audio_stem_separation"
    assert result["summary"]["stems_count"] == 4
    assert result["conclusion"] == "job=succeeded stems=4 artifacts=0"
    assert "callback_status" not in result["summary"]
    assert "callback_attempt" not in result["summary"]
    assert result["responses"]["get_job"]["data"]["job"]["callback"]["status"] == "not_configured"
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "http://127.0.0.1:18200/api/v1/ai-jobs/jobs"
    assert calls[0]["payload"]["job_type"] == "audio_stem_separation"
    assert calls[0]["payload"]["job_params"]["input_audio"] == input_audio


def test_audio_stem_separation_run_prints_stage_poll_and_callback_status(tmp_path, monkeypatch, capsys):
    clear_api_env(monkeypatch)
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(audio_stem_separation, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=true\n",
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")
    input_audio = {
        "public_url": "https://local-dev.oss-local.aliyuncs.com/audio/input.wav",
        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/audio/input.wav",
        "content_type": "audio/wav",
        "sha256": "a" * 64,
    }
    payload_file = tmp_path / "audio-payload.json"
    payload_file.write_text(
        json.dumps(
            audio_stem_separation.build_job_payload(
                input_audio=input_audio,
                job_type="audio_stem_separation",
                client_request_id="audio-client-1",
                max_duration_seconds=60.0,
            )
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(http_runtime, "request_json",
        lambda url, *, method, headers, payload=None, timeout_seconds=10: {
            "code": "0",
            "data": {"job": {"job_id": "audio-job-1", "job_status": "queued"}},
        },
    )

    def fake_poll_job_envelope(**kwargs):
        progress_callback = kwargs["progress_callback"]
        progress_callback(
            {
                "job_id": "audio-job-1",
                "job_status": "running",
                "job_progress": {"stage": "calling_model", "percent": 25, "message": "running onnx"},
                "callback": {"status": "pending", "attempt": 0},
            },
            5.2,
        )
        return {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "audio-job-1",
                    "job_status": "succeeded",
                    "job_type": "audio_stem_separation",
                    "job_progress": {"stage": "succeeded", "percent": 100, "message": "succeeded"},
                    "callback": {"status": "not_configured", "attempt": 0},
                    "job_result": {"stems": {"drums": {}, "bass": {}, "other": {}, "vocals": {}}},
                }
            },
        }

    monkeypatch.setattr(job_runtime, "poll_job_envelope", fake_poll_job_envelope)

    audio_stem_separation.run(
        confirm_run=True,
        confirm_upload=False,
        api_url=None,
        env_file=None,
        allow_remote_api=False,
        service_api_key=None,
        caller_id="caller-1",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        job_type="audio_stem_separation",
        client_request_id=None,
        payload_file=str(payload_file),
        input_file=None,
        input_url_ref_json=None,
        input_public_url=None,
        input_internal_url=None,
        input_sha256=None,
        max_duration_seconds=None,
        key_prefix=None,
        signed_url_expires_seconds=3600,
        download_outputs=False,
        output_dir=".data/audio-stems",
        json_output=False,
    )

    output = capsys.readouterr().out
    assert "== Audio Stem Separation Smoke ==" in output
    assert "OK        preflight" in output
    assert "RUN       prepare" in output
    assert "RUN       submit" in output
    assert "RUN       poll" in output
    assert "WAIT      job" in output
    assert "status=running" in output
    assert "callback=pending" in output
    assert "callback=not_configured" in output
    assert "OK        assert     stems=4" in output


def test_audio_stem_separation_run_does_not_download_outputs_for_failed_job(tmp_path, monkeypatch, capsys):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(audio_stem_separation, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=true\n",
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")
    payload_file = tmp_path / "audio-payload.json"
    payload_file.write_text(
        json.dumps(
            {
                "client_request_id": "audio-client-failed",
                "job_type": "audio_stem_separation",
                "job_params": {
                    "input_audio": {
                        "public_url": "https://local-dev.oss-local.aliyuncs.com/audio/input.wav",
                        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/audio/input.wav",
                        "content_type": "audio/wav",
                        "sha256": "a" * 64,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(http_runtime, "request_json",
        lambda url, *, method, headers, payload=None, timeout_seconds=10: {
            "code": "0",
            "data": {"job": {"job_id": "audio-job-failed", "job_status": "queued"}},
        },
    )
    monkeypatch.setattr(
            job_runtime,
            "poll_job_envelope",
        lambda **_kwargs: {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "audio-job-failed",
                    "job_status": "failed",
                    "job_type": "audio_stem_separation",
                    "error": {"code": "AUDIO_STEM_INFERENCE_FAILED"},
                }
            },
        },
    )
    monkeypatch.setattr(
        audio_stem_separation,
        "download_output_artifacts",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("failed jobs must not download outputs")),
    )

    with pytest.raises(audio_stem_separation.FlowError, match="finished with failed"):
        audio_stem_separation.run(
            confirm_run=True,
            confirm_upload=False,
            api_url=None,
            env_file=None,
            allow_remote_api=False,
            service_api_key=None,
            caller_id="caller-1",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            job_type="audio_stem_separation",
            client_request_id=None,
            payload_file=str(payload_file),
            input_file=None,
            input_url_ref_json=None,
            input_public_url=None,
            input_internal_url=None,
            input_sha256=None,
            max_duration_seconds=None,
            key_prefix=None,
            signed_url_expires_seconds=3600,
            download_outputs=True,
            output_dir=".data/audio-stems",
            json_output=True,
        )

    result = json.loads(capsys.readouterr().out)
    assert result["summary"]["job_status"] == "failed"
    assert result["summary"]["artifacts"] == []


def test_audio_stem_separation_failed_job_prints_diagnostic_hints(tmp_path, monkeypatch, capsys):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(audio_stem_separation, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=true\n",
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")
    payload_file = tmp_path / "audio-payload.json"
    payload_file.write_text(
        json.dumps(
            {
                "client_request_id": "audio-client-failed",
                "job_type": "audio_stem_separation",
                "job_params": {
                    "input_audio": {
                        "public_url": "https://local-dev.oss-local.aliyuncs.com/audio/input.wav",
                        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/audio/input.wav",
                        "content_type": "audio/wav",
                        "sha256": "a" * 64,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(http_runtime, "request_json",
        lambda url, *, method, headers, payload=None, timeout_seconds=10: {
            "code": "0",
            "data": {"job": {"job_id": "audio-job-failed", "job_status": "queued"}},
        },
    )
    monkeypatch.setattr(
            job_runtime,
            "poll_job_envelope",
        lambda **_kwargs: {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "audio-job-failed",
                    "job_status": "failed",
                    "job_type": "audio_stem_separation",
                    "job_error": {"reason": "AUDIO_STEM_INFERENCE_FAILED"},
                    "callback": {
                        "status": "failed",
                        "attempt": 1,
                        "last_error": {"reason": "CALLBACK_DELIVERY_FAILED", "retryable": False},
                        "next_retry_at": None,
                    },
                }
            },
        },
    )

    with pytest.raises(audio_stem_separation.FlowError, match="finished with failed"):
        audio_stem_separation.run(
            confirm_run=True,
            confirm_upload=False,
            api_url=None,
            env_file=None,
            allow_remote_api=False,
            service_api_key=None,
            caller_id="caller-1",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            job_type="audio_stem_separation",
            client_request_id=None,
            payload_file=str(payload_file),
            input_file=None,
            input_url_ref_json=None,
            input_public_url=None,
            input_internal_url=None,
            input_sha256=None,
            max_duration_seconds=None,
            key_prefix=None,
            signed_url_expires_seconds=3600,
            download_outputs=False,
            output_dir=".data/audio-stems",
            json_output=False,
        )

    output = capsys.readouterr().out
    assert "ERROR     job" in output
    assert "ERROR     assert" in output
    assert "callback=failed" in output
    assert "./scripts/jobs.sh show audio-job-failed" in output
    assert "./scripts/jobs.sh timeline audio-job-failed" in output
    assert "./scripts/jobs.sh attempts audio-job-failed" in output


def test_audio_stem_separation_poll_timeout_prints_diagnostic_hints(tmp_path, monkeypatch, capsys):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(audio_stem_separation, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=true\n",
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")
    payload_file = tmp_path / "audio-payload.json"
    payload_file.write_text(
        json.dumps(
            {
                "client_request_id": "audio-client-timeout",
                "job_type": "audio_stem_separation",
                "job_params": {
                    "input_audio": {
                        "public_url": "https://local-dev.oss-local.aliyuncs.com/audio/input.wav",
                        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/audio/input.wav",
                        "content_type": "audio/wav",
                        "sha256": "a" * 64,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(http_runtime, "request_json",
        lambda url, *, method, headers, payload=None, timeout_seconds=10: {
            "code": "0",
            "data": {"job": {"job_id": "audio-job-timeout", "job_status": "queued"}},
        },
    )

    def timeout_poll(**_kwargs):
        raise FlowError("job audio-job-timeout did not finish within 1s", exit_code=5)

    monkeypatch.setattr(job_runtime, "poll_job_envelope", timeout_poll)

    with pytest.raises(FlowError) as exc_info:
        audio_stem_separation.run(
            confirm_run=True,
            confirm_upload=False,
            api_url=None,
            env_file=None,
            allow_remote_api=False,
            service_api_key=None,
            caller_id="caller-1",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            job_type="audio_stem_separation",
            client_request_id=None,
            payload_file=str(payload_file),
            input_file=None,
            input_url_ref_json=None,
            input_public_url=None,
            input_internal_url=None,
            input_sha256=None,
            max_duration_seconds=None,
            key_prefix=None,
            signed_url_expires_seconds=3600,
            download_outputs=False,
            output_dir=".data/audio-stems",
            json_output=False,
        )

    assert exc_info.value.exit_code == 5
    output = capsys.readouterr().out
    assert "./scripts/jobs.sh show audio-job-timeout" in output
    assert "./scripts/jobs.sh timeline audio-job-timeout" in output
    assert "./scripts/jobs.sh attempts audio-job-timeout" in output


def test_audio_stem_separation_run_cleans_staged_input_after_terminal_job(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(audio_stem_separation, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=true\n",
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")
    input_audio = {
        "public_url": "https://local-dev.oss-local.aliyuncs.com/audio/input.wav",
        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/audio/input.wav",
        "content_type": "audio/wav",
        "sha256": "a" * 64,
    }
    staged_input = {
        "provider": "aliyun_oss",
        "bucket": "bucket-a",
        "region": "cn-hangzhou",
        "key": "project-a/audio/input.wav",
        "url_ref": input_audio,
    }
    cleanup_calls = []

    monkeypatch.setattr(
        audio_stem_separation,
        "build_payload",
        lambda **_kwargs: (
            audio_stem_separation.build_job_payload(
                input_audio=input_audio,
                job_type="audio_stem_separation",
                client_request_id="audio-client-cleanup",
                max_duration_seconds=None,
            ),
            staged_input,
        ),
    )
    monkeypatch.setattr(audio_stem_separation, "cleanup_staged_input", lambda staged_input, app_env: cleanup_calls.append(staged_input))
    monkeypatch.setattr(http_runtime, "request_json",
        lambda url, *, method, headers, payload=None, timeout_seconds=10: {
            "code": "0",
            "data": {"job": {"job_id": "audio-job-cleanup", "job_status": "queued"}},
        },
    )
    monkeypatch.setattr(
            job_runtime,
            "poll_job_envelope",
        lambda **_kwargs: {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "audio-job-cleanup",
                    "job_status": "succeeded",
                    "job_type": "audio_stem_separation",
                    "job_result": {"stems": {}},
                }
            },
        },
    )

    audio_stem_separation.run(
        confirm_run=True,
        confirm_upload=True,
        api_url=None,
        env_file=None,
        allow_remote_api=False,
        service_api_key=None,
        caller_id="caller-1",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        job_type="audio_stem_separation",
        client_request_id=None,
        payload_file=None,
        input_file="input.wav",
        input_url_ref_json=None,
        input_public_url=None,
        input_internal_url=None,
        input_sha256=None,
        max_duration_seconds=None,
        key_prefix=None,
        signed_url_expires_seconds=3600,
        download_outputs=False,
        output_dir=".data/audio-stems",
        json_output=True,
    )

    assert cleanup_calls == [staged_input]


def test_audio_stem_separation_run_keeps_staged_input_when_create_response_is_unknown(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(audio_stem_separation, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=true\n",
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")
    input_audio = {
        "public_url": "https://local-dev.oss-local.aliyuncs.com/audio/input.wav",
        "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/audio/input.wav",
        "content_type": "audio/wav",
        "sha256": "a" * 64,
    }
    staged_input = {"provider": "aliyun_oss", "bucket": "bucket-a", "region": "cn-hangzhou", "key": "project-a/audio/input.wav"}
    cleanup_calls = []

    monkeypatch.setattr(
        audio_stem_separation,
        "build_payload",
        lambda **_kwargs: (
            audio_stem_separation.build_job_payload(
                input_audio=input_audio,
                job_type="audio_stem_separation",
                client_request_id="audio-client-unknown",
                max_duration_seconds=None,
            ),
            staged_input,
        ),
    )
    monkeypatch.setattr(audio_stem_separation, "cleanup_staged_input", lambda staged_input, app_env: cleanup_calls.append(staged_input))
    monkeypatch.setattr(http_runtime, "request_json", lambda *_args, **_kwargs: {"code": "0", "data": {}})

    with pytest.raises(FlowError, match="response missing data.job"):
        audio_stem_separation.run(
            confirm_run=True,
            confirm_upload=True,
            api_url=None,
            env_file=None,
            allow_remote_api=False,
            service_api_key=None,
            caller_id="caller-1",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            job_type="audio_stem_separation",
            client_request_id=None,
            payload_file=None,
            input_file="input.wav",
            input_url_ref_json=None,
            input_public_url=None,
            input_internal_url=None,
            input_sha256=None,
            max_duration_seconds=None,
            key_prefix=None,
            signed_url_expires_seconds=3600,
            download_outputs=False,
            output_dir=".data/audio-stems",
            json_output=True,
        )

    assert cleanup_calls == []


def test_smoke_run_uses_double_job_type(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_CONFIG_PATH", raising=False)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    write_smoke_model_catalog(tmp_path, default_model_id="gpt-5.4-mini")
    (tmp_path / ".env").write_text("DISABLE_HTTP_AUTH_HEADER=true\nMODEL_CONFIG_PATH=models.yaml\n", encoding="utf-8")
    append_root_env(tmp_path, "API_PORT=18200")
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

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    monkeypatch.setattr(
            job_runtime,
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


def test_smoke_run_uses_poster_title_image_api_flow(tmp_path, monkeypatch, capsys):
    clear_api_env(monkeypatch)
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DISABLE_HTTP_AUTH_HEADER=true",
                "DISABLE_CALLER_ID_HEADER=true",
                "STORAGE_BACKEND=local",
                "LOCAL_OBJECT_STORAGE_PATH=storage/objects",
            ]
        ),
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")
    (tmp_path / ".data/title").mkdir(parents=True)
    reference_data = _transparent_png_bytes()
    reference_path = tmp_path / ".data/title/英语.png"
    reference_path.write_bytes(reference_data)
    calls = []

    def fake_request_json(url, *, method, headers, payload=None, timeout_seconds=10):
        calls.append({"url": url, "method": method, "headers": headers, "payload": payload})
        if method == "POST":
            return {"code": "0", "data": {"job": {"job_id": "poster-job-1", "job_status": "queued"}}}
        if url.endswith("/billing"):
            return {
                "code": "0",
                "data": {
                    "billing": {
                        "status": "estimated",
                        "currency": "USD",
                        "total_cost_amount": "0.00596500",
                        "usage_units": {
                            "image_count": 1,
                            "input_tokens": 17,
                            "cached_input_tokens": 0,
                            "output_tokens": 196,
                            "total_tokens": 213,
                            "text_input_tokens": 17,
                            "cached_text_input_tokens": 0,
                            "image_input_tokens": 0,
                            "cached_image_input_tokens": 0,
                            "image_output_tokens": 196,
                        },
                        "pricing_refs": ["openai:gpt-image-2@2026-07-02"],
                        "ai_call_count": 2,
                        "billable_call_count": 2,
                        "failed_call_count": 0,
                    }
                },
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    monkeypatch.setattr(
            job_runtime,
            "poll_job_envelope",
        lambda **_kwargs: {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "poster-job-1",
                    "job_status": "succeeded",
                    "job_type": "poster_title_image",
                    "cost": {"currency": "USD", "amount": "0.00596500", "final": True},
                    "job_result": {
                        "items": [
                            {
                                "item_id": "es",
                                "language": "es",
                                "status": "succeeded",
                                "images": [
                                    {
                                        "object": {
                                            "public_url": "https://local-dev.oss-local.aliyuncs.com/output/title.png",
                                            "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/output/title.png",
                                            "content_type": "image/png",
                                            "sha256": "b" * 64,
                                        },
                                        "width": 40,
                                        "height": 40,
                                    }
                                ],
                            }
                        ]
                    },
                }
            },
        },
    )

    poster_title_image.run(
        confirm_cost=True,
        confirm_upload=False,
        api_url=None,
        items_json=None,
        reference_image=poster_title_image.DEFAULT_REFERENCE_IMAGE,
        reference_url_ref_json=None,
        reference_public_url=None,
        reference_internal_url=None,
        reference_sha256=None,
        reference_content_type="image/png",
        item_id="es",
        language="es",
        title_text="Cuando el amor se alejo",
        size="auto",
        quality="high",
        draw_count=1,
        caller_id="caller-1",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        client_request_id="poster-client-1",
        json_output=True,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["job_type"] == "poster_title_image"
    assert payload["summary"]["output_count"] == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["payload"]["job_type"] == "poster_title_image"
    item = calls[0]["payload"]["job_params"]["items"][0]
    assert "model_id" not in item
    assert item["reference_image"]["sha256"] == poster_title_image._bare_sha256(reference_data)
    assert calls[1]["url"] == "http://127.0.0.1:18200/api/v1/ai-jobs/jobs/poster-job-1/billing"
    staged = list((tmp_path / "storage/objects/local-dev/smoke/poster-title-image/reference").glob("**/英语.png"))
    assert len(staged) == 1


def test_poster_title_image_downloads_all_output_artifacts(tmp_path, monkeypatch, capsys):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DISABLE_HTTP_AUTH_HEADER=true",
                "DISABLE_CALLER_ID_HEADER=true",
                "STORAGE_BACKEND=local",
                "LOCAL_OBJECT_STORAGE_PATH=storage/objects",
            ]
        ),
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")
    (tmp_path / ".data/title").mkdir(parents=True)
    (tmp_path / ".data/title/英语.png").write_bytes(_transparent_png_bytes())

    output_data = _transparent_png_bytes()
    objects = [
        ("es", "es", "outputs/poster-job-1/es/title-layer.png", output_data),
        ("es", "es", "outputs/poster-job-1/es/title-layer-2.png", output_data),
        ("pt", "pt", "outputs/poster-job-1/pt/title-layer.png", output_data),
    ]
    for _item_id, _language, key, data in objects:
        path = tmp_path / "storage/objects/local-dev" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def output_object(key: str, data: bytes) -> dict[str, str]:
        return {
            "public_url": f"https://local-dev.oss-local.aliyuncs.com/{key}",
            "internal_url": f"https://local-dev.oss-local-internal.aliyuncs.com/{key}",
            "content_type": "image/png",
            "sha256": poster_title_image._bare_sha256(data),
        }

    monkeypatch.setattr(http_runtime, "request_json",
        lambda url, *, method, headers, payload=None, timeout_seconds=10: (
            {"code": "0", "data": {"job": {"job_id": "poster-job-1", "job_status": "queued"}}}
            if method == "POST"
            else {
                "code": "0",
                "data": {
                    "billing": {
                        "status": "estimated",
                        "currency": "USD",
                        "total_cost_amount": "0.01789500",
                        "usage_units": {
                            "image_count": 3,
                            "input_tokens": 51,
                            "cached_input_tokens": 0,
                            "output_tokens": 588,
                            "total_tokens": 639,
                            "text_input_tokens": 51,
                            "cached_text_input_tokens": 0,
                            "image_input_tokens": 0,
                            "cached_image_input_tokens": 0,
                            "image_output_tokens": 588,
                        },
                        "pricing_refs": [],
                        "ai_call_count": 4,
                        "billable_call_count": 4,
                        "failed_call_count": 0,
                    }
                },
            }
        ),
    )
    monkeypatch.setattr(
            job_runtime,
            "poll_job_envelope",
        lambda **_kwargs: {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "poster-job-1",
                    "job_status": "succeeded",
                    "job_type": "poster_title_image",
                    "job_result": {
                        "items": [
                            {
                                "item_id": "es",
                                "language": "es",
                                "status": "succeeded",
                                "images": [
                                    {"object": output_object(objects[0][2], objects[0][3]), "width": 40, "height": 40},
                                    {"object": output_object(objects[1][2], objects[1][3]), "width": 40, "height": 40},
                                ],
                            },
                            {
                                "item_id": "pt",
                                "language": "pt",
                                "status": "succeeded",
                                "images": [
                                    {"object": output_object(objects[2][2], objects[2][3]), "width": 40, "height": 40},
                                ],
                            },
                        ]
                    },
                }
            },
        },
    )

    poster_title_image.run(
        confirm_cost=True,
        confirm_upload=False,
        api_url=None,
        items_json=None,
        reference_image=poster_title_image.DEFAULT_REFERENCE_IMAGE,
        reference_url_ref_json=None,
        reference_public_url=None,
        reference_internal_url=None,
        reference_sha256=None,
        reference_content_type="image/png",
        item_id="es",
        language="es",
        title_text="Cuando el amor se alejo",
        size="auto",
        quality="high",
        draw_count=1,
        caller_id="caller-1",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        client_request_id="poster-client-1",
        json_output=True,
        download_outputs=True,
        output_dir=".data/downloaded-poster-title",
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["image_inspection"] == {
        "enabled": True,
        "require_transparent_background": True,
        "checked_count": 3,
        "passed_count": 3,
        "failed_count": 0,
    }
    artifacts = payload["summary"]["artifacts"]
    assert len(artifacts) == 3
    assert {(item["item_id"], item["image_index"]) for item in artifacts} == {("es", 1), ("es", 2), ("pt", 1)}
    for artifact in artifacts:
        assert artifact["download_method"] == "local_storage"
        assert artifact["sha256_verified"] is True
        assert artifact["image_inspection"]["passed"] is True
        assert artifact["image_inspection"]["require_transparent_background"] is True
        assert artifact["image_inspection"]["result"]["alpha"]["transparent_background"] is True
        assert (tmp_path / artifact["local_path"]).is_file()


def test_poster_title_image_download_uses_signed_url_when_public_url_is_private(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    app_env = {
        "STORAGE_BACKEND": "aliyun_oss",
        "OSS_BUCKET": "bucket-a",
        "OSS_REGION": "cn-hangzhou",
        "OSS_ACCESS_KEY_ID": "id",
        "OSS_ACCESS_KEY_SECRET": "secret",
        "OSS_PROJECT_ROOT": "project-a",
    }
    data = _transparent_png_bytes()
    calls = []

    def fake_download_url(url, *, timeout_seconds=30):
        calls.append(url)
        if "Signature=" not in url:
            raise poster_title_image.FlowError("download failed: status=403", exit_code=4)
        return data

    monkeypatch.setattr(poster_title_image, "_download_url", fake_download_url)

    artifacts = poster_title_image.download_output_artifacts(
        job={
            "job_id": "poster-job-private",
            "job_result": {
                "items": [
                    {
                        "item_id": "es",
                        "language": "es",
                        "images": [
                            {
                                "object": {
                                    "public_url": (
                                        "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/"
                                        "project-a/poster-job-private/es/title-layer.png"
                                    ),
                                    "internal_url": (
                                        "https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/"
                                        "project-a/poster-job-private/es/title-layer.png"
                                    ),
                                    "content_type": "image/png",
                                    "sha256": poster_title_image._bare_sha256(data),
                                },
                                "width": 40,
                                "height": 40,
                            }
                        ],
                    }
                ]
            },
        },
        app_env=app_env,
        output_dir=".data/downloaded-poster-title",
        signed_url_expires_seconds=60,
    )

    assert len(calls) == 2
    assert calls[0].endswith("/project-a/poster-job-private/es/title-layer.png")
    assert "Signature=" in calls[1]
    assert artifacts[0]["item_id"] == "es"
    assert artifacts[0]["language"] == "es"
    assert artifacts[0]["image_index"] == 1
    assert artifacts[0]["content_type"] == "image/png"
    assert artifacts[0]["sha256"] == poster_title_image._bare_sha256(data)
    assert artifacts[0]["sha256_verified"] is True
    assert artifacts[0]["download_method"] == "signed_url"
    assert artifacts[0]["local_path"] == ".data/downloaded-poster-title/poster-job-private/es-es/01-title-layer.png"
    assert artifacts[0]["image_inspection"]["passed"] is True
    assert artifacts[0]["image_inspection"]["result"]["alpha"]["transparent_background"] is True
    assert (tmp_path / artifacts[0]["local_path"]).read_bytes() == data


def test_poster_title_image_download_signed_fallback_supports_cdn_public_url(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    app_env = {
        "STORAGE_BACKEND": "aliyun_oss",
        "OSS_BUCKET": "bucket-a",
        "OSS_REGION": "cn-hangzhou",
        "OSS_ACCESS_KEY_ID": "id",
        "OSS_ACCESS_KEY_SECRET": "secret",
        "OSS_PROJECT_ROOT": "project-a",
        "OSS_PUBLIC_ENDPOINT": "cdn.example.com",
    }
    data = _transparent_png_bytes()
    calls = []

    def fake_download_url(url, *, timeout_seconds=30):
        calls.append(url)
        if "Signature=" not in url:
            raise poster_title_image.FlowError("download failed: status=403", exit_code=4)
        return data

    monkeypatch.setattr(poster_title_image, "_download_url", fake_download_url)

    artifacts = poster_title_image.download_output_artifacts(
        job={
            "job_id": "poster-job-cdn",
            "job_result": {
                "items": [
                    {
                        "item_id": "es",
                        "language": "es",
                        "images": [
                            {
                                "object": {
                                    "public_url": "https://cdn.example.com/project-a/poster-job-cdn/es/title-layer.png",
                                    "internal_url": (
                                        "https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/"
                                        "project-a/poster-job-cdn/es/title-layer.png"
                                    ),
                                    "content_type": "image/png",
                                    "sha256": poster_title_image._bare_sha256(data),
                                },
                                "width": 40,
                                "height": 40,
                            }
                        ],
                    }
                ]
            },
        },
        app_env=app_env,
        output_dir=".data/downloaded-poster-title",
        signed_url_expires_seconds=60,
    )

    assert calls[0] == "https://cdn.example.com/project-a/poster-job-cdn/es/title-layer.png"
    assert "Signature=" in calls[1]
    assert "/project-a/poster-job-cdn/es/title-layer.png?" in calls[1]
    assert artifacts[0]["sha256_verified"] is True
    assert artifacts[0]["download_method"] == "signed_url"


def test_poster_title_image_download_rejects_non_transparent_background(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    image = Image.new("RGBA", (4, 4), (255, 255, 255, 255))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    data = buf.getvalue()
    key = "outputs/poster-job-opaque/es/title-layer.png"
    path = tmp_path / "storage/objects/local-dev" / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    with pytest.raises(poster_title_image.FlowError, match="image inspection failed"):
        poster_title_image.download_output_artifacts(
            job={
                "job_id": "poster-job-opaque",
                "job_result": {
                    "items": [
                        {
                            "item_id": "es",
                            "language": "es",
                            "images": [
                                {
                                    "object": {
                                        "public_url": f"https://local-dev.oss-local.aliyuncs.com/{key}",
                                        "internal_url": f"https://local-dev.oss-local-internal.aliyuncs.com/{key}",
                                        "content_type": "image/png",
                                        "sha256": poster_title_image._bare_sha256(data),
                                    },
                                    "width": 40,
                                    "height": 40,
                                }
                            ],
                        }
                    ]
                },
            },
            app_env={"STORAGE_BACKEND": "local", "LOCAL_OBJECT_STORAGE_PATH": "storage/objects"},
            output_dir=".data/downloaded-poster-title",
            signed_url_expires_seconds=60,
        )


def test_smoke_run_uploads_poster_reference_when_aliyun_oss_enabled(tmp_path, monkeypatch, capsys):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DISABLE_HTTP_AUTH_HEADER=true",
                "DISABLE_CALLER_ID_HEADER=true",
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket-a",
                "OSS_REGION=cn-hangzhou",
                "OSS_ACCESS_KEY_ID=id",
                "OSS_ACCESS_KEY_SECRET=secret",
                "OSS_PROJECT_ROOT=project-a",
            ]
        ),
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")
    reference_path = tmp_path / "reference.png"
    reference_path.write_bytes(_transparent_png_bytes())
    uploaded_ref = {
        "public_url": "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/reference.png",
        "internal_url": "https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/project-a/reference.png",
        "content_type": "image/png",
        "sha256": "c" * 64,
    }
    uploaded_image = {
        "provider": "aliyun_oss",
        "bucket": "bucket-a",
        "region": "cn-hangzhou",
        "key": "project-a/reference.png",
        "url_ref": uploaded_ref,
    }
    upload_calls = []
    cleanup_calls = []
    http_calls = []

    def fake_upload_image(**kwargs):
        upload_calls.append(kwargs)
        return uploaded_image

    def fake_request_json(url, *, method, headers, payload=None, timeout_seconds=10):
        http_calls.append({"url": url, "method": method, "payload": payload})
        if method == "POST":
            return {"code": "0", "data": {"job": {"job_id": "poster-job-oss", "job_status": "queued"}}}
        return {
            "code": "0",
            "data": {
                "billing": {
                    "status": "estimated",
                    "currency": "USD",
                    "total_cost_amount": "0.04000000",
                    "usage_units": {},
                    "pricing_refs": [],
                    "ai_call_count": 2,
                    "billable_call_count": 2,
                    "failed_call_count": 0,
                }
            },
        }

    monkeypatch.setattr(oss_image_upload, "upload_image", fake_upload_image)
    monkeypatch.setattr(
        oss_image_upload,
        "delete_uploaded_image",
        lambda **kwargs: cleanup_calls.append(kwargs),
    )
    monkeypatch.setattr(http_runtime, "request_json", fake_request_json)
    monkeypatch.setattr(
            job_runtime,
            "poll_job_envelope",
        lambda **_kwargs: {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "poster-job-oss",
                    "job_status": "succeeded",
                    "job_type": "poster_title_image",
                    "job_result": {"items": []},
                }
            },
        },
    )

    poster_title_image.run(
        confirm_cost=True,
        confirm_upload=True,
        api_url=None,
        items_json=None,
        reference_image=str(reference_path),
        reference_url_ref_json=None,
        reference_public_url=None,
        reference_internal_url=None,
        reference_sha256=None,
        reference_content_type="image/png",
        item_id="es",
        language="es",
        title_text="Cuando el amor se alejo",
        size="auto",
        quality="high",
        draw_count=1,
        caller_id="caller-1",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        client_request_id="poster-client-oss",
        json_output=True,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["job_id"] == "poster-job-oss"
    assert upload_calls[0]["image"] == str(reference_path)
    assert upload_calls[0]["key_prefix"] == "smoke/poster-title-image/reference"
    assert http_calls[0]["payload"]["job_params"]["items"][0]["reference_image"] == uploaded_ref
    assert cleanup_calls == [{"upload_result": uploaded_image, "app_env": upload_calls[0]["app_env"]}]


def test_poster_title_image_aliyun_upload_requires_confirmation(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    source = tmp_path / "reference.png"
    source.write_bytes(b"png")

    with pytest.raises(poster_title_image.FlowError, match="--confirm-upload"):
        poster_title_image.resolve_reference_image(
            reference_image=str(source),
            reference_url_ref_json=None,
            reference_public_url=None,
            reference_internal_url=None,
            reference_sha256=None,
            reference_content_type="image/png",
            app_env={"STORAGE_BACKEND": "aliyun_oss"},
            confirm_upload=False,
        )


def test_poster_title_image_explicit_url_ref_does_not_upload(monkeypatch):
    clear_storage_env(monkeypatch)
    uploaded_ref = {
        "public_url": "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/reference.png",
        "internal_url": "https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/project-a/reference.png",
        "content_type": "image/png",
        "sha256": "c" * 64,
    }

    def unexpected_upload(**_kwargs):
        raise AssertionError("explicit URL Ref must not upload")

    monkeypatch.setattr(oss_image_upload, "upload_image", unexpected_upload)

    resolution = poster_title_image.resolve_reference_image(
        reference_image=None,
        reference_url_ref_json=None,
        reference_public_url=uploaded_ref["public_url"],
        reference_internal_url=uploaded_ref["internal_url"],
        reference_sha256=uploaded_ref["sha256"],
        reference_content_type=uploaded_ref["content_type"],
        app_env={"STORAGE_BACKEND": "aliyun_oss"},
        confirm_upload=False,
    )

    assert resolution.ref == uploaded_ref
    assert resolution.uploaded_image is None


def test_poster_title_image_reference_url_ref_json_accepts_upload_output(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    uploaded_ref = {
        "public_url": "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/reference.png",
        "internal_url": "https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/project-a/reference.png",
        "content_type": "image/png",
        "sha256": "c" * 64,
    }
    source = tmp_path / "upload-output.json"
    source.write_text(json.dumps({"url_ref": uploaded_ref}), encoding="utf-8")

    resolution = poster_title_image.resolve_reference_image(
        reference_image=None,
        reference_url_ref_json="upload-output.json",
        reference_public_url=None,
        reference_internal_url=None,
        reference_sha256=None,
        reference_content_type=None,
        app_env={"STORAGE_BACKEND": "aliyun_oss"},
        confirm_upload=False,
    )

    assert resolution.ref == uploaded_ref
    assert resolution.uploaded_image is None


def test_poster_title_image_reference_url_ref_json_accepts_plain_ref(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    uploaded_ref = {
        "public_url": "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/reference.png",
        "internal_url": "https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/project-a/reference.png",
        "content_type": "image/png",
        "sha256": "d" * 64,
    }
    source = tmp_path / "reference.json"
    source.write_text(json.dumps(uploaded_ref), encoding="utf-8")

    resolved = poster_title_image.reference_image_from_url_ref_json("reference.json")

    assert resolved == uploaded_ref


def test_poster_title_image_reference_url_ref_json_rejects_empty_fields(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    source = tmp_path / "reference.json"
    source.write_text(
        json.dumps(
            {
                "public_url": "",
                "internal_url": "https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/project-a/reference.png",
                "content_type": "image/png",
                "sha256": "d" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(poster_title_image.FlowError, match="public_url"):
        poster_title_image.reference_image_from_url_ref_json("reference.json")


def test_poster_title_image_rejects_mixed_reference_sources(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    source = tmp_path / "reference.json"
    source.write_text(
        json.dumps(
            {
                "public_url": "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/reference.png",
                "internal_url": "https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/project-a/reference.png",
                "content_type": "image/png",
                "sha256": "d" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(poster_title_image.FlowError, match="exactly one"):
        poster_title_image.resolve_reference_image(
            reference_image="reference.png",
            reference_url_ref_json="reference.json",
            reference_public_url=None,
            reference_internal_url=None,
            reference_sha256=None,
            reference_content_type=None,
            app_env={"STORAGE_BACKEND": "local"},
            confirm_upload=False,
        )


def test_poster_title_image_explicit_url_ref_accepts_jpeg_content_type(monkeypatch):
    clear_storage_env(monkeypatch)

    result = poster_title_image.resolve_reference_image(
        reference_image=None,
        reference_url_ref_json=None,
        reference_public_url="https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/reference.jpg",
        reference_internal_url="https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/project-a/reference.jpg",
        reference_sha256="c" * 64,
        reference_content_type="image/jpeg",
        app_env={"STORAGE_BACKEND": "aliyun_oss"},
        confirm_upload=False,
    )

    assert result.ref["content_type"] == "image/jpeg"


def test_smoke_run_ignores_env_reference_url_ref_by_default(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DISABLE_HTTP_AUTH_HEADER=true",
                "DISABLE_CALLER_ID_HEADER=true",
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket-a",
                "OSS_REGION=cn-hangzhou",
                "OSS_ACCESS_KEY_ID=id",
                "OSS_ACCESS_KEY_SECRET=secret",
                "OSS_PROJECT_ROOT=project-a",
            ]
        ),
        encoding="utf-8",
    )
    append_root_env(
        tmp_path,
        "API_HOST=127.0.0.1",
        "API_PORT=18200",
        "POSTER_TITLE_IMAGE_REFERENCE_PUBLIC_URL=https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/reference.png",
        "POSTER_TITLE_IMAGE_REFERENCE_INTERNAL_URL=https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/project-a/reference.png",
        "POSTER_TITLE_IMAGE_REFERENCE_CONTENT_TYPE=image/png",
        f"POSTER_TITLE_IMAGE_REFERENCE_SHA256={'c' * 64}",
    )

    with pytest.raises(poster_title_image.FlowError, match="requires --reference, --reference-url-ref-json, or explicit OSS URL Ref options"):
        poster_title_image.run(
            confirm_cost=True,
            confirm_upload=False,
            api_url=None,
            items_json=None,
            reference_image=None,
            reference_url_ref_json=None,
            reference_public_url=None,
            reference_internal_url=None,
            reference_sha256=None,
            reference_content_type=None,
            item_id="es",
            language="es",
            title_text="Cuando el amor se alejo",
            size="auto",
            quality="high",
            draw_count=1,
            caller_id="caller-1",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            client_request_id="poster-client-script-ref",
            json_output=True,
        )


def test_poster_title_image_keeps_uploaded_reference_when_create_response_is_unknown(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DISABLE_HTTP_AUTH_HEADER=true",
                "DISABLE_CALLER_ID_HEADER=true",
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket-a",
                "OSS_REGION=cn-hangzhou",
                "OSS_ACCESS_KEY_ID=id",
                "OSS_ACCESS_KEY_SECRET=secret",
                "OSS_PROJECT_ROOT=project-a",
            ]
        ),
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")
    reference_path = tmp_path / "reference.png"
    reference_path.write_bytes(_transparent_png_bytes())
    uploaded_image = {
        "provider": "aliyun_oss",
        "bucket": "bucket-a",
        "region": "cn-hangzhou",
        "key": "project-a/reference.png",
        "url_ref": {
            "public_url": "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/reference.png",
            "internal_url": "https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/project-a/reference.png",
            "content_type": "image/png",
            "sha256": "c" * 64,
        },
    }
    cleanup_calls = []

    monkeypatch.setattr(oss_image_upload, "upload_image", lambda **_kwargs: uploaded_image)
    monkeypatch.setattr(
        oss_image_upload,
        "delete_uploaded_image",
        lambda **kwargs: cleanup_calls.append(kwargs),
    )
    monkeypatch.setattr(http_runtime, "request_json", lambda *_args, **_kwargs: {"code": "0", "data": {}})

    with pytest.raises(FlowError, match="response missing data.job"):
        poster_title_image.run(
            confirm_cost=True,
            confirm_upload=True,
            api_url=None,
            items_json=None,
            reference_image=str(reference_path),
            reference_url_ref_json=None,
            reference_public_url=None,
            reference_internal_url=None,
            reference_sha256=None,
            reference_content_type="image/png",
            item_id="es",
            language="es",
            title_text="Cuando el amor se alejo",
            size="auto",
            quality="high",
            draw_count=1,
            caller_id="caller-1",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            client_request_id="poster-client-cleanup",
            json_output=True,
        )

    assert cleanup_calls == []


def test_poster_title_image_cleans_uploaded_reference_when_failure_happens_before_create(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DISABLE_HTTP_AUTH_HEADER=true",
                "DISABLE_CALLER_ID_HEADER=true",
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket-a",
                "OSS_REGION=cn-hangzhou",
                "OSS_ACCESS_KEY_ID=id",
                "OSS_ACCESS_KEY_SECRET=secret",
                "OSS_PROJECT_ROOT=project-a",
            ]
        ),
        encoding="utf-8",
    )
    append_root_env(tmp_path, "API_HOST=127.0.0.1", "API_PORT=18200")
    reference_path = tmp_path / "reference.png"
    reference_path.write_bytes(_transparent_png_bytes())
    uploaded_image = {
        "provider": "aliyun_oss",
        "bucket": "bucket-a",
        "region": "cn-hangzhou",
        "key": "project-a/reference.png",
        "url_ref": {
            "public_url": "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/reference.png",
            "internal_url": "https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/project-a/reference.png",
            "content_type": "image/png",
            "sha256": "c" * 64,
        },
    }
    cleanup_calls = []

    monkeypatch.setattr(oss_image_upload, "upload_image", lambda **_kwargs: uploaded_image)
    monkeypatch.setattr(
        oss_image_upload,
        "delete_uploaded_image",
        lambda **kwargs: cleanup_calls.append(kwargs),
    )
    monkeypatch.setattr(
        poster_title_image,
        "build_job_payload",
        lambda **_kwargs: (_ for _ in ()).throw(FlowError("payload failed", exit_code=4)),
    )
    monkeypatch.setattr(http_runtime, "request_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("POST must not be attempted")),
    )

    with pytest.raises(FlowError, match="payload failed"):
        poster_title_image.run(
            confirm_cost=True,
            confirm_upload=True,
            api_url=None,
            items_json=None,
            reference_image=str(reference_path),
            reference_url_ref_json=None,
            reference_public_url=None,
            reference_internal_url=None,
            reference_sha256=None,
            reference_content_type="image/png",
            item_id="es",
            language="es",
            title_text="Cuando el amor se alejo",
            size="auto",
            quality="high",
            draw_count=1,
            caller_id="caller-1",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            client_request_id="poster-client-cleanup",
            json_output=True,
        )

    assert cleanup_calls
    assert cleanup_calls[0]["upload_result"] == uploaded_image


def test_poster_title_image_local_staging_rejects_bucket_traversal(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    source = tmp_path / "reference.png"
    source.write_bytes(_transparent_png_bytes())

    with pytest.raises(poster_title_image.FlowError, match="OSS_BUCKET resolves outside"):
        poster_title_image.stage_local_reference_image(
            reference_image="reference.png",
            content_type=None,
            app_env={
                "STORAGE_BACKEND": "local",
                "LOCAL_OBJECT_STORAGE_PATH": "storage/objects",
                "OSS_BUCKET": "../../escape",
            },
        )


def test_poster_title_image_local_staging_infers_png_reference_content_type(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    source = tmp_path / "reference.png"
    source.write_bytes(_transparent_png_bytes())

    ref = poster_title_image.stage_local_reference_image(
        reference_image="reference.png",
        content_type=None,
        app_env={"STORAGE_BACKEND": "local", "LOCAL_OBJECT_STORAGE_PATH": "storage/objects"},
    )

    assert ref["content_type"] == "image/png"


def test_poster_title_image_accepts_jpeg_reference_content_type(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    source = tmp_path / "reference.jpg"
    source.write_bytes(_jpeg_bytes())

    ref = poster_title_image.stage_local_reference_image(
        reference_image="reference.jpg",
        content_type=None,
        app_env={"STORAGE_BACKEND": "local", "LOCAL_OBJECT_STORAGE_PATH": "storage/objects"},
    )

    assert ref["content_type"] == "image/jpeg"


def test_poster_title_image_rejects_undecodable_reference_image(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(poster_title_image, "ROOT_DIR", tmp_path)
    source = tmp_path / "reference.jpg"
    source.write_bytes(b"jpeg")

    with pytest.raises(poster_title_image.FlowError, match="not a decodable image"):
        poster_title_image.stage_local_reference_image(
            reference_image="reference.jpg",
            content_type=None,
            app_env={"STORAGE_BACKEND": "local", "LOCAL_OBJECT_STORAGE_PATH": "storage/objects"},
        )


def test_smoke_json_output_is_machine_readable(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    monkeypatch.setattr(env_runtime, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text("DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=true\n", encoding="utf-8")
    append_root_env(tmp_path, "API_PORT=18200")

    monkeypatch.setattr(http_runtime, "request_json",
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
            job_runtime,
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
    assert "generated by scripts/smoke.sh" in payload["summary"]["note"]
    assert payload["responses"]["create_job"]["data"]["job"]["job_id"] == "job-1"
    assert payload["responses"]["get_job"]["data"]["job"]["job_status"] == "succeeded"
    assert payload["responses"]["get_billing"]["data"]["billing"]["status"] == "estimated"
