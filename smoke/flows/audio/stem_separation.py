from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.tools.providers.aliyun_oss import AliyunOSSClient, AliyunOSSError
from smoke.flows.oss.url_ref import oss_url_ref_from_output_object
from smoke.harness import formatters
from scripts.media import audio as audio_media
from smoke.flows.image import poster_title_image
from smoke.flows.oss import image_upload as oss_image_upload
from smoke.harness import env_runtime
from smoke.harness import http_runtime
from smoke.harness import service_runtime
from smoke.harness.errors import FlowError
from smoke.jobs import runtime as job_runtime
ROOT_DIR = env_runtime.ROOT_DIR
DEFAULT_JOB_TYPE = "audio_stem_separation"
TRITON_JOB_TYPE = "audio_stem_separation_triton"
SUPPORTED_JOB_TYPES = (DEFAULT_JOB_TYPE, TRITON_JOB_TYPE)
DEFAULT_INPUT_KEY_PREFIX = "smoke/audio-stem-separation/input"
DEFAULT_OUTPUT_DIR = ".data/smoke/audio-stem-separation"
AUDIO_WAV_CONTENT_TYPE = "audio/wav"
AUDIO_INPUT_CONTENT_TYPES = frozenset({"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3"})
STEMS = ("drums", "bass", "other", "vocals")


def _resolve_repo_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR.resolve()))
    except ValueError:
        return str(path)


def _bare_sha256(data: bytes) -> str:
    return oss_image_upload.bare_sha256(data)


def validate_job_type(job_type: str) -> str:
    if job_type not in SUPPORTED_JOB_TYPES:
        raise FlowError(
            f"audio stem separation job_type must be one of: {', '.join(SUPPORTED_JOB_TYPES)}",
            exit_code=2,
        )
    return job_type


def _required_mapping_str(ref: dict[str, Any], key: str, *, label: str) -> str:
    value = ref.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FlowError(f"{label} field {key} is required", exit_code=2)
    return value.strip()


def _input_ref_from_mapping(ref: dict[str, Any], *, label: str) -> dict[str, str]:
    missing = [key for key in ("public_url", "internal_url", "sha256") if key not in ref]
    if missing:
        raise FlowError(f"{label} missing required field(s): {', '.join(missing)}", exit_code=2)
    content_type = ref.get("content_type", AUDIO_WAV_CONTENT_TYPE)
    if content_type not in AUDIO_INPUT_CONTENT_TYPES:
        raise FlowError(
            f"{label} content_type must be one of: {', '.join(sorted(AUDIO_INPUT_CONTENT_TYPES))}",
            exit_code=2,
        )
    return {
        "public_url": _required_mapping_str(ref, "public_url", label=label),
        "internal_url": _required_mapping_str(ref, "internal_url", label=label),
        "content_type": content_type,
        "sha256": _required_mapping_str(ref, "sha256", label=label).removeprefix("sha256:"),
    }


def input_ref_from_url_ref_json(path: str) -> dict[str, str]:
    if path == "-":
        import sys

        raw_text = sys.stdin.read()
        source_label = "stdin"
    else:
        source = _resolve_repo_path(path)
        if not source.is_file():
            raise FlowError(f"audio URL Ref JSON not found: {source}", exit_code=2)
        raw_text = source.read_text(encoding="utf-8")
        source_label = str(source)
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise FlowError(f"audio URL Ref JSON is invalid JSON: {exc}", exit_code=2) from exc
    if not isinstance(payload, dict):
        raise FlowError(f"audio URL Ref JSON must be an object: {source_label}", exit_code=2)
    ref = payload.get("url_ref") if isinstance(payload.get("url_ref"), dict) else payload
    if not isinstance(ref, dict):
        raise FlowError("audio URL Ref JSON must contain url_ref object or URL Ref fields", exit_code=2)
    return _input_ref_from_mapping(ref, label="audio URL Ref JSON")


def explicit_input_ref(
    *,
    public_url: str | None,
    internal_url: str | None,
    sha256: str | None,
) -> dict[str, str] | None:
    provided = [public_url, internal_url, sha256]
    if not any(provided):
        return None
    if not all(provided):
        raise FlowError(
            "--input-public-url, --input-internal-url and --input-sha256 must be provided together",
            exit_code=2,
        )
    assert public_url is not None
    assert internal_url is not None
    assert sha256 is not None
    return _input_ref_from_mapping(
        {
            "public_url": public_url,
            "internal_url": internal_url,
            "content_type": AUDIO_WAV_CONTENT_TYPE,
            "sha256": sha256,
        },
        label="explicit audio URL Ref",
    )


def _local_audio_url_ref(*, bucket: str, region: str, key: str, sha256: str, app_env: dict[str, str]) -> dict[str, str]:
    return oss_url_ref_from_output_object(
        bucket=bucket,
        region=region,
        key=key,
        content_type=AUDIO_WAV_CONTENT_TYPE,
        content_hash=f"sha256:{sha256}",
        public_endpoint=env_runtime.env_value("OSS_PUBLIC_ENDPOINT", app_env) or None,
    )


def _verify_htdemucs_input(source: Path, *, max_duration_seconds: float | None) -> dict[str, Any]:
    try:
        probe = audio_media.run_ffprobe(source)
    except SystemExit as exc:
        raise FlowError(f"ffprobe failed for htdemucs input: {source}", exit_code=int(exc.code or 2)) from exc
    checks = audio_media.htdemucs_checks(probe, max_duration_seconds)
    if not all(check["ok"] for check in checks):
        raise FlowError(f"audio does not satisfy htdemucs-input: {probe.to_dict()}", exit_code=2)
    return {
        "probe": probe.to_dict(),
        "checks": checks,
    }


def _stage_local_audio(
    *,
    input_file: str,
    app_env: dict[str, str],
    max_duration_seconds: float | None,
    key_prefix: str | None,
) -> dict[str, Any]:
    source = _resolve_repo_path(input_file)
    if not source.is_file():
        raise FlowError(f"audio input file not found: {source}", exit_code=2)
    verification = _verify_htdemucs_input(source, max_duration_seconds=max_duration_seconds)
    data = source.read_bytes()
    bucket = env_runtime.env_value("OSS_BUCKET", app_env) or "local-dev"
    region = env_runtime.env_value("OSS_REGION", app_env) or "local"
    storage_root = _resolve_repo_path(
        env_runtime.env_value("LOCAL_OBJECT_STORAGE_PATH", app_env) or "storage/objects"
    )
    prefix = (key_prefix or DEFAULT_INPUT_KEY_PREFIX).strip().strip("/")
    key = f"{prefix}/{int(time.time())}-{uuid.uuid4().hex}/{source.name}" if prefix else f"{int(time.time())}-{uuid.uuid4().hex}/{source.name}"
    root_resolved = storage_root.resolve()
    bucket_root = (storage_root / bucket).resolve()
    if bucket_root != root_resolved and root_resolved not in bucket_root.parents:
        raise FlowError("OSS_BUCKET resolves outside LOCAL_OBJECT_STORAGE_PATH", exit_code=2)
    target = (bucket_root / key).resolve()
    if target != bucket_root and bucket_root not in target.parents:
        raise FlowError("resolved local storage path escapes bucket root", exit_code=2)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    sha256 = _bare_sha256(data)
    return {
        "provider": "local",
        "bucket": bucket,
        "region": region,
        "key": key,
        "local_path": _display_path(target),
        "content_type": AUDIO_WAV_CONTENT_TYPE,
        "sha256": sha256,
        "content_size_bytes": len(data),
        "url_ref": _local_audio_url_ref(bucket=bucket, region=region, key=key, sha256=sha256, app_env=app_env),
        "verification": verification,
    }


def _upload_audio_to_aliyun_oss(
    *,
    input_file: str,
    app_env: dict[str, str],
    max_duration_seconds: float | None,
    key_prefix: str | None,
    signed_url_expires_seconds: int,
) -> dict[str, Any]:
    source = _resolve_repo_path(input_file)
    if not source.is_file():
        raise FlowError(f"audio input file not found: {source}", exit_code=2)
    verification = _verify_htdemucs_input(source, max_duration_seconds=max_duration_seconds)
    data = source.read_bytes()
    config = oss_image_upload.load_aliyun_oss_config(app_env)
    client = AliyunOSSClient(config)
    output_prefix = (env_runtime.env_value("OSS_OUTPUT_PREFIX", app_env) or "ai-jobs").strip().strip("/")
    clean_key_prefix = (key_prefix or DEFAULT_INPUT_KEY_PREFIX).strip().strip("/")
    parts = [part for part in (output_prefix, clean_key_prefix) if part]
    parts.append(f"{int(time.time())}-{uuid.uuid4().hex}")
    parts.append(source.name)
    object_key = client.object_key("/".join(parts))
    try:
        client.put_object(object_key, data, content_type=AUDIO_WAV_CONTENT_TYPE)
        signed_url = client.signed_get_url(object_key, expires_seconds=signed_url_expires_seconds)
    except AliyunOSSError as exc:
        raise FlowError(f"failed to upload audio to Aliyun OSS or generate signed URL: {exc}", exit_code=4) from exc
    content_hash = f"sha256:{_bare_sha256(data)}"
    return {
        "provider": "aliyun_oss",
        "bucket": config.bucket,
        "region": config.region,
        "key": object_key,
        "content_type": AUDIO_WAV_CONTENT_TYPE,
        "content_hash": content_hash,
        "sha256": _bare_sha256(data),
        "content_size_bytes": len(data),
        "signed_url": signed_url,
        "signed_url_expires_seconds": signed_url_expires_seconds,
        "url_ref": oss_url_ref_from_output_object(
            bucket=config.bucket,
            region=config.region,
            key=object_key,
            content_type=AUDIO_WAV_CONTENT_TYPE,
            content_hash=content_hash,
            public_endpoint=env_runtime.env_value("OSS_PUBLIC_ENDPOINT", app_env) or None,
        ),
        "verification": verification,
    }


def resolve_input_audio(
    *,
    input_file: str | None,
    input_url_ref_json: str | None,
    input_public_url: str | None,
    input_internal_url: str | None,
    input_sha256: str | None,
    app_env: dict[str, str],
    max_duration_seconds: float | None,
    confirm_upload: bool,
    key_prefix: str | None,
    signed_url_expires_seconds: int,
) -> tuple[dict[str, str], dict[str, Any] | None]:
    explicit = explicit_input_ref(public_url=input_public_url, internal_url=input_internal_url, sha256=input_sha256)
    source_count = sum([input_file is not None, input_url_ref_json is not None, explicit is not None])
    if source_count != 1:
        raise FlowError(
            "audio input source must be exactly one of --input-file, --input-url-ref-json, or explicit --input-* URL Ref options",
            exit_code=2,
        )
    if explicit is not None:
        return explicit, None
    if input_url_ref_json is not None:
        return input_ref_from_url_ref_json(input_url_ref_json), None
    assert input_file is not None
    storage_backend = env_runtime.env_value("STORAGE_BACKEND", app_env) or "local"
    if storage_backend == "aliyun_oss":
        if not confirm_upload:
            raise FlowError("audio stem separation Aliyun OSS input upload requires --confirm-upload", exit_code=2)
        uploaded = _upload_audio_to_aliyun_oss(
            input_file=input_file,
            app_env=app_env,
            max_duration_seconds=max_duration_seconds,
            key_prefix=key_prefix,
            signed_url_expires_seconds=signed_url_expires_seconds,
        )
        return uploaded["url_ref"], uploaded
    staged = _stage_local_audio(
        input_file=input_file,
        app_env=app_env,
        max_duration_seconds=max_duration_seconds,
        key_prefix=key_prefix,
    )
    return staged["url_ref"], staged


def build_job_payload(
    *,
    input_audio: dict[str, str],
    job_type: str,
    client_request_id: str | None,
    max_duration_seconds: float | None,
) -> dict[str, Any]:
    validated_job_type = validate_job_type(job_type)
    job_params: dict[str, Any] = {"input_audio": input_audio}
    if max_duration_seconds is not None:
        job_params["max_duration_seconds"] = max_duration_seconds
    return {
        "client_request_id": client_request_id or f"smoke-{validated_job_type.replace('_', '-')}-{uuid.uuid4()}",
        "job_type": validated_job_type,
        "job_params": job_params,
        "metadata": {
            "source": "scripts/smoke.sh audio-stem-separation",
            "job_type": validated_job_type,
        },
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }


def _load_payload_file(path: str, *, job_type: str | None = None) -> dict[str, Any]:
    source = _resolve_repo_path(path)
    if not source.is_file():
        raise FlowError(f"payload file not found: {source}", exit_code=2)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FlowError(f"payload file is invalid JSON: {exc}", exit_code=2) from exc
    if not isinstance(payload, dict):
        raise FlowError("payload file must contain a JSON object", exit_code=2)
    if payload.get("job_type") in SUPPORTED_JOB_TYPES and isinstance(payload.get("job_params"), dict):
        if job_type is not None and payload.get("job_type") != job_type:
            raise FlowError(f"payload job_type must match --job-type {job_type}", exit_code=2)
        return payload
    target_job_type = validate_job_type(job_type or DEFAULT_JOB_TYPE)
    if isinstance(payload.get("input_audio"), dict):
        max_duration_seconds = payload.get("max_duration_seconds")
        return build_job_payload(
            input_audio=_input_ref_from_mapping(payload["input_audio"], label="payload file input_audio"),
            job_type=target_job_type,
            client_request_id=None,
            max_duration_seconds=max_duration_seconds if isinstance(max_duration_seconds, (int, float)) else None,
        )
    if isinstance(payload.get("job_params"), dict):
        nested = payload["job_params"]
        if isinstance(nested.get("input_audio"), dict):
            max_duration_seconds = nested.get("max_duration_seconds")
            return build_job_payload(
                input_audio=_input_ref_from_mapping(nested["input_audio"], label="payload file job_params.input_audio"),
                job_type=target_job_type,
                client_request_id=str(payload.get("client_request_id")) if payload.get("client_request_id") is not None else None,
                max_duration_seconds=max_duration_seconds if isinstance(max_duration_seconds, (int, float)) else None,
            )
    raise FlowError(
        "payload file must contain audio_stem_separation/audio_stem_separation_triton create payload or job_params",
        exit_code=2,
    )


def write_or_print_payload(payload: dict[str, Any], *, output: str | None) -> None:
    if output is None or output == "-":
        formatters.print_json(payload)
        return
    target = _resolve_repo_path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    formatters.section("Audio Stem Separation Payload")
    formatters.event("OK", "payload", f"path={_display_path(target)}")


def build_payload(
    *,
    env_file: str | None,
    job_type: str,
    input_file: str | None,
    input_url_ref_json: str | None,
    input_public_url: str | None,
    input_internal_url: str | None,
    input_sha256: str | None,
    max_duration_seconds: float | None,
    client_request_id: str | None,
    confirm_upload: bool,
    key_prefix: str | None,
    signed_url_expires_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    app_env = env_runtime.load_app_env(env_file, root_dir=ROOT_DIR)
    input_audio, staged_input = resolve_input_audio(
        input_file=input_file,
        input_url_ref_json=input_url_ref_json,
        input_public_url=input_public_url,
        input_internal_url=input_internal_url,
        input_sha256=input_sha256,
        app_env=app_env,
        max_duration_seconds=max_duration_seconds,
        confirm_upload=confirm_upload,
        key_prefix=key_prefix,
        signed_url_expires_seconds=signed_url_expires_seconds,
    )
    return build_job_payload(
        input_audio=input_audio,
        job_type=job_type,
        client_request_id=client_request_id,
        max_duration_seconds=max_duration_seconds,
    ), staged_input


def _stem_outputs(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = job.get("job_result")
    if not isinstance(result, dict):
        return {}
    stems = result.get("stems")
    if not isinstance(stems, dict):
        return {}
    outputs: dict[str, dict[str, Any]] = {}
    for stem in STEMS:
        value = stems.get(stem)
        if isinstance(value, dict):
            outputs[stem] = value
    return outputs


def _filename_from_output_object(stem: str, output: dict[str, Any]) -> str:
    public_url = output.get("public_url")
    if isinstance(public_url, str):
        name = Path(urlsplit(public_url).path).name
        if name:
            return name
    return f"{stem}.wav"


def download_output_artifacts(
    *,
    job: dict[str, Any],
    app_env: dict[str, str],
    output_dir: str,
    signed_url_expires_seconds: int,
) -> list[dict[str, Any]]:
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        raise FlowError("job_id is required before downloading outputs", exit_code=4)
    outputs = _stem_outputs(job)
    missing = [stem for stem in STEMS if stem not in outputs]
    if missing:
        raise FlowError(f"audio stem result missing outputs: {missing}", exit_code=4)
    target_root = _resolve_repo_path(output_dir)
    artifacts: list[dict[str, Any]] = []
    for stem, output in outputs.items():
        expected_sha256 = output.get("sha256")
        if not isinstance(expected_sha256, str) or not expected_sha256.strip():
            raise FlowError(f"{stem} output sha256 is required before downloading outputs", exit_code=4)
        data, method = poster_title_image._read_output_bytes(
            app_env=app_env,
            output=output,
            signed_url_expires_seconds=signed_url_expires_seconds,
        )
        actual_sha256 = _bare_sha256(data)
        if actual_sha256 != expected_sha256.removeprefix("sha256:"):
            raise FlowError(f"downloaded {stem} output sha256 mismatch", exit_code=4)
        target = target_root / job_id / stem / _filename_from_output_object(stem, output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        verification = _verify_htdemucs_input(target, max_duration_seconds=None)
        artifacts.append(
            {
                "stem": stem,
                "content_type": output.get("content_type"),
                "sha256": actual_sha256,
                "sha256_verified": True,
                "download_method": method,
                "local_path": _display_path(target),
                "audio_verification": verification,
            }
        )
    return artifacts


def cleanup_staged_input(staged_input: dict[str, Any], app_env: dict[str, str]) -> None:
    provider = staged_input.get("provider")
    if provider == "aliyun_oss":
        config = oss_image_upload.load_aliyun_oss_config(app_env)
        if staged_input.get("bucket") != config.bucket:
            raise FlowError("uploaded audio cleanup bucket does not match configured OSS bucket", exit_code=4)
        if staged_input.get("region") != config.region:
            raise FlowError("uploaded audio cleanup region does not match configured OSS region", exit_code=4)
        key = staged_input.get("key")
        if not isinstance(key, str) or not key.strip():
            raise FlowError("uploaded audio cleanup requires key", exit_code=4)
        try:
            AliyunOSSClient(config).delete_object(key)
        except AliyunOSSError as exc:
            raise FlowError(f"failed to delete uploaded Aliyun OSS audio: {exc}", exit_code=4) from exc
        return
    if provider == "local":
        local_path = staged_input.get("local_path")
        if not isinstance(local_path, str) or not local_path.strip():
            raise FlowError("local staged audio cleanup requires local_path", exit_code=4)
        _resolve_repo_path(local_path).unlink()
        return
    raise FlowError(f"unsupported staged audio provider for cleanup: {provider}", exit_code=4)


def should_cleanup_staged_input(*, create_attempted: bool, terminal_job: dict[str, Any] | None) -> bool:
    return not create_attempted or terminal_job is not None


def cleanup_failure_message(original: BaseException, cleanup_exc: BaseException) -> str:
    return f"{original}; staged audio cleanup failed: {cleanup_exc}"


def summarize(job: dict[str, Any], *, artifacts: list[dict[str, Any]] | None = None, staged_input: dict[str, Any] | None = None) -> dict[str, Any]:
    outputs = _stem_outputs(job)
    return {
        "note": "summary is generated by scripts/smoke.sh; raw HTTP envelopes are under responses",
        "job_id": job.get("job_id"),
        "job_status": job.get("job_status"),
        "job_type": job.get("job_type"),
        "stems_count": len(outputs),
        "stems": outputs,
        "artifacts": artifacts or [],
        "staged_input": staged_input,
    }


def conclusion(job: dict[str, Any], artifacts: list[dict[str, Any]]) -> str:
    return f"job={job.get('job_status')} stems={len(_stem_outputs(job))} artifacts={len(artifacts)}"


def _callback_state(job: dict[str, Any]) -> dict[str, Any] | None:
    callback = job.get("callback")
    return callback if isinstance(callback, dict) else None


def _callback_status(job: dict[str, Any]) -> str:
    callback = _callback_state(job)
    status = callback.get("status") if callback is not None else None
    return str(status) if status is not None else "-"


def _callback_display(job: dict[str, Any]) -> dict[str, Any]:
    callback = _callback_state(job)
    if callback is None:
        return {
            "callback_status": "-",
            "callback_attempt": "-",
            "callback_next_retry_at": "-",
            "callback_last_error_reason": "-",
        }
    last_error = callback.get("last_error")
    last_error_reason = last_error.get("reason") if isinstance(last_error, dict) else None
    return {
        "callback_status": callback.get("status"),
        "callback_attempt": callback.get("attempt"),
        "callback_next_retry_at": callback.get("next_retry_at"),
        "callback_last_error_reason": last_error_reason,
    }


def _progress_summary(job: dict[str, Any]) -> str:
    progress = job.get("job_progress")
    if not isinstance(progress, dict):
        return "stage=- percent=-"
    stage = progress.get("stage") or "-"
    percent = progress.get("percent")
    if percent is None:
        percent_text = "-"
    else:
        percent_text = f"{percent}%"
    message = progress.get("message") or "-"
    return f"stage={stage} percent={percent_text} message={formatters.compact(message, max_length=48)}"


def _input_source_label(
    *,
    payload_file: str | None,
    input_file: str | None,
    input_url_ref_json: str | None,
    input_public_url: str | None,
) -> str:
    if payload_file is not None:
        return f"payload-file {payload_file}"
    if input_file is not None:
        return f"input-file {input_file}"
    if input_url_ref_json is not None:
        return "url-ref-json"
    if input_public_url is not None:
        return "url-ref-cli"
    return "default"


def _staged_input_summary(staged_input: dict[str, Any] | None) -> str:
    if staged_input is None:
        return "source=payload"
    provider = staged_input.get("provider", "-")
    bucket = staged_input.get("bucket")
    key = staged_input.get("key")
    if bucket or key:
        return f"provider={provider} bucket={bucket or '-'} key={key or '-'}"
    local_path = staged_input.get("local_path")
    return f"provider={provider} local_path={local_path or '-'}"


def _diagnostic_hint(job_id: str) -> None:
    formatters.event("INFO", "debug", f"./scripts/jobs.sh show {job_id}")
    formatters.event("INFO", "debug", f"./scripts/jobs.sh timeline {job_id}")
    formatters.event("INFO", "debug", f"./scripts/jobs.sh attempts {job_id}")


def run(
    *,
    confirm_run: bool,
    confirm_upload: bool,
    api_url: str | None,
    env_file: str | None,
    allow_remote_api: bool,
    service_api_key: str | None,
    caller_id: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
    job_type: str,
    client_request_id: str | None,
    payload_file: str | None,
    input_file: str | None,
    input_url_ref_json: str | None,
    input_public_url: str | None,
    input_internal_url: str | None,
    input_sha256: str | None,
    max_duration_seconds: float | None,
    key_prefix: str | None,
    signed_url_expires_seconds: int,
    download_outputs: bool,
    output_dir: str,
    json_output: bool,
) -> None:
    if not confirm_run:
        raise FlowError("audio stem separation smoke requires --confirm-run", exit_code=2)
    target_job_type = validate_job_type(job_type)
    if not json_output:
        formatters.section("Audio Stem Separation Smoke")
    context = job_runtime.resolve_job_context(
        env_file=env_file,
        api_url=api_url,
        allow_remote_api=allow_remote_api,
        caller_id=caller_id,
        service_api_key=service_api_key,
    )
    app_env = context.app_env
    staged_input: dict[str, Any] | None = None
    jobs_url = str(context.summary["jobs_url"])
    headers = service_runtime.build_headers(app_env, caller_id=caller_id, service_api_key=service_api_key)
    if not json_output:
        formatters.event(
            "OK",
            "preflight",
            f"base_url={context.summary['api_url']} storage={context.summary['storage_backend']}",
        )
        formatters.event(
            "RUN",
            "prepare",
            f"job_type={target_job_type} input={_input_source_label(payload_file=payload_file, input_file=input_file, input_url_ref_json=input_url_ref_json, input_public_url=input_public_url)}",
        )
    create_attempted = False
    terminal_job: dict[str, Any] | None = None
    job_id: str | None = None
    try:
        if payload_file is not None:
            if any([input_file, input_url_ref_json, input_public_url, input_internal_url, input_sha256]):
                raise FlowError("--payload-file cannot be combined with audio input source options", exit_code=2)
            payload = _load_payload_file(payload_file, job_type=target_job_type)
        else:
            payload, staged_input = build_payload(
                env_file=env_file,
                job_type=target_job_type,
                input_file=input_file,
                input_url_ref_json=input_url_ref_json,
                input_public_url=input_public_url,
                input_internal_url=input_internal_url,
                input_sha256=input_sha256,
                max_duration_seconds=max_duration_seconds,
                client_request_id=client_request_id,
                confirm_upload=confirm_upload,
                key_prefix=key_prefix,
                signed_url_expires_seconds=signed_url_expires_seconds,
            )
        if payload.get("job_type") not in SUPPORTED_JOB_TYPES:
            raise FlowError(f"payload job_type must be one of: {', '.join(SUPPORTED_JOB_TYPES)}", exit_code=2)
        if client_request_id is not None:
            payload["client_request_id"] = client_request_id

        if not json_output:
            formatters.event("OK", "prepare", _staged_input_summary(staged_input))
            formatters.event("RUN", "submit", f"url={jobs_url}")
        create_attempted = True
        create_envelope = http_runtime.request_json(jobs_url, method="POST", headers=headers, payload=payload)
        created = http_runtime.data_object(create_envelope, "job")
        job_id = str(created["job_id"])
        if not json_output:
            formatters.event("OK", "submit", f"id={job_id} status={created.get('job_status')}")
            formatters.event("RUN", "poll", f"timeout={timeout_seconds}s interval={poll_interval_seconds}s")

        def progress_callback(job: dict[str, Any], elapsed_seconds: float) -> None:
            if json_output:
                return
            formatters.event(
                "WAIT",
                "job",
                (
                    f"id={job_id} status={job.get('job_status')} "
                    f"callback={_callback_status(job)} elapsed={int(elapsed_seconds)}s "
                    f"{_progress_summary(job)}"
                ),
            )

        get_job_envelope = job_runtime.poll_job_envelope(
            jobs_url=jobs_url,
            job_id=job_id,
            headers=headers,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            progress_callback=progress_callback,
        )
        terminal_job = http_runtime.data_object(get_job_envelope, "job")
    except Exception as exc:
        if staged_input is not None and should_cleanup_staged_input(
            create_attempted=create_attempted,
            terminal_job=terminal_job,
        ):
            try:
                if not json_output:
                    formatters.event("RUN", "cleanup", _staged_input_summary(staged_input))
                cleanup_staged_input(staged_input, app_env)
                if not json_output:
                    formatters.event("OK", "cleanup", "staged input removed")
            except Exception as cleanup_exc:
                exit_code = exc.exit_code if isinstance(exc, FlowError) else 4
                raise FlowError(cleanup_failure_message(exc, cleanup_exc), exit_code=exit_code) from exc
        if job_id is not None and not json_output:
            _diagnostic_hint(job_id)
        raise
    assert terminal_job is not None
    assert job_id is not None
    job_succeeded = terminal_job.get("job_status") == "succeeded"
    if staged_input is not None:
        if not json_output:
            formatters.event("RUN", "cleanup", _staged_input_summary(staged_input))
        cleanup_staged_input(staged_input, app_env)
        if not json_output:
            formatters.event("OK", "cleanup", "staged input removed")
    artifacts = []
    if download_outputs and job_succeeded:
        if not json_output:
            formatters.event("RUN", "artifacts", f"download_outputs=true output_dir={output_dir}")
        artifacts = download_output_artifacts(
            job=terminal_job,
            app_env=app_env,
            output_dir=output_dir,
            signed_url_expires_seconds=signed_url_expires_seconds,
        )
        if not json_output:
            formatters.event("OK", "artifacts", f"downloaded={len(artifacts)}")
    summary = summarize(terminal_job, artifacts=artifacts, staged_input=staged_input)
    summary["context"] = context.summary
    if json_output:
        formatters.print_json(
            {
                "conclusion": conclusion(terminal_job, artifacts),
                "summary": summary,
                "responses": {
                    "create_job": create_envelope,
                    "get_job": get_job_envelope,
                },
            }
        )
    else:
        formatters.event(
            "OK" if job_succeeded else "ERROR",
            "job",
            f"id={job_id} status={terminal_job.get('job_status')} callback={_callback_status(terminal_job)}",
        )
        if job_succeeded:
            formatters.event("OK", "assert", f"stems={summary['stems_count']}")
        else:
            formatters.event("ERROR", "assert", f"job_error={formatters.compact(terminal_job.get('job_error'))}")
        display_summary = {**summary, **_callback_display(terminal_job)}
        formatters.print_table(
            [display_summary],
            [
                ("job_id", "job_id"),
                ("job_status", "job"),
                ("job_type", "type"),
                ("callback_status", "callback"),
                ("callback_attempt", "cb_try"),
                ("callback_last_error_reason", "cb_error"),
                ("stems_count", "stems"),
            ],
        )
        if artifacts:
            formatters.print_table(
                artifacts,
                [
                    ("stem", "stem"),
                    ("content_type", "content_type"),
                    ("sha256_verified", "sha256"),
                    ("download_method", "method"),
                    ("local_path", "local_path"),
                ],
            )
    if not job_succeeded:
        if not json_output:
            _diagnostic_hint(job_id)
        raise FlowError(f"job {job_id} finished with {terminal_job.get('job_status')}", exit_code=1)
