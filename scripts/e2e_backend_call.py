"""模拟后端调用小说本地化服务，验证真实多阶段 Job 链路。

默认流程：
  1. 从 .data 选取第一个 .txt 原文。
  2. 提交 step1_localize，产出 localized.txt。
  3. 将 localized.txt 作为输入提交 step2_review，记录校验结果。
  4. 将 localized.txt 作为输入提交 step3_translate，产出 translated.txt。

脚本只通过 HTTP API 调用本服务，用于验证“后端调用方 -> 服务 API -> Celery -> OpenAI -> 本地对象存储”的完整链路。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
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


def first_txt_file(data_dir: Path) -> Path:
    if not data_dir.exists():
        raise FileNotFoundError(f"data dir not found: {data_dir}")
    files = sorted(path for path in data_dir.rglob("*.txt") if path.is_file())
    if not files:
        raise FileNotFoundError(f"no .txt file found under: {data_dir}")
    return files[0]


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def select_model(models_body: dict[str, Any], requested_model_id: str | None) -> str:
    available = {item["id"] for item in models_body["models"]}
    if requested_model_id:
        if requested_model_id not in available:
            raise RuntimeError(f"model not available: {requested_model_id}; available={sorted(available)}")
        return requested_model_id

    for model_id in PREFERRED_OPENAI_MODELS:
        if model_id in available:
            return model_id

    raise RuntimeError(
        "no OpenAI model is available from service; check OPENAI_API_KEY in .env "
        "or pass --model-id explicitly"
    )


def load_prompt_blocks(templates_body: dict[str, Any], job_type: str) -> list[dict[str, str]]:
    template = next((item for item in templates_body["job_types"] if item["job_type"] == job_type), None)
    if not template:
        raise RuntimeError(f"job_type not available: {job_type}")
    return [
        {"key": block["key"], "role": block["role"], "content": block["default_content"]}
        for block in template["prompt_blocks"]
    ]


def create_payload(
    *,
    config: Config,
    stage: StageSpec,
    model_id: str,
    prompt_blocks: list[dict[str, str]],
    input_text: str,
) -> dict[str, Any]:
    request_suffix = int(time.time())
    return {
        "client_request_id": f"e2e-{request_suffix}-{stage.name}",
        "job_type": stage.job_type,
        "model_id": model_id,
        "input": {
            "type": "text",
            "content": input_text,
            "content_hash": sha256_text(input_text),
        },
        "output": {
            "type": "oss_prefix",
            "oss_bucket": config.output_bucket,
            "oss_prefix": config.output_prefix.rstrip("/") + "/",
            "oss_region": config.output_region,
        },
        "callback": {"url": "http://127.0.0.1:9/callback", "events": ["job.failed"]},
        "prompt": {"blocks": prompt_blocks},
    }


def path_for_artifact(config: Config, artifact: dict[str, Any]) -> Path:
    return config.storage_dir / artifact["oss_bucket"] / artifact["oss_key"]


def stored_artifact_paths(config: Config, final_body: dict[str, Any]) -> list[Path]:
    result = final_body.get("result") or {}
    paths: list[Path] = []
    for artifact in result.get("artifacts") or []:
        if artifact.get("storage") != "oss_object":
            continue
        path = path_for_artifact(config, artifact)
        if not path.exists():
            raise RuntimeError(f"artifact file not found: {path}")
        if path.stat().st_size <= 0:
            raise RuntimeError(f"artifact file is empty: {path}")
        paths.append(path)
    return paths


def artifact_path_by_key(config: Config, final_body: dict[str, Any], artifact_key: str) -> Path:
    result = final_body.get("result") or {}
    for artifact in result.get("artifacts") or []:
        if artifact.get("key") == artifact_key and artifact.get("storage") == "oss_object":
            path = path_for_artifact(config, artifact)
            if not path.exists() or path.stat().st_size <= 0:
                raise RuntimeError(f"artifact {artifact_key} is missing or empty: {path}")
            return path
    raise RuntimeError(f"stored artifact not found: {artifact_key}")


def inline_artifact_content(final_body: dict[str, Any], artifact_key: str) -> str | None:
    result = final_body.get("result") or {}
    for artifact in result.get("artifacts") or []:
        if artifact.get("key") == artifact_key:
            content = artifact.get("content")
            return str(content) if content is not None else None
    return None


def artifact_content(final_body: dict[str, Any], artifact_key: str) -> Any | None:
    result = final_body.get("result") or {}
    for artifact in result.get("artifacts") or []:
        if artifact.get("key") == artifact_key:
            return artifact.get("content")
    return None


def inject_project_memory(prompt_blocks: list[dict[str, str]], project_memory: dict[str, Any] | None) -> list[dict[str, str]]:
    if not project_memory:
        return prompt_blocks
    tagged = "<project_memory>\n" + json.dumps(project_memory, ensure_ascii=False, indent=2) + "\n</project_memory>"
    injected: list[dict[str, str]] = []
    for block in prompt_blocks:
        copied = dict(block)
        if copied["key"] == "work_note":
            content = copied.get("content") or ""
            copied["content"] = f"{content}\n\n{tagged}".strip()
        injected.append(copied)
    return injected


def submit_stage(
    *,
    config: Config,
    client: httpx.Client,
    headers: dict[str, str],
    stage: StageSpec,
    model_id: str,
    prompt_blocks: list[dict[str, str]],
    input_text: str,
) -> StageResult:
    payload = create_payload(
        config=config,
        stage=stage,
        model_id=model_id,
        prompt_blocks=prompt_blocks,
        input_text=input_text,
    )

    if config.dry_run:
        print(f"[{stage.name}] dry_run: no job submitted")
        print(f"[{stage.name}] client_request_id:", payload["client_request_id"])
        return StageResult(stage=stage, job_id="", status="dry_run", output_paths=[], final_body={})

    created = client.post("/api/v1/novel-localization-ai/jobs", headers=headers, json=payload)
    if created.status_code != 202:
        print(f"[{stage.name}] ERROR {created.status_code}: {created.text}")
        created.raise_for_status()
    created_body = created.json()
    print(f"[{stage.name}] created:", created_body)

    status_url = created_body["status_url"]
    deadline = time.monotonic() + config.timeout_seconds
    while time.monotonic() < deadline:
        status_resp = client.get(status_url, headers=headers)
        status_resp.raise_for_status()
        status_body = status_resp.json()
        print(
            f"[{stage.name}] status:",
            status_body["status"],
            status_body.get("progress_percent"),
            status_body.get("progress_text"),
        )

        if status_body["status"] == "succeeded":
            paths = stored_artifact_paths(config, status_body)
            if stage.expected_artifact_key:
                artifact_path_by_key(config, status_body, stage.expected_artifact_key)
            return StageResult(
                stage=stage,
                job_id=status_body["job_id"],
                status=status_body["status"],
                output_paths=paths,
                final_body=status_body,
            )
        if status_body["status"] in {"failed", "canceled"}:
            raise RuntimeError(f"{stage.name} ended with status={status_body['status']}: {status_body.get('error')}")

        time.sleep(config.poll_interval)

    raise TimeoutError(f"{stage.name} did not finish within {config.timeout_seconds}s")


def write_report(config: Config, results: list[StageResult], localized_path: Path | None, translated_path: Path | None) -> Path:
    report_path = config.storage_dir / config.output_bucket / config.output_prefix.rstrip("/") / "e2e_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "input_file": str(config.input_file),
        "repeat_input": config.repeat_input,
        "output_bucket": config.output_bucket,
        "output_prefix": config.output_prefix.rstrip("/") + "/",
        "localized_path": str(localized_path) if localized_path else None,
        "translated_path": str(translated_path) if translated_path else None,
        "stages": [
            {
                "stage": result.stage.name,
                "job_type": result.stage.job_type,
                "job_id": result.job_id,
                "status": result.status,
                "output_paths": [str(path) for path in result.output_paths],
                "review_summary": inline_artifact_content(result.final_body, "review_summary"),
            }
            for result in results
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def call_flow(config: Config) -> list[StageResult]:
    headers = {"Authorization": f"Bearer {config.service_api_key}"}
    with httpx.Client(base_url=config.base_url, timeout=30) as client:
        health = client.get("/health")
        health.raise_for_status()
        print("health:", health.json())

        models = client.get("/api/v1/novel-localization-ai/models", headers=headers)
        models.raise_for_status()
        model_id = select_model(models.json(), config.model_id)
        print("input_file:", config.input_file)
        print("model_id:", model_id)

        templates = client.get("/api/v1/novel-localization-ai/prompt-templates", headers=headers)
        templates.raise_for_status()
        templates_body = templates.json()

        source_text = config.input_file.read_text(encoding="utf-8")
        if config.repeat_input > 1:
            source_text = "\n\n".join([source_text] * config.repeat_input)
        if not source_text.strip():
            raise RuntimeError(f"input file is empty: {config.input_file}")

        results: list[StageResult] = []
        text_by_label = {"source": source_text}
        project_memory: dict[str, Any] | None = None

        for stage in STAGES:
            input_text = text_by_label[stage.input_label]
            prompt_blocks = load_prompt_blocks(templates_body, stage.job_type)
            if stage.name in {"step2_review", "step3_translate"}:
                prompt_blocks = inject_project_memory(prompt_blocks, project_memory)
            result = submit_stage(
                config=config,
                client=client,
                headers=headers,
                stage=stage,
                model_id=model_id,
                prompt_blocks=prompt_blocks,
                input_text=input_text,
            )
            results.append(result)

            if stage.name == "step1_localize" and not config.dry_run:
                localized_path = artifact_path_by_key(config, result.final_body, "localized_text")
                text_by_label["localized"] = localized_path.read_text(encoding="utf-8")
                maybe_memory = artifact_content(result.final_body, "project_memory")
                if isinstance(maybe_memory, dict):
                    project_memory = maybe_memory
            elif stage.name == "step1_localize" and config.dry_run:
                text_by_label["localized"] = source_text

        return results


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="模拟后端调用，验证小说本地化服务完整多阶段链路。")
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--service-api-key", default=default_service_api_key())
    parser.add_argument("--input-file", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--model-id", default=os.getenv("E2E_MODEL_ID"))
    parser.add_argument("--output-bucket", default="local-dev")
    parser.add_argument("--output-prefix", default=f"novel-localization/e2e/{int(time.time())}")
    parser.add_argument("--output-region", default="local")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--storage-dir", type=Path, default=DEFAULT_STORAGE_DIR)
    parser.add_argument("--repeat-input", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    input_file = args.input_file or first_txt_file(args.data_dir)
    if not input_file.is_absolute():
        input_file = ROOT_DIR / input_file
    if not input_file.exists():
        raise FileNotFoundError(f"input file not found: {input_file}")

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
    )


def main() -> int:
    try:
        config = parse_args()
        results = call_flow(config)
        localized_path = None
        translated_path = None
        if not config.dry_run:
            step1 = next(result for result in results if result.stage.name == "step1_localize")
            step3 = next(result for result in results if result.stage.name == "step3_translate")
            localized_path = artifact_path_by_key(config, step1.final_body, "localized_text")
            translated_path = artifact_path_by_key(config, step3.final_body, "translated_text")
        report_path = write_report(config, results, localized_path, translated_path)
    except Exception as exc:
        print(f"e2e failed: {exc}", file=sys.stderr)
        return 1

    print("final_status:", "dry_run" if config.dry_run else "succeeded")
    if localized_path:
        print("localized_text:", localized_path)
    if translated_path:
        print("translated_text:", translated_path)
    print("report:", report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
