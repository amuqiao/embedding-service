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


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    headers = {"Authorization": f"Bearer {SERVICE_API_KEY}"}
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        health = client.get("/health")
        health.raise_for_status()
        print("health:", health.json())

        models = client.get("/api/v1/novel-localization-ai/models", headers=headers)
        models.raise_for_status()
        default_model_id = models.json()["default_model_id"]
        print("default_model_id:", default_model_id)

        templates = client.get("/api/v1/novel-localization-ai/prompt-templates", headers=headers)
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
            "model_id": default_model_id,
            "input": {"type": "text", "content": text, "content_hash": sha256_text(text)},
            "output": {
                "type": "oss_prefix",
                "oss_bucket": "local-dev",
                "oss_prefix": "novel-localization/smoke/",
                "oss_region": "local",
            },
            "callback": {"url": "http://127.0.0.1:9/callback", "events": ["job.succeeded", "job.failed"]},
            "prompt": {"blocks": blocks},
        }
        created = client.post("/api/v1/novel-localization-ai/jobs", headers=headers, json=payload)
        created.raise_for_status()
        job = created.json()
        print("created:", job)

        status_url = job["status_url"]
        for _ in range(60):
            status_resp = client.get(status_url, headers=headers)
            status_resp.raise_for_status()
            status_body = status_resp.json()
            print("status:", status_body["status"], status_body.get("progress_percent"))
            if status_body["status"] in {"succeeded", "failed", "canceled"}:
                print("final:", status_body)
                return 0 if status_body["status"] == "succeeded" else 1
            time.sleep(2)

        print("job did not finish within timeout", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
