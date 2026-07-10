import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
REQUIRED_FILES = [
    "htdemucs_ft_drums.onnx",
    "htdemucs_ft_bass.onnx",
    "htdemucs_ft_other.onnx",
    "htdemucs_ft_vocals.onnx",
    "bag_infer.py",
    "requirements.txt",
]
OPTIONAL_FILES = [
    ".gitattributes",
    "README.md",
    "htdemucs_ft_bass_fp16weights.onnx",
    "htdemucs_ft_drums_fp16weights.onnx",
    "htdemucs_ft_other_fp16weights.onnx",
    "htdemucs_ft_vocals_fp16weights.onnx",
]


def _env(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("HF_CLI", None)
    env.update(overrides)
    return env


def _write_required_files(model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_FILES:
        (model_dir / name).write_text(f"{name}\n", encoding="utf-8")


def _write_onnx_files(model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_FILES[:4]:
        (model_dir / name).write_text(f"{name}\n", encoding="utf-8")


def _write_all_files(model_dir: Path) -> None:
    _write_required_files(model_dir)
    for name in OPTIONAL_FILES:
        (model_dir / name).write_text(f"{name}\n", encoding="utf-8")


def _write_fake_onnxruntime(module_dir: Path) -> Path:
    module = module_dir / "onnxruntime.py"
    module.write_text(
        "import os\n"
        "__version__ = '1.99.0'\n"
        "\n"
        "class Meta:\n"
        "    producer_name = 'fake-producer'\n"
        "    graph_name = 'fake-graph'\n"
        "    domain = ''\n"
        "    description = ''\n"
        "    version = 1\n"
        "    custom_metadata_map = {'export': 'fake'}\n"
        "\n"
        "class Node:\n"
        "    def __init__(self, name, type_, shape):\n"
        "        self.name = name\n"
        "        self.type = type_\n"
        "        self.shape = shape\n"
        "\n"
        "class InferenceSession:\n"
        "    def __init__(self, path, providers=None):\n"
        "        capture = os.environ.get('FAKE_ORT_CAPTURE')\n"
        "        if capture:\n"
        "            with open(capture, 'a', encoding='utf-8') as handle:\n"
        "                handle.write(path + '|' + ','.join(providers or []) + '\\n')\n"
        "    def get_inputs(self):\n"
        "        return [Node('mix', 'tensor(float)', [1, 2, 343980])]\n"
        "    def get_outputs(self):\n"
        "        return [Node('stems', 'tensor(float)', [1, 4, 2, 343980])]\n"
        "    def get_modelmeta(self):\n"
        "        return Meta()\n"
        "    def get_providers(self):\n"
        "        return ['CPUExecutionProvider']\n"
        "\n"
        "def get_available_providers():\n"
        "    return ['CPUExecutionProvider']\n",
        encoding="utf-8",
    )
    return module


def _write_broken_onnxruntime(module_dir: Path) -> Path:
    module = module_dir / "onnxruntime.py"
    module.write_text("raise ImportError('broken ort import')\n", encoding="utf-8")
    return module


def test_models_list_json_has_htdemucs_asset():
    result = subprocess.run(
        ["./scripts/models.sh", "list", "--json"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert '"model":"htdemucs-ft"' in result.stdout
    assert '"repo":"StemSplitio/htdemucs-ft-onnx"' in result.stdout
    assert result.stderr == ""


def test_models_top_level_help_documents_scope_and_hf_endpoint():
    result = subprocess.run(
        ["./scripts/models.sh", "--help"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "HF_ENDPOINT" in result.stdout
    assert "required" in result.stdout
    assert "all-files" in result.stdout
    assert "inspect" in result.stdout
    assert "HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh download htdemucs-ft" in result.stdout
    assert "HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh verify htdemucs-ft --remote-check --all-files" in result.stdout


def test_models_status_reports_missing_required_files(tmp_path):
    model_dir = tmp_path / "model"

    result = subprocess.run(
        ["./scripts/models.sh", "status", "htdemucs-ft", "--model-dir", str(model_dir)],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "MISSING" in result.stdout
    assert "htdemucs_ft_drums.onnx" in result.stdout
    assert result.stderr == ""


def test_models_status_json_reports_required_scope_by_default(tmp_path):
    model_dir = tmp_path / "model"
    _write_required_files(model_dir)

    result = subprocess.run(
        ["./scripts/models.sh", "status", "htdemucs-ft", "--model-dir", str(model_dir), "--json"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert '"scope":"required"' in result.stdout
    assert '"complete":true' in result.stdout
    assert "fp16weights" not in result.stdout


def test_models_status_all_files_reports_missing_optional_files(tmp_path):
    model_dir = tmp_path / "model"
    _write_required_files(model_dir)

    result = subprocess.run(
        ["./scripts/models.sh", "status", "htdemucs-ft", "--model-dir", str(model_dir), "--all-files"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "scope" in result.stdout
    assert "all-files" in result.stdout
    assert "MISSING" in result.stdout
    assert "htdemucs_ft_bass_fp16weights.onnx" in result.stdout


def test_models_verify_fails_when_required_files_are_missing(tmp_path):
    result = subprocess.run(
        ["./scripts/models.sh", "verify", "htdemucs-ft", "--model-dir", str(tmp_path / "model")],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "model files for scope 'required' are missing or empty" in result.stderr
    assert "htdemucs_ft_vocals.onnx" in result.stderr


def test_models_subcommands_print_usage_to_stderr_when_model_is_missing():
    for command in ("status", "verify", "inspect", "download"):
        result = subprocess.run(
            ["./scripts/models.sh", command],
            cwd=ROOT_DIR,
            env=_env(),
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 2
        assert result.stdout == ""
        assert "用法：" in result.stderr


def test_models_inspect_help_documents_no_inference():
    result = subprocess.run(
        ["./scripts/models.sh", "inspect", "--help"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "ONNX" in result.stdout
    assert "sha256" in result.stdout
    assert "不执行推理" in result.stdout
    assert "--providers" in result.stdout


def test_models_inspect_json_reports_onnx_signatures_and_sha256(tmp_path):
    model_dir = tmp_path / "model"
    _write_required_files(model_dir)
    _write_fake_onnxruntime(tmp_path)
    captured = tmp_path / "ort.sessions"
    env = _env(
        PYTHON_BIN=sys.executable,
        PYTHONPATH=str(tmp_path),
        FAKE_ORT_CAPTURE=str(captured),
    )

    result = subprocess.run(
        [
            "./scripts/models.sh",
            "inspect",
            "htdemucs-ft",
            "--model-dir",
            str(model_dir),
            "--providers",
            "CPUExecutionProvider",
            "--json",
        ],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stderr == ""
    assert '"model":"htdemucs-ft"' in result.stdout
    assert '"source":"huggingface"' in result.stdout
    assert '"scope":"required"' in result.stdout
    assert '"complete":true' in result.stdout
    assert '"onnxruntime_version":"1.99.0"' in result.stdout
    assert '"available_providers":["CPUExecutionProvider"]' in result.stdout
    assert '"stem":"drums"' in result.stdout
    assert '"size_bytes":' in result.stdout
    assert '"producer_name":"fake-producer"' in result.stdout
    assert '"session_providers":["CPUExecutionProvider"]' in result.stdout
    assert '"name":"mix"' in result.stdout
    assert '"type":"tensor(float)"' in result.stdout
    assert '"dtype":"float"' in result.stdout
    assert '"shape":[1,2,343980]' in result.stdout
    assert '"name":"stems"' in result.stdout
    assert '"shape":[1,4,2,343980]' in result.stdout
    assert '"auxiliary_files":' in result.stdout
    assert '"file":"bag_infer.py"' in result.stdout
    assert '"present":true' in result.stdout

    sessions = captured.read_text(encoding="utf-8").splitlines()
    assert len(sessions) == 4
    assert sessions[0].endswith("htdemucs_ft_drums.onnx|CPUExecutionProvider")


def test_models_inspect_human_reports_expert_files(tmp_path):
    model_dir = tmp_path / "model"
    _write_required_files(model_dir)
    _write_fake_onnxruntime(tmp_path)

    result = subprocess.run(
        ["./scripts/models.sh", "inspect", "htdemucs-ft", "--model-dir", str(model_dir)],
        cwd=ROOT_DIR,
        env=_env(PYTHON_BIN=sys.executable, PYTHONPATH=str(tmp_path)),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "ONNX Inspect" in result.stdout
    assert "htdemucs_ft_vocals.onnx" in result.stdout
    assert "sha256" in result.stdout
    assert "session-providers CPUExecutionProvider" in result.stdout
    assert "mix tensor(float) [1, 2, 343980]" in result.stdout
    assert "stems tensor(float) [1, 4, 2, 343980]" in result.stdout
    assert result.stderr == ""


def test_models_inspect_fails_when_onnx_file_is_missing(tmp_path):
    model_dir = tmp_path / "model"
    _write_required_files(model_dir)
    (model_dir / "htdemucs_ft_vocals.onnx").unlink()
    _write_fake_onnxruntime(tmp_path)

    result = subprocess.run(
        ["./scripts/models.sh", "inspect", "htdemucs-ft", "--model-dir", str(model_dir)],
        cwd=ROOT_DIR,
        env=_env(PYTHON_BIN=sys.executable, PYTHONPATH=str(tmp_path)),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "required ONNX file missing or empty" in result.stderr


def test_models_inspect_succeeds_when_auxiliary_files_are_missing(tmp_path):
    model_dir = tmp_path / "model"
    _write_onnx_files(model_dir)
    _write_fake_onnxruntime(tmp_path)

    result = subprocess.run(
        ["./scripts/models.sh", "inspect", "htdemucs-ft", "--model-dir", str(model_dir), "--json"],
        cwd=ROOT_DIR,
        env=_env(PYTHON_BIN=sys.executable, PYTHONPATH=str(tmp_path)),
        capture_output=True,
        text=True,
        check=True,
    )

    assert '"file":"bag_infer.py"' in result.stdout
    assert '"present":false' in result.stdout
    assert result.stderr == ""


def test_models_inspect_reports_onnxruntime_import_failure_without_traceback(tmp_path):
    model_dir = tmp_path / "model"
    _write_onnx_files(model_dir)
    _write_broken_onnxruntime(tmp_path)

    result = subprocess.run(
        ["./scripts/models.sh", "inspect", "htdemucs-ft", "--model-dir", str(model_dir)],
        cwd=ROOT_DIR,
        env=_env(PYTHON_BIN=sys.executable, PYTHONPATH=str(tmp_path)),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "onnxruntime import failed: ImportError: broken ort import" in result.stderr
    assert "Traceback" not in result.stderr


def test_models_verify_passes_with_required_files(tmp_path):
    model_dir = tmp_path / "model"
    _write_required_files(model_dir)

    result = subprocess.run(
        ["./scripts/models.sh", "verify", "htdemucs-ft", "--model-dir", str(model_dir)],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "OK" in result.stdout
    assert "htdemucs_ft_other.onnx" in result.stdout
    assert result.stderr == ""


def test_models_verify_remote_check_uses_hf_cli_override(tmp_path):
    model_dir = tmp_path / "model"
    _write_required_files(model_dir)
    captured = tmp_path / "hf.args"
    fake_hf = tmp_path / "hf"
    fake_hf.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_HF_ARGS\"\n",
        encoding="utf-8",
    )
    fake_hf.chmod(0o755)

    result = subprocess.run(
        [
            "./scripts/models.sh",
            "verify",
            "htdemucs-ft",
            "--model-dir",
            str(model_dir),
            "--remote-check",
            "--revision",
            "abc123",
        ],
        cwd=ROOT_DIR,
        env=_env(HF_CLI=str(fake_hf), FAKE_HF_ARGS=str(captured)),
        capture_output=True,
        text=True,
        check=True,
    )

    args = captured.read_text(encoding="utf-8").splitlines()
    assert args == [
        "cache",
        "verify",
        "StemSplitio/htdemucs-ft-onnx",
        "--local-dir",
        str(model_dir),
        "--revision",
        "abc123",
    ]
    assert "Remote Check" in result.stdout
    assert result.stderr == ""


def test_models_verify_remote_check_all_files_requires_missing_remote_files(tmp_path):
    model_dir = tmp_path / "model"
    _write_all_files(model_dir)
    captured = tmp_path / "hf.args"
    fake_hf = tmp_path / "hf"
    fake_hf.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_HF_ARGS\"\n",
        encoding="utf-8",
    )
    fake_hf.chmod(0o755)

    result = subprocess.run(
        [
            "./scripts/models.sh",
            "verify",
            "htdemucs-ft",
            "--model-dir",
            str(model_dir),
            "--remote-check",
            "--all-files",
        ],
        cwd=ROOT_DIR,
        env=_env(HF_CLI=str(fake_hf), FAKE_HF_ARGS=str(captured)),
        capture_output=True,
        text=True,
        check=True,
    )

    args = captured.read_text(encoding="utf-8").splitlines()
    assert "--fail-on-missing-files" in args
    assert "All Files" in result.stdout
    assert result.stderr == ""


def test_models_verify_all_files_fails_when_optional_files_are_missing(tmp_path):
    model_dir = tmp_path / "model"
    _write_required_files(model_dir)

    result = subprocess.run(
        ["./scripts/models.sh", "verify", "htdemucs-ft", "--model-dir", str(model_dir), "--all-files"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "htdemucs_ft_vocals_fp16weights.onnx" in result.stderr


def test_models_verify_remote_check_rejects_json_output(tmp_path):
    model_dir = tmp_path / "model"
    _write_required_files(model_dir)

    result = subprocess.run(
        [
            "./scripts/models.sh",
            "verify",
            "htdemucs-ft",
            "--model-dir",
            str(model_dir),
            "--remote-check",
            "--json",
        ],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--json cannot be combined with --remote-check" in result.stderr


def test_models_download_rejects_unsupported_source(tmp_path):
    result = subprocess.run(
        [
            "./scripts/models.sh",
            "download",
            "htdemucs-ft",
            "--model-dir",
            str(tmp_path / "model"),
            "--source",
            "modelscope",
        ],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unsupported source 'modelscope'" in result.stderr
    assert "no fallback is attempted" in result.stderr


def test_models_download_uses_hf_and_checks_downloaded_files(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    captured = tmp_path / "hf.args"
    fake_hf = fake_bin / "hf"
    fake_hf.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_HF_ARGS\"\n"
        "local_dir=''\n"
        "prev=''\n"
        "for arg in \"$@\"; do\n"
        "  if [[ \"$prev\" == '--local-dir' ]]; then local_dir=\"$arg\"; fi\n"
        "  prev=\"$arg\"\n"
        "done\n"
        "mkdir -p \"$local_dir\"\n"
        "for name in htdemucs_ft_drums.onnx htdemucs_ft_bass.onnx htdemucs_ft_other.onnx htdemucs_ft_vocals.onnx bag_infer.py requirements.txt .gitattributes README.md htdemucs_ft_bass_fp16weights.onnx htdemucs_ft_drums_fp16weights.onnx htdemucs_ft_other_fp16weights.onnx htdemucs_ft_vocals_fp16weights.onnx; do\n"
        "  printf '%s\\n' \"$name\" > \"$local_dir/$name\"\n"
        "done\n",
        encoding="utf-8",
    )
    fake_hf.chmod(0o755)

    model_dir = tmp_path / "model"
    env = _env(PATH=f"{fake_bin}:{os.environ['PATH']}", FAKE_HF_ARGS=str(captured))

    result = subprocess.run(
        [
            "./scripts/models.sh",
            "download",
            "htdemucs-ft",
            "--model-dir",
            str(model_dir),
            "--revision",
            "abc123",
            "--max-workers",
            "2",
        ],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    args = captured.read_text(encoding="utf-8").splitlines()
    assert args == [
        "download",
        "StemSplitio/htdemucs-ft-onnx",
        "--local-dir",
        str(model_dir),
        "--include",
        "htdemucs_ft_drums.onnx",
        "--include",
        "htdemucs_ft_bass.onnx",
        "--include",
        "htdemucs_ft_other.onnx",
        "--include",
        "htdemucs_ft_vocals.onnx",
        "--include",
        "bag_infer.py",
        "--include",
        "requirements.txt",
        "--revision",
        "abc123",
        "--max-workers",
        "2",
    ]
    assert "Required Files" in result.stdout
    assert (model_dir / "bag_infer.py").exists()
    assert result.stderr == ""


def test_models_download_all_files_skips_required_file_includes(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    captured = tmp_path / "hf.args"
    fake_hf = fake_bin / "hf"
    fake_hf.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_HF_ARGS\"\n"
        "local_dir=''\n"
        "prev=''\n"
        "for arg in \"$@\"; do\n"
        "  if [[ \"$prev\" == '--local-dir' ]]; then local_dir=\"$arg\"; fi\n"
        "  prev=\"$arg\"\n"
        "done\n"
        "mkdir -p \"$local_dir\"\n"
        "for name in htdemucs_ft_drums.onnx htdemucs_ft_bass.onnx htdemucs_ft_other.onnx htdemucs_ft_vocals.onnx bag_infer.py requirements.txt .gitattributes README.md htdemucs_ft_bass_fp16weights.onnx htdemucs_ft_drums_fp16weights.onnx htdemucs_ft_other_fp16weights.onnx htdemucs_ft_vocals_fp16weights.onnx; do\n"
        "  printf '%s\\n' \"$name\" > \"$local_dir/$name\"\n"
        "done\n",
        encoding="utf-8",
    )
    fake_hf.chmod(0o755)

    model_dir = tmp_path / "model"
    env = _env(PATH=f"{fake_bin}:{os.environ['PATH']}", FAKE_HF_ARGS=str(captured))

    result = subprocess.run(
        [
            "./scripts/models.sh",
            "download",
            "htdemucs-ft",
            "--model-dir",
            str(model_dir),
            "--all-files",
        ],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    args = captured.read_text(encoding="utf-8").splitlines()
    assert "--include" not in args
    assert args == [
        "download",
        "StemSplitio/htdemucs-ft-onnx",
        "--local-dir",
        str(model_dir),
    ]
    assert "All Files" in result.stdout
    assert result.stderr == ""
