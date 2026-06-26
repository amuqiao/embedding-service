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
        self.billing = settings_obj.billing
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
    model_type: text
    adapter: litellm
    provider: openai
    provider_model: custom-model
    adapter_model: openai/custom-model
    pricing_ref: openai:custom-model@2026-06-23
    enabled: true
    capabilities:
      - text_generation
    input_media_types:
      - text/plain
    output_media_types:
      - text/plain
    limits:
      context_window: 12345
    features:
      supports_json_output: true
    parameters:
      public: []
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
    assert response.models[0].capabilities == ["text_generation"]
    assert response.models[0].input_media_types == ["text/plain"]
    assert response.models[0].output_media_types == ["text/plain"]
    assert response.models[0].model_type == "text"
    assert response.models[0].limits == {"context_window": 12345}
    assert response.models[0].features == {"supports_json_output": True}
    assert response.models[0].parameters == []
    assert response.billing_enabled is None
    assert response.cost_estimate_available is None


def test_model_registry_exposes_billing_capability_when_enabled(tmp_path, monkeypatch):
    config_path = tmp_path / "models.yaml"
    _write_model_config(config_path)
    test_settings = _build_settings(
        OPENAI_API_KEY="test-key",
        DEFAULT_MODEL_ID="custom-model",
        MODEL_CONFIG_PATH=str(config_path),
        BILLING_ENABLED="false",
        MODEL_CATALOG_EXPOSE_BILLING_CAPABILITY="true",
    )
    monkeypatch.setattr(model_registry, "settings", _SettingsProxy(test_settings))

    response = model_registry.list_models_response()

    assert response.billing_enabled is False
    assert response.cost_estimate_available is False


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


def test_model_registry_validate_catalog_rejects_pricing_mismatch(tmp_path, monkeypatch):
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """\
version: "1"
models:
  - id: custom-model
    name: Custom Model
    model_type: text
    adapter: litellm
    provider: openai
    provider_model: custom-model
    adapter_model: openai/custom-model
    pricing_ref: openai:gpt-5.5@2026-06-23
    enabled: true
    capabilities:
      - text_generation
    input_media_types:
      - text/plain
    output_media_types:
      - text/plain
    limits:
      context_window: 12345
    features:
      supports_json_output: true
    parameters:
      public: []
    notes: ""
    requires_env:
      - OPENAI_API_KEY
    generation:
      temperature: 0.2
      num_retries: 1
      drop_params: false
""",
        encoding="utf-8",
    )
    test_settings = _build_settings(
        OPENAI_API_KEY="test-key",
        DEFAULT_MODEL_ID="custom-model",
        MODEL_CONFIG_PATH=str(config_path),
    )
    monkeypatch.setattr(model_registry, "settings", _SettingsProxy(test_settings))

    with pytest.raises(RuntimeError, match="does not match model custom-model"):
        model_registry.validate_model_catalog()


def test_model_registry_exposes_public_model_parameters(tmp_path, monkeypatch):
    config_path = tmp_path / "models.yaml"
    _write_model_config(config_path)
    config_text = config_path.read_text(encoding="utf-8").replace(
        "    parameters:\n      public: []",
        """\
    parameters:
      public:
        - name: size
          label: Size
          type: select
          required: false
          default: 1024x1024
          options:
            - 1024x1024
            - 1536x1024
        - name: n
          label: Count
          type: integer
          required: false
          default: 1
          min: 1
          max: 4""",
    )
    config_path.write_text(config_text, encoding="utf-8")
    test_settings = _build_settings(
        OPENAI_API_KEY="test-key",
        DEFAULT_MODEL_ID="custom-model",
        MODEL_CONFIG_PATH=str(config_path),
    )
    monkeypatch.setattr(model_registry, "settings", _SettingsProxy(test_settings))

    response = model_registry.list_models_response()

    assert [parameter.name for parameter in response.models[0].parameters] == ["size", "n"]
    size = response.models[0].parameters[0]
    assert size.label == "Size"
    assert size.type == "select"
    assert size.required is False
    assert size.default == "1024x1024"
    assert size.options == ["1024x1024", "1536x1024"]
    assert size.min is None
    assert size.max is None
    count = response.models[0].parameters[1]
    assert count.label == "Count"
    assert count.type == "integer"
    assert count.default == 1
    assert count.min == 1
    assert count.max == 4


def test_model_registry_compares_select_values_by_json_type(tmp_path, monkeypatch):
    config_path = tmp_path / "models.yaml"
    _write_model_config(config_path)
    config_text = config_path.read_text(encoding="utf-8").replace(
        "    parameters:\n      public: []",
        """\
    parameters:
      public:
        - name: flag
          label: Flag
          type: select
          required: false
          default: true
          options:
            - true
            - 1""",
    )
    config_path.write_text(config_text, encoding="utf-8")
    test_settings = _build_settings(
        OPENAI_API_KEY="test-key",
        DEFAULT_MODEL_ID="custom-model",
        MODEL_CONFIG_PATH=str(config_path),
    )
    monkeypatch.setattr(model_registry, "settings", _SettingsProxy(test_settings))

    response = model_registry.list_models_response()

    flag = response.models[0].parameters[0]
    assert flag.default is True
    assert flag.options == [True, 1]


def test_model_registry_supports_image_model_type_public_catalog(tmp_path, monkeypatch):
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """\
version: "1"
models:
  - id: image-model
    name: Image Model
    model_type: image
    adapter: litellm
    provider: openai
    provider_model: image-model
    adapter_model: openai/image-model
    pricing_ref: openai:image-model@2026-06-23
    enabled: true
    capabilities:
      - image_generation
    input_media_types:
      - text/plain
      - image/png
    output_media_types:
      - image/png
      - image/webp
    limits:
      max_output_count: 4
    features:
      supports_edit: true
      native_transparency: false
    parameters:
      public:
        - name: n
          label: Count
          type: integer
          required: false
          default: 1
          min: 1
          max: 4
        - name: output_format
          label: Output format
          type: select
          required: false
          default: png
          options:
            - png
            - jpeg
            - webp
    notes: ""
    requires_env:
      - OPENAI_API_KEY
""",
        encoding="utf-8",
    )
    test_settings = _build_settings(
        OPENAI_API_KEY="test-key",
        DEFAULT_MODEL_ID="image-model",
        MODEL_CONFIG_PATH=str(config_path),
    )
    monkeypatch.setattr(model_registry, "settings", _SettingsProxy(test_settings))

    response = model_registry.list_models_response()

    model = response.models[0]
    assert model.model_type == "image"
    assert model.capabilities == ["image_generation"]
    assert model.output_media_types == ["image/png", "image/webp"]
    assert model.limits == {"max_output_count": 4}
    assert model.features == {"supports_edit": True, "native_transparency": False}
    assert [parameter.name for parameter in model.parameters] == ["n", "output_format"]


def test_model_registry_model_type_does_not_constrain_capabilities_or_output_media(tmp_path, monkeypatch):
    config_path = tmp_path / "models.yaml"
    _write_model_config(config_path)
    config_text = (
        config_path.read_text(encoding="utf-8")
        .replace("model_type: text", "model_type: audio")
        .replace("output_media_types:\n      - text/plain", "output_media_types:\n      - text/plain")
        .replace(
            "limits:\n      context_window: 12345",
            "limits:\n      max_input_seconds: 600",
        )
        .replace(
            "features:\n      supports_json_output: true",
            "features:\n      supports_transcription: true",
        )
        .replace(
            "    generation:\n      temperature: 0.2\n      num_retries: 1\n      drop_params: false\n",
            "",
        )
    )
    config_path.write_text(config_text, encoding="utf-8")
    test_settings = _build_settings(
        OPENAI_API_KEY="test-key",
        DEFAULT_MODEL_ID="custom-model",
        MODEL_CONFIG_PATH=str(config_path),
    )
    monkeypatch.setattr(model_registry, "settings", _SettingsProxy(test_settings))

    response = model_registry.list_models_response()

    model = response.models[0]
    assert model.model_type == "audio"
    assert model.capabilities == ["text_generation"]
    assert model.output_media_types == ["text/plain"]


@pytest.mark.parametrize(
    ("parameters_block", "message"),
    [
        ("parameters: []", "parameters as a YAML object"),
        ("parameters: {}", "parameters.public as a YAML list"),
        (
            """\
parameters:
      public: []
      runtime: {}""",
            "parameters contains unknown fields",
        ),
        (
            """\
parameters:
      public:
        - name: size
          label: Size
          type: select
          required: false
          default: 2048x2048
          options:
            - 1024x1024""",
            "default must be one of options",
        ),
        (
            """\
parameters:
      public:
        - name: n
          label: Count
          type: integer
          required: false
          default: 0
          min: 1
          max: 4""",
            "default is below min",
        ),
        (
            """\
parameters:
      public:
        - name: n
          label: Count
          type: integer
          required: false
          default: 2
          min: 1.5
          max: 4""",
            "min must be an integer",
        ),
        (
            """\
parameters:
      public:
        - name: quality
          label: Quality
          type: select
          required: false
          default: true
          options:
            - 1""",
            "default must be one of options",
        ),
        (
            """\
parameters:
      public:
        - name: quality
          label: Quality
          type: select
          required: false
          default: high
          options:
            - high
            - high""",
            "duplicate option",
        ),
        (
            """\
parameters:
      public:
        - name: quality
          label: Quality
          type: select
          required: false
          default: high
          provider_param: raw_quality""",
            "unknown fields",
        ),
        (
            """\
parameters:
      public:
        - name: n
          label: Count
          type: integer
          required: false
          default: 1
        - name: n
          label: Count
          type: integer
          required: false
          default: 2""",
            "duplicate parameter",
        ),
    ],
)
def test_model_registry_rejects_invalid_public_parameters(tmp_path, monkeypatch, parameters_block, message):
    config_path = tmp_path / "models.yaml"
    _write_model_config(config_path)
    config_text = config_path.read_text(encoding="utf-8").replace(
        "parameters:\n      public: []",
        parameters_block,
    )
    config_path.write_text(config_text, encoding="utf-8")
    test_settings = _build_settings(
        OPENAI_API_KEY="test-key",
        DEFAULT_MODEL_ID="custom-model",
        MODEL_CONFIG_PATH=str(config_path),
    )
    monkeypatch.setattr(model_registry, "settings", _SettingsProxy(test_settings))

    with pytest.raises(RuntimeError, match=message):
        model_registry.list_models_response()


@pytest.mark.parametrize(
    ("block", "replacement", "message"),
    [
        ("model_type: text", "model_type: unknown", "model_type contains unknown value"),
        (
            "limits:\n      context_window: 12345",
            "limits: {}",
            "limits.context_window",
        ),
        (
            "features:\n      supports_json_output: true",
            "features: {}",
            "features.supports_json_output",
        ),
    ],
)
def test_model_registry_rejects_invalid_model_type_contract(tmp_path, monkeypatch, block, replacement, message):
    config_path = tmp_path / "models.yaml"
    _write_model_config(config_path)
    config_text = config_path.read_text(encoding="utf-8").replace(block, replacement)
    config_path.write_text(config_text, encoding="utf-8")
    test_settings = _build_settings(
        OPENAI_API_KEY="test-key",
        DEFAULT_MODEL_ID="custom-model",
        MODEL_CONFIG_PATH=str(config_path),
    )
    monkeypatch.setattr(model_registry, "settings", _SettingsProxy(test_settings))

    with pytest.raises(RuntimeError, match=message):
        model_registry.list_models_response()


def test_repository_model_catalog_validates_with_current_shape():
    model_registry.validate_model_catalog()


def test_model_registry_validate_catalog_rejects_unknown_adapter(tmp_path, monkeypatch):
    config_path = tmp_path / "models.yaml"
    _write_model_config(config_path)
    config_text = config_path.read_text(encoding="utf-8").replace("adapter: litellm", "adapter: missing-adapter")
    config_path.write_text(config_text, encoding="utf-8")
    test_settings = _build_settings(
        OPENAI_API_KEY="test-key",
        DEFAULT_MODEL_ID="custom-model",
        MODEL_CONFIG_PATH=str(config_path),
    )
    monkeypatch.setattr(model_registry, "settings", _SettingsProxy(test_settings))

    with pytest.raises(RuntimeError, match="adapter not found"):
        model_registry.validate_model_catalog()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("capabilities", "[]", "capabilities"),
        ("capabilities", "[text_generation, custom_unknown]", "unknown values"),
        ("capabilities", "[text_generation, text_generation]", "duplicate capabilities"),
        ("input_media_types", "[]", "input_media_types"),
        ("input_media_types", "[textplain]", "invalid media types"),
        ("input_media_types", "[text/plain, text/plain]", "duplicate input_media_types"),
        ("output_media_types", "[]", "output_media_types"),
        ("output_media_types", "[textplain]", "invalid media types"),
        ("output_media_types", "[text/plain, text/plain]", "duplicate output_media_types"),
    ],
)
def test_model_registry_rejects_invalid_capability_or_media_contract(tmp_path, monkeypatch, field, value, message):
    config_path = tmp_path / "models.yaml"
    _write_model_config(config_path)
    config_text = config_path.read_text(encoding="utf-8")
    if field == "capabilities":
        config_text = config_text.replace("capabilities:\n      - text_generation", f"capabilities: {value}")
    elif field == "input_media_types":
        config_text = config_text.replace("input_media_types:\n      - text/plain", f"input_media_types: {value}")
    else:
        config_text = config_text.replace("output_media_types:\n      - text/plain", f"output_media_types: {value}")
    config_path.write_text(config_text, encoding="utf-8")
    test_settings = _build_settings(
        OPENAI_API_KEY="test-key",
        DEFAULT_MODEL_ID="custom-model",
        MODEL_CONFIG_PATH=str(config_path),
    )
    monkeypatch.setattr(model_registry, "settings", _SettingsProxy(test_settings))

    with pytest.raises(RuntimeError, match=message):
        model_registry.list_models_response()


@pytest.mark.parametrize(
    ("field", "block", "message"),
    [
        ("capabilities", "    capabilities:\n      - text_generation\n", "capabilities"),
        ("model_type", "    model_type: text\n", "model_type"),
        ("adapter", "    adapter: litellm\n", "adapter"),
        ("provider_model", "    provider_model: custom-model\n", "provider_model"),
        ("adapter_model", "    adapter_model: openai/custom-model\n", "adapter_model"),
        ("input_media_types", "    input_media_types:\n      - text/plain\n", "input_media_types"),
        ("output_media_types", "    output_media_types:\n      - text/plain\n", "output_media_types"),
        ("limits", "    limits:\n      context_window: 12345\n", "limits"),
        ("features", "    features:\n      supports_json_output: true\n", "features"),
        ("parameters", "    parameters:\n      public: []\n", "parameters"),
    ],
)
def test_model_registry_rejects_missing_capability_or_media_contract(tmp_path, monkeypatch, field, block, message):
    config_path = tmp_path / "models.yaml"
    _write_model_config(config_path)
    config_text = config_path.read_text(encoding="utf-8").replace(block, "")
    config_path.write_text(config_text, encoding="utf-8")
    test_settings = _build_settings(
        OPENAI_API_KEY="test-key",
        DEFAULT_MODEL_ID="custom-model",
        MODEL_CONFIG_PATH=str(config_path),
    )
    monkeypatch.setattr(model_registry, "settings", _SettingsProxy(test_settings))

    with pytest.raises(RuntimeError, match=message):
        model_registry.validate_model_catalog()


@pytest.mark.asyncio
async def test_generate_text_uses_provider_request_config(monkeypatch):
    calls = {}

    request = ai_gateway.LiteLLMTextGenerationRequest(
        litellm_model="openai/custom-model",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.2,
        timeout_seconds=30,
        api_key="test-key",
        api_base="https://example.test/v1",
        num_retries=1,
        drop_params=False,
    )

    async def fake_acompletion(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=" result "))],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
        )

    monkeypatch.setattr(ai_gateway.litellm, "acompletion", fake_acompletion)

    result = await ai_gateway.generate_text(request)

    assert result.text == "result"
    assert result.prompt_tokens == 3
    assert result.completion_tokens == 4
    assert calls["model"] == "openai/custom-model"
    assert calls["messages"] == [{"role": "user", "content": "hello"}]
    assert calls["temperature"] == 0.2
    assert calls["timeout"] == 30
    assert calls["api_key"] == "test-key"
    assert calls["api_base"] == "https://example.test/v1"
    assert calls["num_retries"] == 1
    assert calls["drop_params"] is False


@pytest.mark.asyncio
async def test_generate_text_extracts_dict_usage(monkeypatch):
    request = ai_gateway.LiteLLMTextGenerationRequest(
        litellm_model="openai/custom-model",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.2,
        timeout_seconds=30,
        api_key=None,
        api_base=None,
        num_retries=1,
        drop_params=False,
    )

    async def fake_acompletion(**_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=" result "))],
            usage={"prompt_tokens": 7, "completion_tokens": 8},
        )

    monkeypatch.setattr(ai_gateway.litellm, "acompletion", fake_acompletion)

    result = await ai_gateway.generate_text(request)

    assert result.prompt_tokens == 7
    assert result.completion_tokens == 8
    assert result.usage == {"prompt_tokens": 7, "completion_tokens": 8}
