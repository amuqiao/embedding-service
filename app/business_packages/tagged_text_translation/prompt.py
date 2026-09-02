from __future__ import annotations

import json

from app.business_packages.tagged_text_translation.schemas import TaggedTextTranslationParams


SYSTEM_PROMPT = """\
You are a production translation engine for CMS text.
Translate only natural-language content. Preserve HTML tags, attributes,
template placeholders, variables, and item identity exactly.
Return only one JSON object. Do not return Markdown.
"""


def build_translation_messages(params: TaggedTextTranslationParams) -> list[dict[str, str]]:
    payload = {
        "source_language": params.source_language,
        "target_language": params.target_language,
        "items": [
            {
                "id": item.id,
                "text": item.text,
                "max_target_chars_hint": item.max_target_chars_hint,
            }
            for item in params.items
        ],
    }
    contract = {
        "source_language": "<actual source language or null>",
        "target_language": params.target_language,
        "items": [
            {
                "id": "<same id as input item>",
                "translated_text": "<translated text with original tags and placeholders preserved>",
            }
        ],
    }
    content = (
        "Translate the following CMS text items.\n"
        "Rules:\n"
        "- Keep every item id unchanged.\n"
        "- Keep item count and item order unchanged.\n"
        "- Preserve every HTML tag, attribute name, placeholder, variable, and template marker exactly.\n"
        "- Do not translate text inside placeholders such as {name} or {{order_id}}.\n"
        "- Try to respect max_target_chars_hint for visible translated text when present, but do not break tags or placeholders.\n"
        "- Return only a JSON object matching the output contract.\n\n"
        f"Output contract:\n{json.dumps(contract, ensure_ascii=False, indent=2)}\n\n"
        f"Input:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
