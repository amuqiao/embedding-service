"""模拟后端调用小说本地化服务，验证真实多阶段 Job 链路。

默认流程：
  1. 从 .data 选取第一个 .txt 原文。
  2. 枚举 health、models、prompt-templates，并提交错误请求做契约预检。
  3. 启动本地 callback receiver。
  4. 提交 step1_localize，产出 localized.txt。
  5. 将 localized.txt 作为输入提交 step2_review，记录校验结果。
  6. 将 localized.txt 作为输入提交 step3_translate，产出 translated.txt。
  7. 校验每个终态 Job 的轮询结果与 callback body/header/signature 一致。

脚本只通过 HTTP API 调用本服务，用于验证“后端调用方 -> 服务 API -> Celery -> OpenAI -> 本地对象存储”的完整链路。
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import threading
import json
import os
import sys
import time
from contextlib import nullcontext
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.infrastructure.storage import storage as object_storage

DEFAULT_BASE_URL = "http://127.0.0.1:8100"
DEFAULT_DATA_DIR = ROOT_DIR / ".data"
DEFAULT_STORAGE_DIR = ROOT_DIR / "storage" / "objects"
PREFERRED_OPENAI_MODELS = ("gpt-4o-mini", "gpt-4o", "gpt-4.1")


@dataclass(frozen=True)
class Config:
    base_url: str
    service_api_key: str
    input_file: Path
    model_id: str | None
    output_bucket: str
    output_prefix: str
    output_region: str
    poll_interval: float
    timeout_seconds: int
    storage_dir: Path
    repeat_input: int
    dry_run: bool
    contract_only: bool
    contract_check: bool
    callback_port: int
    callback_wait_seconds: int
    callback_signing_secret: str


@dataclass(frozen=True)
class StageSpec:
    name: str
    job_type: str
    input_label: str
    expected_artifact_key: str | None


@dataclass(frozen=True)
class StageResult:
    stage: StageSpec
    job_id: str
    status: str
    output_paths: list[Path]
    final_body: dict[str, Any]


@dataclass(frozen=True)
class CallbackRecord:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]
    raw_body: bytes


STAGES = (
    StageSpec(
        name="step1_localize",
        job_type="novel_localization.step1_localize",
        input_label="source",
        expected_artifact_key="localized_text",
    ),
    StageSpec(
        name="step2_review",
        job_type="novel_localization.step2_review",
        input_label="localized",
        expected_artifact_key=None,
    ),
    StageSpec(
        name="step3_translate",
        job_type="novel_localization.step3_translate",
        input_label="localized",
        expected_artifact_key="translated_text",
    ),
)

STAGE_LABELS = {
    "step1_localize": "本地化",
    "step2_review": "本地化校验",
    "step3_translate": "翻译",
}


def stage_label(stage: StageSpec) -> str:
    return STAGE_LABELS.get(stage.name, stage.name)


class CallbackStore:
    def __init__(self) -> None:
        self._records: list[CallbackRecord] = []
        self._condition = threading.Condition()

    def append(self, record: CallbackRecord) -> None:
        with self._condition:
            self._records.append(record)
            self._condition.notify_all()

    def snapshot(self) -> list[CallbackRecord]:
        with self._condition:
            return list(self._records)

    def wait_for_job(self, job_id: str, timeout_seconds: int) -> CallbackRecord:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while True:
                for record in self._records:
                    if str(record.body.get("job_id")) == job_id:
                        return record
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"未收到 callback，job_id={job_id}")
                self._condition.wait(timeout=remaining)


class CallbackHandler(BaseHTTPRequestHandler):
    server: "CallbackHTTPServer"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        raw_body = self.rfile.read(length)
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except Exception:
            body = {"_invalid_json": raw_body.decode("utf-8", errors="replace")}
        self.server.store.append(
            CallbackRecord(
                path=self.path,
                headers={key: value for key, value in self.headers.items()},
                body=body,
                raw_body=raw_body,
            )
        )
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


class CallbackHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], store: CallbackStore) -> None:
        super().__init__(server_address, CallbackHandler)
        self.store = store


class CallbackReceiver:
    def __init__(self, port: int) -> None:
        self.store = CallbackStore()
        self.server = CallbackHTTPServer(("127.0.0.1", port), self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/callbacks/novel-localization"

    def __enter__(self) -> "CallbackReceiver":
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def load_dotenv_value(key: str) -> str | None:
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name == key:
            return value.strip().strip('"').strip("'")
    return None


def default_service_api_key() -> str:
    return os.getenv("SERVICE_API_KEY") or load_dotenv_value("SERVICE_API_KEY") or "dev-service-key"


def default_callback_signing_secret() -> str:
    return os.getenv("CALLBACK_SIGNING_SECRET") or load_dotenv_value("CALLBACK_SIGNING_SECRET") or ""


def first_txt_file(data_dir: Path) -> Path:
    if not data_dir.exists():
        raise FileNotFoundError(f"找不到数据目录: {data_dir}")
    files = sorted(path for path in data_dir.rglob("*.txt") if path.is_file())
    if not files:
        raise FileNotFoundError(f"数据目录下没有 .txt 文件: {data_dir}")
    return files[0]


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_object_ref(config: Config, stage: StageSpec, input_text: str, *, write_object: bool) -> dict[str, dict[str, str]]:
    content_hash = sha256_text(input_text)
    object_key = (
        config.output_prefix.rstrip("/")
        + f"/inputs/{stage.name}-{int(time.time())}.txt"
    )
    if write_object:
        object_storage.write_text(
            bucket=config.output_bucket,
            key=object_key,
            region=config.output_region,
            content=input_text,
        )
    return {
        "oss": {
            "oss_key": object_key,
            "oss_url": f"local://{object_key}",
            "content_hash": content_hash,
            "content_type": "text/plain; charset=utf-8",
        },
    }


def select_model(models_body: dict[str, Any], requested_model_id: str | None) -> str:
    available = {item["id"] for item in models_body["models"]}
    if requested_model_id:
        if requested_model_id not in available:
            raise RuntimeError(f"模型不可用: {requested_model_id}; 可用模型={sorted(available)}")
        return requested_model_id

    for model_id in PREFERRED_OPENAI_MODELS:
        if model_id in available:
            return model_id

    raise RuntimeError(
        "服务未返回可用 OpenAI 模型；请检查 .env 中的 OPENAI_API_KEY，"
        "或通过 --model-id 显式指定模型"
    )


def load_prompt_blocks(templates_body: dict[str, Any], job_type: str) -> list[dict[str, str]]:
    template = next((item for item in templates_body["job_types"] if item["job_type"] == job_type), None)
    if not template:
        raise RuntimeError(f"job_type 不可用: {job_type}")
    return [
        {"key": block["key"], "role": block["role"], "content": block["default_content"]}
        for block in template["prompt_blocks"]
    ]


def require_error(response: httpx.Response, *, status_code: int, code: str, label: str) -> None:
    if response.status_code != status_code:
        raise RuntimeError(f"{label}: 期望 HTTP {status_code}，实际 HTTP {response.status_code}: {response.text}")
    body = response.json()
    actual_code = ((body.get("error") or {}).get("code"))
    if actual_code != code:
        raise RuntimeError(f"{label}: 期望错误码 {code}，实际错误码 {actual_code}: {body}")
    print(f"[契约] {label}: HTTP {status_code} {code}")


def validate_meta_contract(
    *,
    client: httpx.Client,
    headers: dict[str, str],
    models_body: dict[str, Any],
    templates_body: dict[str, Any],
    model_id: str,
) -> None:
    healthz = client.get("/healthz")
    healthz.raise_for_status()

    unauthorized = client.get("/api/v1/novel-localization-ai/models")
    require_error(unauthorized, status_code=401, code="UNAUTHORIZED", label="未鉴权 models 已拒绝")

    model_ids = {item.get("id") for item in models_body.get("models") or []}
    if model_id not in model_ids:
        raise RuntimeError(f"/models 未包含选中的模型: {model_id}")
    if not models_body.get("default_model_id"):
        raise RuntimeError("/models default_model_id 为空")

    templates = {item.get("job_type"): item for item in templates_body.get("job_types") or []}
    expected_job_types = {stage.job_type for stage in STAGES}
    missing = expected_job_types - set(templates)
    if missing:
        raise RuntimeError(f"/prompt-templates 缺少 job_types: {sorted(missing)}")
    for stage in STAGES:
        blocks = templates[stage.job_type].get("prompt_blocks") or []
        roles = {block.get("key"): block.get("role") for block in blocks}
        if roles != {"system": "system", "user": "user", "work_note": "user"}:
            raise RuntimeError(f"{stage_label(stage)} prompt blocks 不符合预期: {roles}")

    print("[契约] meta: health、auth、models、prompt-templates 校验通过")


def run_create_job_contract_checks(
    *,
    config: Config,
    client: httpx.Client,
    headers: dict[str, str],
    model_id: str,
    templates_body: dict[str, Any],
    source_text: str,
    callback_url: str,
) -> None:
    stage = STAGES[0]
    prompt_blocks = load_prompt_blocks(templates_body, stage.job_type)

    payload = create_payload(
        config=config,
        stage=stage,
        model_id="__missing_model__",
        prompt_blocks=prompt_blocks,
        input_text=source_text,
        callback_url=callback_url,
        write_source=False,
    )
    require_error(
        client.post("/api/v1/novel-localization-ai/jobs", headers=headers, json=payload),
        status_code=422,
        code="MODEL_NOT_AVAILABLE",
        label="非法 model_id 已拒绝",
    )

    payload = create_payload(
        config=config,
        stage=stage,
        model_id=model_id,
        prompt_blocks=prompt_blocks,
        input_text=source_text,
        callback_url=callback_url,
        write_source=False,
    )
    payload["job_type"] = "novel_localization.unknown"
    require_error(
        client.post("/api/v1/novel-localization-ai/jobs", headers=headers, json=payload),
        status_code=422,
        code="INVALID_JOB_TYPE",
        label="非法 job_type 已拒绝",
    )

    payload = create_payload(
        config=config,
        stage=stage,
        model_id=model_id,
        prompt_blocks=prompt_blocks[:-1],
        input_text=source_text,
        callback_url=callback_url,
        write_source=False,
    )
    require_error(
        client.post("/api/v1/novel-localization-ai/jobs", headers=headers, json=payload),
        status_code=422,
        code="INVALID_INPUT",
        label="缺失 prompt block 已拒绝",
    )

    duplicate_blocks = prompt_blocks + [dict(prompt_blocks[-1])]
    payload = create_payload(
        config=config,
        stage=stage,
        model_id=model_id,
        prompt_blocks=duplicate_blocks,
        input_text=source_text,
        callback_url=callback_url,
        write_source=False,
    )
    require_error(
        client.post("/api/v1/novel-localization-ai/jobs", headers=headers, json=payload),
        status_code=422,
        code="INVALID_INPUT",
        label="重复 prompt block 已拒绝",
    )

    payload = create_payload(
        config=config,
        stage=stage,
        model_id=model_id,
        prompt_blocks=prompt_blocks,
        input_text=source_text,
        callback_url=callback_url,
        write_source=False,
    )
    payload["source"]["oss"]["content_type"] = "application/json"
    require_error(
        client.post("/api/v1/novel-localization-ai/jobs", headers=headers, json=payload),
        status_code=422,
        code="INVALID_INPUT",
        label="非法 content_type 已拒绝",
    )


def create_payload(
    *,
    config: Config,
    stage: StageSpec,
    model_id: str,
    prompt_blocks: list[dict[str, str]],
    input_text: str,
    callback_url: str | None,
    write_source: bool = True,
) -> dict[str, Any]:
    request_suffix = int(time.time())
    return {
        "client_request_id": f"e2e-{request_suffix}-{stage.name}",
        "job_type": stage.job_type,
        "model_id": model_id,
        "source": source_object_ref(config, stage, input_text, write_object=write_source),
        "callback": {
            "url": callback_url or "http://127.0.0.1:9/callback",
            "events": ["job.succeeded", "job.failed"],
        },
        "prompt": {"blocks": prompt_blocks},
    }


def trace_root(config: Config) -> Path:
    return config.storage_dir / config.output_bucket / config.output_prefix.rstrip("/") / "e2e_trace"


def stage_trace_dir(config: Config, stage: StageSpec) -> Path:
    path = trace_root(config) / stage.name
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_trace_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_meta_trace(
    config: Config,
    *,
    health_body: dict[str, Any],
    models_body: dict[str, Any],
    templates_body: dict[str, Any],
    model_id: str,
) -> Path:
    return write_trace_json(
        trace_root(config) / "meta.json",
        {
            "health": health_body,
            "models": models_body,
            "prompt_templates": templates_body,
            "selected_model_id": model_id,
        },
    )


def save_stage_request(config: Config, stage: StageSpec, payload: dict[str, Any]) -> Path:
    return write_trace_json(stage_trace_dir(config, stage) / "request.json", payload)


def save_stage_create_response(config: Config, stage: StageSpec, payload: dict[str, Any]) -> Path:
    return write_trace_json(stage_trace_dir(config, stage) / "create_response.json", payload)


def save_stage_final_response(config: Config, stage: StageSpec, payload: dict[str, Any]) -> Path:
    return write_trace_json(stage_trace_dir(config, stage) / "final_response.json", payload)


def save_stage_callback(config: Config, stage: StageSpec, record: CallbackRecord) -> Path:
    return write_trace_json(
        stage_trace_dir(config, stage) / "callback.json",
        {
            "path": record.path,
            "headers": record.headers,
            "body": record.body,
        },
    )


def stage_artifact_filename(stage: StageSpec, artifact: dict[str, Any]) -> str:
    key = str(artifact.get("key") or "artifact")
    if stage.name == "step2_review" and key == "work_note":
        key = "suggested_work_note"
    return f"{safe_filename(key)}.txt"


def verified_artifact_copy(config: Config, stage: StageSpec, artifact: dict[str, Any]) -> Path:
    content = object_storage.read_text(
        bucket=artifact["oss_bucket"],
        key=artifact["oss_key"],
        region=artifact["oss_region"],
    )
    if not content.strip():
        raise RuntimeError(f"artifact 文件为空: {artifact['key']}")
    path = stage_trace_dir(config, stage) / stage_artifact_filename(stage, artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def stored_artifact_paths(config: Config, stage: StageSpec, final_body: dict[str, Any]) -> list[Path]:
    result = final_body.get("result") or {}
    paths: list[Path] = []
    for artifact in result.get("artifacts") or []:
        if artifact.get("storage") != "oss_object":
            continue
        paths.append(verified_artifact_copy(config, stage, artifact))
    return paths


def artifact_path_by_key(config: Config, stage: StageSpec, final_body: dict[str, Any], artifact_key: str) -> Path:
    result = final_body.get("result") or {}
    for artifact in result.get("artifacts") or []:
        if artifact.get("key") == artifact_key and artifact.get("storage") == "oss_object":
            return verified_artifact_copy(config, stage, artifact)
    raise RuntimeError(f"未找到 OSS artifact: {artifact_key}")


def artifacts(final_body: dict[str, Any]) -> list[dict[str, Any]]:
    result = final_body.get("result") or {}
    return list(result.get("artifacts") or [])


def artifact_by_key(final_body: dict[str, Any], artifact_key: str) -> dict[str, Any] | None:
    for artifact in artifacts(final_body):
        if artifact.get("key") == artifact_key:
            return artifact
    return None


def inline_artifact_content(final_body: dict[str, Any], artifact_key: str) -> str | None:
    artifact = artifact_by_key(final_body, artifact_key)
    if artifact:
        content = artifact.get("content")
        return str(content) if content is not None else None
    return None


def require_inline_artifact(final_body: dict[str, Any], artifact_key: str) -> dict[str, Any]:
    artifact = artifact_by_key(final_body, artifact_key)
    if not artifact:
        raise RuntimeError(f"未找到 inline artifact: {artifact_key}")
    if artifact.get("storage") == "oss_object":
        raise RuntimeError(f"artifact 应该是 inline，但实际写入了 OSS: {artifact_key}")
    if artifact.get("content") is None:
        raise RuntimeError(f"inline artifact content 缺失: {artifact_key}")
    return artifact


def artifact_apply_mode(final_body: dict[str, Any], artifact_key: str) -> str | None:
    artifact = artifact_by_key(final_body, artifact_key)
    value = artifact.get("apply_mode") if artifact else None
    return str(value) if value is not None else None


def artifact_keys(final_body: dict[str, Any]) -> list[str]:
    return [str(artifact.get("key")) for artifact in artifacts(final_body)]


def safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value).strip("_")


def save_inline_artifacts(config: Config, result: StageResult) -> list[dict[str, str]]:
    saved: list[dict[str, str]] = []
    base_dir = stage_trace_dir(config, result.stage)
    for artifact in artifacts(result.final_body):
        if artifact.get("storage") == "oss_object":
            continue
        content = artifact.get("content")
        if content is None:
            continue
        key = str(artifact.get("key") or "artifact")
        path = base_dir / stage_artifact_filename(result.stage, artifact)
        path.write_text(str(content), encoding="utf-8")
        saved.append({"key": key, "path": str(path)})
    return saved


def callback_signature(timestamp: str, body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def validate_callback_record(
    *,
    config: Config,
    stage: StageSpec,
    final_body: dict[str, Any],
    record: CallbackRecord,
) -> None:
    expected_event = "job.succeeded" if final_body["status"] == "succeeded" else "job.failed"
    headers = {key.lower(): value for key, value in record.headers.items()}
    body = record.body

    if headers.get("x-ai-service-job-id") != final_body["job_id"]:
        raise RuntimeError(f"callback header 中的 job_id 不一致: {headers}")
    if headers.get("x-ai-service-event") != expected_event:
        raise RuntimeError(f"callback header 中的 event 不一致: {headers}")
    if body.get("event") != expected_event or body.get("status") != final_body["status"]:
        raise RuntimeError(f"callback body 中的 status 不一致: {body}")
    if body.get("job_id") != final_body["job_id"] or body.get("job_type") != stage.job_type:
        raise RuntimeError(f"callback body 中的 job 信息不一致: {body}")
    if body.get("result") != final_body.get("result"):
        raise RuntimeError(f"{stage_label(stage)} callback result 与轮询 result 不一致")
    if body.get("error") != final_body.get("error"):
        raise RuntimeError(f"{stage_label(stage)} callback error 与轮询 error 不一致")

    timestamp = headers.get("x-ai-service-timestamp")
    signature = headers.get("x-ai-service-signature")
    if not timestamp or not signature:
        raise RuntimeError(f"callback 签名 header 缺失: {headers}")
    expected_signature = callback_signature(timestamp, record.raw_body, config.callback_signing_secret)
    if not hmac.compare_digest(signature, expected_signature):
        raise RuntimeError(f"{stage_label(stage)} callback 签名无效")

    print(f"[{stage_label(stage)}] callback 已收到:", body.get("event"), body.get("job_id"))


def validate_stage_result(config: Config, stage: StageSpec, final_body: dict[str, Any]) -> None:
    if stage.name == "step1_localize":
        artifact_path_by_key(config, stage, final_body, "localized_text")
        work_note = require_inline_artifact(final_body, "work_note")
        if work_note.get("type") != "work_note" or work_note.get("apply_mode") != "replace":
            raise RuntimeError(f"本地化 work_note artifact 不符合预期: {work_note}")
        if artifact_by_key(final_body, "project_memory"):
            raise RuntimeError("project_memory 不应作为最终 artifact 对外返回")
        return

    if stage.name == "step2_review":
        require_inline_artifact(final_body, "review_summary")
        signals = (final_body.get("result") or {}).get("signals") or {}
        if not isinstance(signals.get("passed"), bool):
            raise RuntimeError(f"本地化校验 signals.passed 必须是 bool: {signals}")
        work_note = artifact_by_key(final_body, "work_note")
        if signals["passed"]:
            if work_note:
                raise RuntimeError(f"本地化校验 passed=true 时不应返回 work_note: {work_note}")
        else:
            if not work_note:
                raise RuntimeError("本地化校验 passed=false 时必须返回 work_note")
            if work_note.get("type") != "work_note" or work_note.get("apply_mode") != "append":
                raise RuntimeError(f"本地化校验 work_note artifact 不符合预期: {work_note}")
            if not str(work_note.get("content") or "").strip():
                raise RuntimeError("本地化校验 passed=false 时 work_note content 不能为空")
        return

    if stage.name == "step3_translate":
        artifact_path_by_key(config, stage, final_body, "translated_text")
        return


def review_passed(final_body: dict[str, Any]) -> bool | None:
    result = final_body.get("result") or {}
    value = (result.get("signals") or {}).get("passed")
    return value if isinstance(value, bool) else None


def submit_stage(
    *,
    config: Config,
    client: httpx.Client,
    headers: dict[str, str],
    stage: StageSpec,
    model_id: str,
    prompt_blocks: list[dict[str, str]],
    input_text: str,
    callback_url: str | None,
    callback_store: CallbackStore | None,
) -> StageResult:
    payload = create_payload(
        config=config,
        stage=stage,
        model_id=model_id,
        prompt_blocks=prompt_blocks,
        input_text=input_text,
        callback_url=callback_url,
    )
    save_stage_request(config, stage, payload)

    if config.dry_run:
        print(f"[{stage_label(stage)}] dry_run: 不提交 Job")
        print(f"[{stage_label(stage)}] client_request_id:", payload["client_request_id"])
        return StageResult(stage=stage, job_id="", status="dry_run", output_paths=[], final_body={})

    created = client.post("/api/v1/novel-localization-ai/jobs", headers=headers, json=payload)
    if created.status_code != 202:
        print(f"[{stage_label(stage)}] 错误 {created.status_code}: {created.text}")
        created.raise_for_status()
    created_body = created.json()
    save_stage_create_response(config, stage, created_body)
    print(f"[{stage_label(stage)}] Job 已创建:", created_body)

    status_url = created_body["status_url"]
    deadline = time.monotonic() + config.timeout_seconds
    while time.monotonic() < deadline:
        status_resp = client.get(status_url, headers=headers)
        status_resp.raise_for_status()
        status_body = status_resp.json()
        print(
            f"[{stage_label(stage)}] 状态:",
            status_body["status"],
            status_body.get("progress_percent"),
            status_body.get("progress_text"),
        )

        if status_body["status"] == "succeeded":
            save_stage_final_response(config, stage, status_body)
            paths = stored_artifact_paths(config, stage, status_body)
            if stage.expected_artifact_key:
                artifact_path_by_key(config, stage, status_body, stage.expected_artifact_key)
            validate_stage_result(config, stage, status_body)
            if callback_store:
                record = callback_store.wait_for_job(status_body["job_id"], config.callback_wait_seconds)
                validate_callback_record(config=config, stage=stage, final_body=status_body, record=record)
                save_stage_callback(config, stage, record)
            return StageResult(
                stage=stage,
                job_id=status_body["job_id"],
                status=status_body["status"],
                output_paths=paths,
                final_body=status_body,
            )
        if status_body["status"] in {"failed", "canceled"}:
            save_stage_final_response(config, stage, status_body)
            if callback_store and status_body["status"] == "failed":
                record = callback_store.wait_for_job(status_body["job_id"], config.callback_wait_seconds)
                validate_callback_record(config=config, stage=stage, final_body=status_body, record=record)
                save_stage_callback(config, stage, record)
            raise RuntimeError(
                f"{stage_label(stage)} 结束状态异常 status={status_body['status']}: {status_body.get('error')}"
            )

        time.sleep(config.poll_interval)

    raise TimeoutError(f"{stage_label(stage)} 在 {config.timeout_seconds}s 内未完成")


def write_report(
    config: Config,
    *,
    model_id: str,
    results: list[StageResult],
    localized_path: Path | None,
    translated_path: Path | None,
    callback_records: list[CallbackRecord],
) -> Path:
    report_path = trace_root(config) / "e2e_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "input_file": str(config.input_file),
        "model_id": model_id,
        "repeat_input": config.repeat_input,
        "output_bucket": config.output_bucket,
        "output_prefix": config.output_prefix.rstrip("/") + "/",
        "localized_path": str(localized_path) if localized_path else None,
        "translated_path": str(translated_path) if translated_path else None,
        "contract_check": config.contract_check,
        "callbacks": [
            {
                "path": record.path,
                "job_id": record.body.get("job_id"),
                "event": record.body.get("event"),
                "status": record.body.get("status"),
                "header_event": record.headers.get("X-AI-Service-Event"),
            }
            for record in callback_records
        ],
        "stages": [
            {
                "stage": result.stage.name,
                "job_type": result.stage.job_type,
                "job_id": result.job_id,
                "status": result.status,
                "artifact_keys": artifact_keys(result.final_body),
                "work_note_apply_mode": artifact_apply_mode(result.final_body, "work_note"),
                "review_passed": review_passed(result.final_body),
                "output_paths": [str(path) for path in result.output_paths],
                "inline_artifact_paths": save_inline_artifacts(config, result),
                "review_summary": inline_artifact_content(result.final_body, "review_summary"),
            }
            for result in results
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def call_flow(config: Config, callback_receiver: CallbackReceiver | None) -> tuple[list[StageResult], str]:
    headers = {"Authorization": f"Bearer {config.service_api_key}"}
    callback_url = callback_receiver.url if callback_receiver else None
    callback_store = callback_receiver.store if callback_receiver else None
    with httpx.Client(base_url=config.base_url, timeout=30) as client:
        health = client.get("/health")
        health.raise_for_status()
        health_body = health.json()
        print("健康检查 health:", health_body)

        models = client.get("/api/v1/novel-localization-ai/models", headers=headers)
        models.raise_for_status()
        models_body = models.json()
        model_id = select_model(models_body, config.model_id)
        print("输入文件 input_file:", config.input_file)
        print("模型 model_id:", model_id)

        templates = client.get("/api/v1/novel-localization-ai/prompt-templates", headers=headers)
        templates.raise_for_status()
        templates_body = templates.json()
        save_meta_trace(
            config,
            health_body=health_body,
            models_body=models_body,
            templates_body=templates_body,
            model_id=model_id,
        )

        if config.contract_check:
            validate_meta_contract(
                client=client,
                headers=headers,
                models_body=models_body,
                templates_body=templates_body,
                model_id=model_id,
            )

        source_text = config.input_file.read_text(encoding="utf-8")
        if config.repeat_input > 1:
            source_text = "\n\n".join([source_text] * config.repeat_input)
        if not source_text.strip():
            raise RuntimeError(f"输入文件为空: {config.input_file}")

        if config.contract_check and not config.dry_run:
            run_create_job_contract_checks(
                config=config,
                client=client,
                headers=headers,
                model_id=model_id,
                templates_body=templates_body,
                source_text=source_text,
                callback_url=callback_url or "http://127.0.0.1:9/callback",
            )
            if config.contract_only:
                return [], model_id

        results: list[StageResult] = []
        text_by_label = {"source": source_text}

        for stage in STAGES:
            input_text = text_by_label[stage.input_label]
            prompt_blocks = load_prompt_blocks(templates_body, stage.job_type)
            result = submit_stage(
                config=config,
                client=client,
                headers=headers,
                stage=stage,
                model_id=model_id,
                prompt_blocks=prompt_blocks,
                input_text=input_text,
                callback_url=callback_url,
                callback_store=callback_store,
            )
            results.append(result)

            if stage.name == "step1_localize" and not config.dry_run:
                localized_path = artifact_path_by_key(config, stage, result.final_body, "localized_text")
                text_by_label["localized"] = localized_path.read_text(encoding="utf-8")
            elif stage.name == "step1_localize" and config.dry_run:
                text_by_label["localized"] = source_text

        return results, model_id


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="模拟后端调用，验证小说本地化服务完整多阶段链路。")
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--service-api-key", default=default_service_api_key())
    parser.add_argument("--input-file", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--model-id", default=os.getenv("E2E_MODEL_ID"))
    parser.add_argument("--output-bucket", default=os.getenv("OSS_BUCKET") or load_dotenv_value("OSS_BUCKET") or "local-dev")
    parser.add_argument("--output-prefix", default=f"novel-localization/e2e/{int(time.time())}")
    parser.add_argument("--output-region", default=os.getenv("OSS_REGION") or load_dotenv_value("OSS_REGION") or "local")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--storage-dir", type=Path, default=DEFAULT_STORAGE_DIR)
    parser.add_argument("--repeat-input", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--contract-only",
        action="store_true",
        help="只验证 meta 和 POST /jobs 错误请求契约，不创建真实模型 Job。",
    )
    parser.add_argument(
        "--skip-contract-check",
        action="store_true",
        help="只跑三阶段主链路，不做 meta、错误请求和 callback 契约检查。",
    )
    parser.add_argument("--callback-port", type=int, default=0, help="本地 callback receiver 端口，0 表示随机端口。")
    parser.add_argument("--callback-wait-seconds", type=int, default=20)
    parser.add_argument("--callback-signing-secret", default=default_callback_signing_secret())
    args = parser.parse_args()
    if args.contract_only and args.skip_contract_check:
        parser.error("--contract-only cannot be used with --skip-contract-check")
    if args.contract_only and args.dry_run:
        parser.error("--contract-only cannot be used with --dry-run")

    input_file = args.input_file or first_txt_file(args.data_dir)
    if not input_file.is_absolute():
        input_file = ROOT_DIR / input_file
    if not input_file.exists():
        raise FileNotFoundError(f"找不到输入文件: {input_file}")

    return Config(
        base_url=args.base_url,
        service_api_key=args.service_api_key,
        input_file=input_file,
        model_id=args.model_id,
        output_bucket=args.output_bucket,
        output_prefix=args.output_prefix,
        output_region=args.output_region,
        poll_interval=args.poll_interval,
        timeout_seconds=args.timeout_seconds,
        storage_dir=args.storage_dir if args.storage_dir.is_absolute() else ROOT_DIR / args.storage_dir,
        repeat_input=max(1, args.repeat_input),
        dry_run=args.dry_run,
        contract_only=args.contract_only,
        contract_check=not args.skip_contract_check,
        callback_port=args.callback_port,
        callback_wait_seconds=args.callback_wait_seconds,
        callback_signing_secret=args.callback_signing_secret,
    )


def main() -> int:
    try:
        config = parse_args()
        callback_receiver = None
        if config.contract_check and not config.dry_run:
            callback_receiver = CallbackReceiver(config.callback_port)
            print("回调地址 callback_url:", callback_receiver.url)
        context = callback_receiver if callback_receiver else nullcontext()
        with context:
            results, model_id = call_flow(config, callback_receiver)
            callback_records = callback_receiver.store.snapshot() if callback_receiver else []
        localized_path = None
        translated_path = None
        if not config.dry_run and results:
            step1 = next(result for result in results if result.stage.name == "step1_localize")
            step3 = next(result for result in results if result.stage.name == "step3_translate")
            localized_path = artifact_path_by_key(config, step1.stage, step1.final_body, "localized_text")
            translated_path = artifact_path_by_key(config, step3.stage, step3.final_body, "translated_text")
        report_path = write_report(
            config,
            model_id=model_id,
            results=results,
            localized_path=localized_path,
            translated_path=translated_path,
            callback_records=callback_records,
        )
    except Exception as exc:
        print(f"e2e 失败: {exc}", file=sys.stderr)
        return 1

    if config.contract_only:
        final_status = "contract_only"
    elif config.dry_run:
        final_status = "dry_run"
    else:
        final_status = "succeeded"
    print("最终状态 final_status:", final_status)
    if localized_path:
        print("本地化正文 localized_text:", localized_path)
    if translated_path:
        print("英文终稿 translated_text:", translated_path)
    print("验证报告 report:", report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
