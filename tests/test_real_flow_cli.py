import json
import io
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from scripts.real_flow.cli import app
from app.integrations.aliyun_oss import AliyunOSSConfig
from app.integrations.ai_adapters.base import ImageGenerationResult
from scripts.real_flow.flows import (
    adapter_image_probe,
    audio_stem_separation,
    llm_job_billing,
    oss_image_upload,
    poster_title_image,
)


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


def append_root_env(root: Path, *lines: str) -> None:
    env_path = root / ".env"
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    prefix = existing.rstrip("\n")
    suffix = "\n".join(lines)
    env_path.write_text(f"{prefix}\n{suffix}\n" if prefix else f"{suffix}\n", encoding="utf-8")


def test_real_flow_cli_requires_confirm_cost():
    result = runner.invoke(app, ["llm-job-billing"])

    assert result.exit_code == 2
    assert "real LLM flow requires --confirm-cost" in result.stderr


def test_poster_title_image_cli_requires_confirm_cost():
    result = runner.invoke(app, ["poster-title-image"])

    assert result.exit_code == 2
    assert "poster title image flow requires --confirm-cost" in result.stderr


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


def test_real_flow_doctor_prints_resolved_context(tmp_path, monkeypatch):
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
    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
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
            "doctor",
            "--env-file",
            "env_test/.env",
            "--allow-remote-api",
            "--x-ai-service-caller-id",
            "default",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["api_url"] == "http://test.example.com"
    assert payload["api_url_source"] == "env_file"
    assert payload["service_api_key_source"] == "env_file"
    assert payload["caller_id"] == "default"
    assert payload["storage_backend"] == "aliyun_oss"
    assert payload["oss_public_endpoint"] == "cdn.example.com"
    assert payload["ready"] is True
    assert payload["problems"] == []


def test_real_flow_doctor_rejects_missing_service_api_key(tmp_path, monkeypatch):
    for name in ["API_URL", "SERVICE_API_KEY", "DISABLE_HTTP_AUTH_HEADER"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text("API_URL=http://127.0.0.1:8100\n", encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ready"] is False
    assert payload["problems"] == ["SERVICE_API_KEY is required unless DISABLE_HTTP_AUTH_HEADER=true"]


def test_llm_job_billing_cli_accepts_remote_api_and_auth_options(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(llm_job_billing, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "llm-job-billing",
            "--allow-remote-api",
            "--api-url",
            "http://test-cms-poster-title.epubgame.com",
            "--env-file",
            "env_test/.env",
            "--service-api-key",
            "test-token",
            "--x-ai-service-caller-id",
            "default",
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
            "llm-job-double-billing",
            "--allow-remote-api",
            "--api-url",
            "http://test-cms-poster-title.epubgame.com",
            "--env-file",
            "env_test/.env",
            "--service-api-key",
            "test-token",
            "--x-ai-service-caller-id",
            "default",
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
            "poster-title-image",
            "--allow-remote-api",
            "--api-url",
            "http://test-cms-poster-title.epubgame.com",
            "--env-file",
            "env_test/.env",
            "--service-api-key",
            "test-token",
            "--x-ai-service-caller-id",
            "default",
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


def test_poster_title_image_cli_keeps_legacy_caller_id_option(monkeypatch):
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
            "--caller-id",
            "legacy-caller",
        ],
    )

    assert result.exit_code == 0
    assert captured["caller_id"] == "legacy-caller"


def test_oss_upload_image_cli_accepts_env_file(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(oss_image_upload, "run", fake_run)

    result = runner.invoke(
        app,
        [
            "oss-upload-image",
            "--confirm-upload",
            "--env-file",
            "env_test/.env",
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
            "audio-stem-separation",
            "build-payload",
            "--env-file",
            "env_test/.env",
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
            "real-flow/audio/input",
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
        "key_prefix": "real-flow/audio/input",
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
            "audio-stem-separation",
            "run",
            "--confirm-run",
            "--confirm-upload",
            "--api-url",
            "http://127.0.0.1:18200",
            "--env-file",
            "env_test/.env",
            "--allow-remote-api",
            "--service-api-key",
            "test-token",
            "--x-ai-service-caller-id",
            "default",
            "--timeout-seconds",
            "10",
            "--poll-interval-seconds",
            "0.5",
            "--client-request-id",
            "audio-client-2",
            "--job-type",
            "audio_stem_separation_triton",
            "--payload-file",
            ".run/audio-payload.json",
            "--download-outputs",
            "--output-dir",
            ".run/audio-stems",
            "--json",
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
            "adapter-image-probe",
            "--confirm-cost",
            "--env-file",
            "env_test/.env",
            "--models-config",
            "app/jobs/types/poster_title_image/models.yaml",
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
            "--timeout-seconds",
            "45",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "confirm_cost": True,
        "env_file": "env_test/.env",
        "models_config": "app/jobs/types/poster_title_image/models.yaml",
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
                "public_model_selection:",
                "  default_model_id: gpt-image-from-config",
                "internal_models:",
                "  style_probe:",
                "    model_id: gpt-response-from-config",
                "generation:",
                "  image_adapter: openai_responses",
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

    with pytest.raises(llm_job_billing.FlowError, match="image/jpg"):
        oss_image_upload.image_content_type(image_path, "image/jpg")


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


def test_real_flow_builds_poster_title_image_payload():
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


def test_real_flow_builds_poster_title_image_payload_with_caller_model_id():
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


def test_real_flow_builds_audio_stem_separation_payload():
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
            "source": "scripts/real-flow.sh audio-stem-separation",
            "job_type": "audio_stem_separation",
        },
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }


def test_real_flow_builds_audio_stem_separation_payload_with_mp3_ref():
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


def test_real_flow_builds_audio_stem_separation_triton_payload():
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
        "source": "scripts/real-flow.sh audio-stem-separation",
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
    monkeypatch.setattr(llm_job_billing, "request_json", fake_request_json)
    monkeypatch.setattr(
        llm_job_billing,
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


def test_oss_image_upload_builds_url_ref_with_fake_client(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setattr(oss_image_upload, "ROOT_DIR", tmp_path)
    source = tmp_path / "reference.png"
    source.write_bytes(b"png-reference")
    calls = []

    class FakeClient:
        config = AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
            project_root="project-a",
        )

        def object_key(self, key):
            return f"project-a/{key.strip('/')}" if not key.startswith("project-a/") else key

        def put_object(self, key, data, *, content_type):
            calls.append({"key": key, "data": data, "content_type": content_type})

        def signed_get_url(self, key, *, expires_seconds):
            assert key == "project-a/inputs/reference.png"
            assert expires_seconds == 1800
            return "https://signed.example.com/project-a/inputs/reference.png?Signature=sig"

    result = oss_image_upload.upload_image(
        image="reference.png",
        content_type=None,
        app_env={"OSS_OUTPUT_PREFIX": "outputs", "OSS_PUBLIC_ENDPOINT": "aigc-datas.epubgame.com"},
        key="inputs/reference.png",
        signed_url_expires_seconds=1800,
        client=FakeClient(),
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


def test_real_flow_headers_use_explicit_service_key(monkeypatch):
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    headers = llm_job_billing.build_headers(
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


def test_real_flow_load_app_env_uses_explicit_file(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text("API_URL=http://test.example.com\nSERVICE_API_KEY=file-token\n", encoding="utf-8")

    values = llm_job_billing.load_app_env("env_test/.env")

    assert values["API_URL"] == "http://test.example.com"
    assert values["SERVICE_API_KEY"] == "file-token"


def test_real_flow_load_app_env_rejects_missing_explicit_file(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)

    with pytest.raises(llm_job_billing.FlowError) as exc:
        llm_job_billing.load_app_env("env_test/.env")

    assert exc.value.exit_code == 2
    assert "env file not found" in str(exc.value)


def test_real_flow_env_value_prefers_runtime_env(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "runtime-token")

    value = llm_job_billing.env_value("SERVICE_API_KEY", {"SERVICE_API_KEY": "file-token"})

    assert value == "runtime-token"


def test_real_flow_resolves_api_url_from_root_env():
    api_url = llm_job_billing.resolved_api_url(None, {"API_HOST": "127.0.0.1", "API_PORT": "18200"})

    assert api_url == "http://127.0.0.1:18200"


def test_real_flow_rejects_non_local_api_url():
    with pytest.raises(llm_job_billing.FlowError) as exc:
        llm_job_billing.resolved_api_url("https://api.example.com", {})

    assert exc.value.exit_code == 2
    assert "only targets local API URLs" in str(exc.value)


def test_real_flow_accepts_remote_api_url_when_explicitly_allowed():
    api_url = llm_job_billing.resolved_api_url(
        "https://api.example.com",
        {},
        allow_remote_api=True,
    )

    assert api_url == "https://api.example.com"


@pytest.mark.parametrize("api_url", ["https://127.example.com", "https://127.0.0.1.nip.io"])
def test_real_flow_rejects_loopback_prefix_hostnames(api_url):
    with pytest.raises(llm_job_billing.FlowError) as exc:
        llm_job_billing.resolved_api_url(api_url, {})

    assert exc.value.exit_code == 2
    assert "only targets local API URLs" in str(exc.value)


def test_real_flow_accepts_loopback_ip_url():
    api_url = llm_job_billing.resolved_api_url("http://127.0.0.1:18200", {})

    assert api_url == "http://127.0.0.1:18200"


def test_real_flow_run_uses_http_job_and_billing_flow(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_MODEL_ID", raising=False)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    app_env = tmp_path / ".env"
    app_env.write_text(
        "DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=false\nDEFAULT_MODEL_ID=gpt-5.4-mini\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
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


def test_real_flow_run_uses_env_file_for_remote_api_and_service_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_MODEL_ID", raising=False)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
    env_dir = tmp_path / "env_test"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "\n".join(
            [
                "API_URL=http://test-cms-poster-title.epubgame.com",
                "SERVICE_API_KEY=file-secret",
                "DISABLE_HTTP_AUTH_HEADER=false",
                "DISABLE_CALLER_ID_HEADER=false",
                "DEFAULT_MODEL_ID=gpt-5.4-mini",
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

    monkeypatch.setattr(llm_job_billing, "request_json", fake_request_json)
    monkeypatch.setattr(
        llm_job_billing,
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
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
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

    monkeypatch.setattr(llm_job_billing, "request_json", fake_request_json)
    monkeypatch.setattr(
        llm_job_billing,
        "poll_job_envelope",
        lambda **kwargs: {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "audio-job-1",
                    "job_status": "succeeded",
                    "job_type": "audio_stem_separation",
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
        },
    )

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

    result = json.loads(capsys.readouterr().out)
    assert result["summary"]["job_id"] == "audio-job-1"
    assert result["summary"]["job_type"] == "audio_stem_separation"
    assert result["summary"]["stems_count"] == 4
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "http://127.0.0.1:18200/api/v1/ai-jobs/jobs"
    assert calls[0]["payload"]["job_type"] == "audio_stem_separation"
    assert calls[0]["payload"]["job_params"]["input_audio"] == input_audio


def test_audio_stem_separation_run_does_not_download_outputs_for_failed_job(tmp_path, monkeypatch, capsys):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
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

    monkeypatch.setattr(
        llm_job_billing,
        "request_json",
        lambda url, *, method, headers, payload=None, timeout_seconds=10: {
            "code": "0",
            "data": {"job": {"job_id": "audio-job-failed", "job_status": "queued"}},
        },
    )
    monkeypatch.setattr(
        llm_job_billing,
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


def test_audio_stem_separation_run_cleans_staged_input_after_terminal_job(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
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
    monkeypatch.setattr(
        llm_job_billing,
        "request_json",
        lambda url, *, method, headers, payload=None, timeout_seconds=10: {
            "code": "0",
            "data": {"job": {"job_id": "audio-job-cleanup", "job_status": "queued"}},
        },
    )
    monkeypatch.setattr(
        llm_job_billing,
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
    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
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
    monkeypatch.setattr(llm_job_billing, "request_json", lambda *_args, **_kwargs: {"code": "0", "data": {}})

    with pytest.raises(llm_job_billing.FlowError, match="response missing data.job"):
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


def test_real_flow_run_uses_double_job_type(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_MODEL_ID", raising=False)
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text("DISABLE_HTTP_AUTH_HEADER=true\nDEFAULT_MODEL_ID=gpt-5.4-mini\n", encoding="utf-8")
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


def test_real_flow_run_uses_poster_title_image_api_flow(tmp_path, monkeypatch, capsys):
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

    monkeypatch.setattr(llm_job_billing, "request_json", fake_request_json)
    monkeypatch.setattr(
        llm_job_billing,
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
    staged = list((tmp_path / "storage/objects/local-dev/real-flow/poster-title-image/reference").glob("**/英语.png"))
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

    monkeypatch.setattr(
        llm_job_billing,
        "request_json",
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
        llm_job_billing,
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


def test_real_flow_run_uploads_poster_reference_when_aliyun_oss_enabled(tmp_path, monkeypatch, capsys):
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
    monkeypatch.setattr(llm_job_billing, "request_json", fake_request_json)
    monkeypatch.setattr(
        llm_job_billing,
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
    assert upload_calls[0]["key_prefix"] == "real-flow/poster-title-image/reference"
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


def test_real_flow_run_ignores_env_reference_url_ref_by_default(tmp_path, monkeypatch):
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
    monkeypatch.setattr(llm_job_billing, "request_json", lambda *_args, **_kwargs: {"code": "0", "data": {}})

    with pytest.raises(llm_job_billing.FlowError, match="response missing data.job"):
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
        lambda **_kwargs: (_ for _ in ()).throw(llm_job_billing.FlowError("payload failed", exit_code=4)),
    )
    monkeypatch.setattr(
        llm_job_billing,
        "request_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("POST must not be attempted")),
    )

    with pytest.raises(llm_job_billing.FlowError, match="payload failed"):
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


def test_real_flow_json_output_is_machine_readable(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DISABLE_HTTP_AUTH_HEADER", raising=False)
    monkeypatch.delenv("DISABLE_CALLER_ID_HEADER", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)

    monkeypatch.setattr(llm_job_billing, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text("DISABLE_HTTP_AUTH_HEADER=true\nDISABLE_CALLER_ID_HEADER=true\n", encoding="utf-8")
    append_root_env(tmp_path, "API_PORT=18200")

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
