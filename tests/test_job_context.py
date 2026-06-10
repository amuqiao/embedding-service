from app.services.job_context import append_context_to_prompt, extract_tagged_json


class DummyJob:
    prompt_payload = {
        "blocks": [
            {"key": "user", "role": "user", "content": "用户提示"},
            {"key": "work_note", "role": "user", "content": ""},
        ]
    }


def test_extract_tagged_json_from_work_note():
    text = '<project_memory>\n{"characters": ["李明"], "style_guide": "美式"}\n</project_memory>'

    assert extract_tagged_json(text, "project_memory") == {"characters": ["李明"], "style_guide": "美式"}


def test_append_context_to_system_prompt():
    payload = append_context_to_prompt(DummyJob(), "冻结映射表")

    # no system block in payload → creates system_context block at position 0
    system = next(block for block in payload["blocks"] if block["key"] == "system_context")
    assert system["role"] == "system"
    assert system["content"] == "冻结映射表"
