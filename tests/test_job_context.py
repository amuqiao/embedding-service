from app.services.job_context import append_context_to_prompt, extract_tagged_json


class DummyJob:
    prompt_payload = {
        "blocks": [
            {"key": "system", "role": "system", "content": "原系统提示"},
            {"key": "user", "role": "user", "content": "用户提示"},
            {"key": "work_note", "role": "user", "content": ""},
        ]
    }


def test_extract_tagged_json_from_work_note():
    text = '<project_memory>\n{"characters": ["李明"], "style_guide": "美式"}\n</project_memory>'

    assert extract_tagged_json(text, "project_memory") == {"characters": ["李明"], "style_guide": "美式"}


def test_append_context_to_system_prompt():
    payload = append_context_to_prompt(DummyJob(), "冻结映射表")

    system = next(block for block in payload["blocks"] if block["key"] == "system")
    assert system["content"].startswith("冻结映射表")
    assert "原系统提示" in system["content"]
