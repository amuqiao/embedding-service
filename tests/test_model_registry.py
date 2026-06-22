from types import SimpleNamespace

import pytest

from app.core import config as config_module
from app.core.config import Settings
from app.core import model_registry
from app.integrations import ai_gateway


def _settings_kwargs(**overrides):
    values = {
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/fastapi_best_ai_architecture",
        "SERVICE_API_KEY": "test-token",
        "CALLBACK_SIGNING_SECRET": "test-callback-secret",
        "CALLBACK_RETRY_DELAY_SECONDS": 300,
    }
    values.update(overrides)
    return values


def _build_settings(**overrides) -> Settings:
    values = _settings_kwargs(**overrides)
    nested: dict[str, dict[str, object]] = {}
    for env_key, value in values.items():
        section_name, field_name = config_module.APPLICATION_ENV_FIELD_MAP[env_key]
        nested.setdefault(section_name, {})[field_name] = value
    return Settings(**nested)


class _SettingsProxy:
    def __init__(self, settings_obj: Settings):
        self._settings = settings_obj
        self.service = settings_obj.service
        self.database = settings_obj.database
        self.broker = settings_obj.broker
        self.security = settings_obj.security
        self.storage = settings_obj.storage
        self.callback = settings_obj.callback
        self.ai_provider = settings_obj.ai_provider
        self.registry = settings_obj.registry
        self.job = settings_obj.job
        self.observability = settings_obj.observability

    def application_env_value(self, name: str) -> str:
        return self._settings.application_env_value(name)

    def __getattr__(self, name: str):
        legacy = {
            "DEFAULT_MODEL_ID": self.registry.default_model_id,
            "OPENAI_API_KEY": self.ai_provider.openai_api_key_value,
            "model_config_path": self.registry.model_config_path,
        }
        if name in legacy:
            return legacy[name]
        return getattr(self._settings, name)


def _write_model_config(path, requires_env: str = "OPENAI_API_KEY") -> None:
    path.write_text(
        f"""\
version: "1"
models:
  - id: custom-model
    name: Custom Model
    provider: openai
    litellm_model: openai/custom-model
    enabled: true
    context_window: 12345
    supports_json_output: true
    notes: ""
    requires_env:
      - {requires_env}
    generation:
      temperature: 0.2
      num_retries: 1
      drop_params: false
""",
        encoding="utf-8",
    )


def test_model_registry_loads_available_models_from_yaml(tmp_path, monkeypatch):
    config_path = tmp_path / "models.yaml"
    _write_model_config(config_path)
    test_settings = _build_settings(
        OPENAI_API_KEY="test-key",
        DEFAULT_MODEL_ID="custom-model",
        MODEL_CONFIG_PATH=str(config_path),
    )
    monkeypatch.setattr(model_registry, "settings", _SettingsProxy(test_settings))

    response = model_registry.list_models_response()

    assert response.default_model_id == "custom-model"
    assert [model.id for model in response.models] == ["custom-model"]
    assert response.models[0].context_window == 12345


def test_model_registry_hides_models_when_required_env_is_missing(tmp_path, monkeypatch):
    config_path = tmp_path / "models.yaml"
    _write_model_config(config_path, requires_env="OPENAI_API_KEY")
    test_settings = _build_settings(
        DEFAULT_MODEL_ID="custom-model",
        MODEL_CONFIG_PATH=str(config_path),
        OPENAI_API_KEY="",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(model_registry, "settings", _SettingsProxy(test_settings))

    response = model_registry.list_models_response()

    assert response.models == []


def test_model_registry_rejects_invalid_model_config(tmp_path, monkeypatch):
    valid_config_path = tmp_path / "valid-models.yaml"
    _write_model_config(valid_config_path)
    config_path = tmp_path / "models.yaml"
    config_path.write_text("version: '1'\nmodels:\n  - id: broken\n", encoding="utf-8")
    test_settings = _SettingsProxy(_build_settings(
        OPENAI_API_KEY="test-key",
        DEFAULT_MODEL_ID="custom-model",
        MODEL_CONFIG_PATH=str(valid_config_path),
    ))
    test_settings.registry = SimpleNamespace(
        default_model_id="custom-model",
        model_config_path=config_path,
    )
    monkeypatch.setattr(model_registry, "settings", test_settings)

    with pytest.raises(RuntimeError, match="model broken requires"):
        model_registry.list_models_response()


@pytest.mark.asyncio
async def test_generate_text_uses_model_generation_config(monkeypatch):
    calls = {}
    model = SimpleNamespace(
        litellm_model="openai/custom-model",
        temperature=0.2,
        num_retries=1,
        drop_params=False,
    )

    async def fake_acompletion(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=" result "))],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
        )

    monkeypatch.setattr(ai_gateway, "get_enabled_model", lambda model_id: model)
    monkeypatch.setattr(ai_gateway.litellm, "acompletion", fake_acompletion)

    result = await ai_gateway.generate_text("custom-model", [{"role": "user", "content": "hello"}])

    assert result.text == "result"
    assert result.prompt_tokens == 3
    assert result.completion_tokens == 4
    assert calls["model"] == "openai/custom-model"
    assert calls["temperature"] == 0.2
    assert calls["num_retries"] == 1
    assert calls["drop_params"] is False
