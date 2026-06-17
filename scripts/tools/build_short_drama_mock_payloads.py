from __future__ import annotations

import json
import uuid
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import ROOT_DIR, settings
from app.workflows.short_drama_tagging.adapter import build_rs_tagging_payload
from app.workflows.short_drama_tagging.rs_client import normalize_tag_schema_response, schema_fixture_path_for_language

REQUEST_PATH = ROOT_DIR / "docs/接口层/mock-data/short_drama_tagging/cpp_create_tagging_job_request.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_mock_model_final_result(tag_schema: dict[str, Any]) -> dict[str, Any]:
    selected_tags: dict[str, list[dict[str, Any]]] = {}
    for category in tag_schema["categories"]:
        first_label = category["labels"][0]
        selected_tags[category["category_id"]] = [
            {
                "标签名": first_label["name"],
                "权重": 0.9,
                "打标原因": "本地 mock：根据示例剧情和字幕素材选择该标签。",
            }
        ]
    return {
        "selected_tags": selected_tags,
        "tagging_detail": {
            "rule_applications": [],
            "removed_tags": [],
            "notes": ["local mock payload"],
        },
    }


def callback_envelope(job_view: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": "job.succeeded",
        "event_id": str(uuid.uuid4()),
        "attempt": 1,
        "sent_at": datetime.now(UTC).isoformat(),
        "job": job_view,
    }


def main() -> int:
    create_request = load_json(REQUEST_PATH)
    language = create_request["job_params"]["work_context"]["subtitle_language"]
    schema_path = schema_fixture_path_for_language(settings.SHORT_DRAMA_RS_SCHEMA_FIXTURE_PATH, language)
    bundle = normalize_tag_schema_response(load_json(schema_path))
    job_id = str(uuid.uuid4())
    final_result = build_mock_model_final_result(bundle["tag_schema_snapshot"])
    rs_payload, tagging_detail = build_rs_tagging_payload(
        t_book_id=create_request["job_params"]["t_book_id"],
        job_id=job_id,
        tag_schema=bundle["tag_schema_snapshot"],
        mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
        final_result=final_result,
    )
    now = datetime.now(UTC).isoformat()
    job_view = {
        "job_id": job_id,
        "client_request_id": create_request.get("client_request_id"),
        "job_type": create_request["job_type"],
        "status": "succeeded",
        "progress": {"percent": 100, "message": "finished", "stage": "finished"},
        "result": None,
        "error": None,
        "callback": {"status": "pending", "attempts": 0, "next_retry_at": None, "last_error": None},
        "metadata": create_request.get("metadata", {}),
        "created_at": now,
        "started_at": now,
        "finished_at": now,
    }
    output = {
        "cpp_create_request": create_request,
        "rs_schema_request": {
            "method": "GET",
            "path": "/api/v1/tag-schemas/default",
            "query": {"lang": create_request["job_params"]["work_context"]["subtitle_language"]},
        },
        "rs_schema_response": load_json(schema_path),
        "rs_write_request": rs_payload,
        "tagging_detail": tagging_detail,
        "job_status_response": job_view,
        "cpp_callback_request": callback_envelope(job_view),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
