from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8100")


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


SERVICE_API_KEY = os.getenv("SERVICE_API_KEY") or load_dotenv_value("SERVICE_API_KEY") or "dev-service-key"
SERVICE_API_PREFIX = (
    os.getenv("SERVICE_API_PREFIX")
    or load_dotenv_value("SERVICE_API_PREFIX")
    or "/api/v1/ai-jobs"
).rstrip("/")
OUTPUT_BUCKET = os.getenv("OSS_BUCKET") or load_dotenv_value("OSS_BUCKET") or "local-dev"
OUTPUT_REGION = os.getenv("OSS_REGION") or load_dotenv_value("OSS_REGION") or "local"


def local_storage_dir() -> Path:
    configured = os.getenv("LOCAL_OBJECT_STORAGE_PATH") or load_dotenv_value("LOCAL_OBJECT_STORAGE_PATH")
    path = Path(configured or "storage/objects")
    return path if path.is_absolute() else ROOT_DIR / path


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_source_object(text: str) -> dict[str, dict[str, str]]:
    content_hash = sha256_text(text)
    object_key = f"novel-localization/smoke/input-{int(time.time())}.txt"
    path = local_storage_dir() / OUTPUT_BUCKET / object_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {
        "oss": {
            "oss_key": object_key,
            "oss_url": f"local://{object_key}",
            "content_hash": content_hash,
            "content_type": "text/plain; charset=utf-8",
        },
    }


def main() -> int:
    headers = {"Authorization": f"Bearer {SERVICE_API_KEY}"}
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        health = client.get("/health")
        health.raise_for_status()
        print("health:", health.json())

        models = client.get(f"{SERVICE_API_PREFIX}/models", headers=headers)
        models.raise_for_status()
        default_model_id = models.json()["default_model_id"]
        print("default_model_id:", default_model_id)

        templates = client.get(f"{SERVICE_API_PREFIX}/prompt-templates", headers=headers)
        templates.raise_for_status()
        step1 = next(
            item for item in templates.json()["job_types"]
            if item["job_type"] == "novel_localization.step1_localize"
        )
        blocks = [
            {"key": block["key"], "role": block["role"], "content": block["default_content"]}
            for block in step1["prompt_blocks"]
        ]

        text = "这是一个关于家庭、身份和选择的短篇小说。主角在一次聚会上重新理解了自己的生活。"
        payload = {
            "client_request_id": f"smoke-{int(time.time())}",
            "job_type": "novel_localization.step1_localize",
            "job_params": {
                "model_id": default_model_id,
                "source": write_source_object(text),
                "prompt": {"blocks": blocks},
            },
            "callback": {"url": "http://127.0.0.1:9/callback", "events": ["job.succeeded", "job.failed"]},
        }
        created = client.post(f"{SERVICE_API_PREFIX}/jobs", headers=headers, json=payload)
        created.raise_for_status()
        job = created.json()
        print("created:", job)

        status_url = job["status_url"]
        for _ in range(60):
            status_resp = client.get(status_url, headers=headers)
            status_resp.raise_for_status()
            status_body = status_resp.json()
            print("status:", status_body["status"], status_body.get("progress", {}).get("percent"))
            if status_body["status"] in {"succeeded", "failed", "canceled"}:
                print("final:", status_body)
                return 0 if status_body["status"] == "succeeded" else 1
            time.sleep(2)

        print("job did not finish within timeout", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
