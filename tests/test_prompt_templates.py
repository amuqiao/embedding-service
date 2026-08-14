from pathlib import Path

from app.core import prompt_templates
from app.core.exceptions import ValidationAppError


def test_public_prompt_template_blocks_are_declared_by_config(monkeypatch):
    monkeypatch.setattr(
        prompt_templates,
        "_load_prompt_config",
        lambda **_kwargs: {
            "version": "test",
            "job_types": {
                "job.generic": {
                    "name": "Generic job",
                    "description": "Generic prompt template",
                    "prompt_blocks": {
                        "user": {
                            "role": "user",
                            "label": "User",
                            "content": "Default user prompt",
                        }
                    },
                }
            },
        },
    )

    prompt_templates.validate_prompt_config_shape(known_output_schemas=set())
    template = prompt_templates.list_prompt_templates(job_type="job.generic")

    assert template.job_type == "job.generic"
    assert template.version == "test"
    assert template.prompt_blocks[0].key == "user"
    assert len(template.prompt_blocks) == 1


def test_prompt_templates_default_to_poster_title_image():
    template = prompt_templates.list_prompt_templates()

    assert template.version == "poster_title_image.v1"
    assert template.job_type == "poster_title_image"
    assert prompt_templates.get_prompt_block_default("poster_title_image", "layout_rules")


def test_job_local_prompt_template_uses_job_local_version(monkeypatch):
    base = {"version": "base", "job_types": {}, "prompts": {}}
    overlay = {
        "version": "job.local.v2",
        "job_types": {
            "job.local": {
                "name": "Local job",
                "description": "Local job prompt template",
                "prompt_blocks": {
                    "user": {
                        "role": "user",
                        "label": "User",
                        "content": "Local prompt",
                    }
                },
            }
        },
    }
    prompt_templates._merge_prompt_config(base, overlay, source=Path("job.yaml"))
    monkeypatch.setattr(prompt_templates, "_load_prompt_config", lambda **_kwargs: base)

    template = prompt_templates.list_prompt_templates(job_type="job.local")

    assert template.version == "job.local.v2"


def test_prompt_templates_reject_unknown_job_type(monkeypatch):
    monkeypatch.setattr(
        prompt_templates,
        "_load_prompt_config",
        lambda **_kwargs: {
            "version": "test",
            "job_types": {},
        },
    )

    try:
        prompt_templates.list_prompt_templates(job_type="missing.job")
    except ValidationAppError as exc:
        assert exc.code == "INVALID_JOB_TYPE"
    else:
        raise AssertionError("missing job_type should be rejected")


def test_prompt_config_rejects_unknown_top_level_keys(tmp_path):
    path = tmp_path / "prompts.yaml"
    path.write_text(
        """
version: test
job_typez: {}
""".strip(),
        encoding="utf-8",
    )

    try:
        prompt_templates._read_prompt_config_file(path)
    except RuntimeError as exc:
        assert "unknown top-level keys" in str(exc)
    else:
        raise AssertionError("unknown prompt config top-level key should be rejected")
