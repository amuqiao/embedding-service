from pathlib import Path

from app.core import config as config_module
from app.core.config import Settings
from app.ai import resolver
from app.ai.catalog import registry as model_registry


def _settings_kwargs(**overrides):
    values = {
        "APP_ENV": "local",
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/fastapi_best_ai_architecture",
        "SERVICE_API_KEY": "test-token",
        "CALLBACK_SIGNING_SECRET": "test-callback-secret",
        "DISABLE_HTTP_AUTH_HEADER": False,
        "DISABLE_CALLER_ID_HEADER": False,
    }
    values.update(overrides)
    return values


def _build_settings(**overrides) -> Settings:
    values = _settings_kwargs(**overrides)
    nested: dict[str, dict[str, object]] = {}
    for env_key, value in values.items():
        config_module._assign_nested(nested, config_module.APPLICATION_ENV_FIELD_MAP[env_key], value)
    return Settings(**nested)


def _write_catalog(path: Path) -> None:
    path.write_text(
        """\
version: "2"
default_model_ids:
  text_generation: qwen-plus
models:
  - id: qwen-plus
    enabled: true
    public:
      name: Qwen Plus
      provider: dashscope
      model_type: text
      capabilities:
        - text_generation
      input_media_types:
        - text/plain
      output_media_types:
        - text/plain
      limits:
        context_window: 131072
      features:
        supports_json_output: true
      parameters: []
      notes: ""
    execution:
      routes:
        text_generation:
          provider: dashscope
          provider_model: qwen-plus
          adapter: litellm
          adapter_model: openai/qwen-plus
          pricing_ref: dashscope:qwen-plus@2026-08-29
          requires_env:
            - DASHSCOPE_API_KEY
          generation:
            temperature: 0.7
            num_retries: 0
            drop_params: true
""",
        encoding="utf-8",
    )


def test_resolver_freezes_provider_adapter_and_route_hash(tmp_path, monkeypatch):
    catalog_path = tmp_path / "models.yaml"
    _write_catalog(catalog_path)
    test_settings = _build_settings(
        MODEL_CONFIG_PATH=str(catalog_path),
        DASHSCOPE_API_KEY="test-key",
    )
    monkeypatch.setattr(model_registry, "settings", test_settings)

    selection = resolver.resolve_model(capability="text_generation")

    assert selection.resolved_model.model_id == "qwen-plus"
    assert selection.resolved_model.provider == "dashscope"
    assert selection.resolved_model.adapter == "litellm"
    assert selection.resolved_model.provider_model == "qwen-plus"
    assert selection.resolved_model.adapter_model == "openai/qwen-plus"
    assert selection.resolved_model.pricing_ref == "dashscope:qwen-plus@2026-08-29"
    assert selection.resolved_model.route_config_hash.startswith("sha256:")
    assert selection.source_policy == "global:text_generation"
