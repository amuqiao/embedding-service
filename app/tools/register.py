from __future__ import annotations


def register_all_tools() -> None:
    """Register all tool definitions."""
    from app.tools.definitions import ToolDefinition
    from app.tools.registry import register

    register(
        ToolDefinition(
            tool_ref="audio_decode_normalize:1",
            kind="media_transform",
            entrypoint_path="app.tools.private.media_audio:decode_normalize_audio",
            request_schema="AudioDecodeNormalizeRequest",
            error_codes=frozenset(
                {
                    "AUDIO_STEM_DURATION_EXCEEDS_LIMIT",
                    "AUDIO_STEM_INPUT_INVALID",
                    "AUDIO_STEM_RUNTIME_UNAVAILABLE",
                }
            ),
        )
    )
