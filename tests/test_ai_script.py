import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class _ModelsHandler(BaseHTTPRequestHandler):
    expected_path = "/compatible-mode/v1/models"
    expected_auth = "Bearer dashscope-test-key"
    response_status = 200
    response_body: bytes | None = None
    requests: list[dict[str, str]] = []

    def do_GET(self):
        type(self).requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
            }
        )
        if self.path != self.expected_path:
            self.send_response(404)
            self.end_headers()
            return
        if self.headers.get("Authorization") != self.expected_auth:
            self.send_response(401)
            self.end_headers()
            return

        if self.response_body is None:
            body = json.dumps(
                {
                    "object": "list",
                    "data": [
                        {"id": "qwen-max", "object": "model", "created": 1700000001, "owned_by": "dashscope"},
                        {"id": "qwen-plus", "object": "model", "created": 1700000000, "owned_by": "dashscope"},
                        {"id": "text-embedding-v4", "object": "model", "created": 1700000002},
                    ],
                }
            ).encode("utf-8")
        else:
            body = self.response_body
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def _env() -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "PYTHON_BIN",
        "ENV_FILE",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
    ]:
        env.pop(key, None)
    return env


def _run_server():
    _ModelsHandler.requests = []
    _ModelsHandler.response_status = 200
    _ModelsHandler.response_body = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _write_dashscope_env(tmp_path: Path, base_url: str, *, api_key: str = "dashscope-test-key") -> Path:
    env_file = tmp_path / ".env.ai"
    env_file.write_text(
        "\n".join(
            [
                f"DASHSCOPE_API_KEY={api_key}",
                f"DASHSCOPE_BASE_URL={base_url}/compatible-mode/v1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return env_file


def test_ai_help_describes_models_boundary():
    result = subprocess.run(
        ["./scripts/ai.sh", "--help"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "models" in result.stdout
    assert "默认读取根目录 .env" in result.stdout
    assert "真实访问模型厂商" in result.stdout
    assert "不读本项目 models.yaml" in result.stdout


def test_ai_models_reads_env_file_and_lists_dashscope_remote_models(tmp_path):
    server = _run_server()
    try:
        env_file = _write_dashscope_env(tmp_path, f"http://127.0.0.1:{server.server_port}")

        result = subprocess.run(
            ["./scripts/ai.sh", "--env-file", str(env_file), "models", "dashscope", "--json"],
            cwd=ROOT_DIR,
            env=_env(),
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    data = json.loads(result.stdout)
    assert result.stderr == ""
    assert data["ok"] is True
    assert data["env_file"] == str(env_file)
    assert [provider["provider"] for provider in data["providers"]] == ["dashscope"]
    assert data["providers"][0]["model_count"] == 3
    assert [model["id"] for model in data["providers"][0]["models"]] == [
        "qwen-max",
        "qwen-plus",
        "text-embedding-v4",
    ]
    assert "dashscope-test-key" not in result.stdout
    assert _ModelsHandler.requests == [
        {
            "path": "/compatible-mode/v1/models",
            "authorization": "Bearer dashscope-test-key",
        }
    ]


def test_ai_models_reads_env_file_variable(tmp_path):
    server = _run_server()
    try:
        env_file = _write_dashscope_env(tmp_path, f"http://127.0.0.1:{server.server_port}")
        env = _env()
        env["ENV_FILE"] = str(env_file)

        result = subprocess.run(
            ["./scripts/ai.sh", "models", "dashscope", "--json"],
            cwd=ROOT_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    data = json.loads(result.stdout)
    assert data["env_file"] == str(env_file)
    assert data["providers"][0]["provider"] == "dashscope"
    assert data["providers"][0]["model_count"] == 3


def test_ai_explicit_env_file_ignores_shell_provider_override(tmp_path, monkeypatch):
    from scripts.ai import cli as ai_cli

    env_file = _write_dashscope_env(tmp_path, "http://file-config.example")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "shell-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "http://shell-config.example/compatible-mode/v1")

    values, loaded_path = ai_cli._load_env_values(str(env_file))

    assert loaded_path == env_file
    assert values["DASHSCOPE_API_KEY"] == "dashscope-test-key"
    assert values["DASHSCOPE_BASE_URL"] == "http://file-config.example/compatible-mode/v1"


def test_ai_env_file_variable_ignores_shell_provider_override(tmp_path, monkeypatch):
    from scripts.ai import cli as ai_cli

    env_file = _write_dashscope_env(tmp_path, "http://env-file-config.example")
    monkeypatch.setenv("ENV_FILE", str(env_file))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "shell-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "http://shell-config.example/compatible-mode/v1")

    values, loaded_path = ai_cli._load_env_values(None)

    assert loaded_path == env_file
    assert values["DASHSCOPE_API_KEY"] == "dashscope-test-key"
    assert values["DASHSCOPE_BASE_URL"] == "http://env-file-config.example/compatible-mode/v1"


def test_ai_default_env_allows_shell_provider_override(tmp_path, monkeypatch):
    from scripts.ai import cli as ai_cli

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DASHSCOPE_API_KEY=file-key",
                "DASHSCOPE_BASE_URL=http://file-config.example/compatible-mode/v1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_cli, "ROOT_DIR", tmp_path)
    monkeypatch.delenv("ENV_FILE", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "shell-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "http://shell-config.example/compatible-mode/v1")

    values, loaded_path = ai_cli._load_env_values(None)

    assert loaded_path == tmp_path / ".env"
    assert values["DASHSCOPE_API_KEY"] == "shell-key"
    assert values["DASHSCOPE_BASE_URL"] == "http://shell-config.example/compatible-mode/v1"


def test_ai_models_auto_selects_configured_providers(tmp_path):
    server = _run_server()
    try:
        env_file = _write_dashscope_env(tmp_path, f"http://127.0.0.1:{server.server_port}")

        result = subprocess.run(
            ["./scripts/ai.sh", "models", "--env-file", str(env_file), "--json"],
            cwd=ROOT_DIR,
            env=_env(),
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    data = json.loads(result.stdout)
    assert [provider["provider"] for provider in data["providers"]] == ["dashscope"]
    assert data["providers"][0]["models"][1]["id"] == "qwen-plus"


def test_ai_models_missing_provider_key_fails_fast(tmp_path):
    env_file = tmp_path / ".env.ai"
    env_file.write_text("DASHSCOPE_BASE_URL=http://127.0.0.1:9/compatible-mode/v1\n", encoding="utf-8")

    result = subprocess.run(
        ["./scripts/ai.sh", "--env-file", str(env_file), "models", "dashscope", "--json"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "DASHSCOPE_API_KEY is required for provider dashscope" in result.stderr


def test_ai_models_invalid_key_reports_auth_failure(tmp_path):
    server = _run_server()
    try:
        env_file = _write_dashscope_env(
            tmp_path,
            f"http://127.0.0.1:{server.server_port}",
            api_key="wrong-key",
        )

        result = subprocess.run(
            ["./scripts/ai.sh", "--env-file", str(env_file), "models", "dashscope"],
            cwd=ROOT_DIR,
            env=_env(),
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 4
    assert result.stdout == ""
    assert "dashscope API key is not authorized; HTTP 401" in result.stderr
    assert "wrong-key" not in result.stderr


def test_ai_models_rejects_sensitive_base_url(tmp_path):
    env_file = tmp_path / ".env.ai"
    env_file.write_text(
        "\n".join(
            [
                "DASHSCOPE_API_KEY=dashscope-test-key",
                "DASHSCOPE_BASE_URL=http://user:secret@127.0.0.1:9/compatible-mode/v1?token=hidden",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["./scripts/ai.sh", "--env-file", str(env_file), "models", "dashscope"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "DASHSCOPE_BASE_URL must not include credentials, query, or fragment" in result.stderr
    assert "secret" not in result.stderr
    assert "hidden" not in result.stderr


def test_ai_models_does_not_echo_upstream_error_body(tmp_path):
    server = _run_server()
    _ModelsHandler.response_status = 500
    _ModelsHandler.response_body = b'{"error":"secret-upstream-body"}'
    try:
        env_file = _write_dashscope_env(tmp_path, f"http://127.0.0.1:{server.server_port}")

        result = subprocess.run(
            ["./scripts/ai.sh", "--env-file", str(env_file), "models", "dashscope"],
            cwd=ROOT_DIR,
            env=_env(),
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 4
    assert result.stdout == ""
    assert "dashscope models request returned HTTP 500" in result.stderr
    assert "secret-upstream-body" not in result.stderr


def test_ai_openai_uses_default_base_url_when_env_base_url_is_empty():
    from scripts.ai import cli as ai_cli

    runtime = ai_cli._runtime_for_provider("openai", {"OPENAI_API_KEY": "openai-test-key"})

    assert runtime.base_url == "https://api.openai.com/v1"
    assert ai_cli._models_url(runtime.base_url) == "https://api.openai.com/v1/models"
