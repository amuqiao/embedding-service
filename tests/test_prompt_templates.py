from app.core import prompt_templates


def test_public_prompt_template_blocks_are_declared_by_config(monkeypatch):
    monkeypatch.setattr(
        prompt_templates,
        "_load_prompt_config",
        lambda: {
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
    templates = prompt_templates.list_prompt_templates()

    assert templates.job_types[0].prompt_blocks[0].key == "user"
    assert len(templates.job_types[0].prompt_blocks) == 1
