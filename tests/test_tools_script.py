import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHON_BIN", None)
    return env


def test_tools_secret_help_describes_generated_secret():
    result = subprocess.run(
        ["./scripts/tools.sh", "secret", "--help"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "secrets.token_urlsafe(32)" in result.stdout
    assert "SERVICE_API_KEY" in result.stdout
    assert ".env" in result.stdout


def test_tools_secret_generates_urlsafe_token_only_on_stdout():
    result = subprocess.run(
        ["./scripts/tools.sh", "secret"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    token = result.stdout.strip()
    assert result.stderr == ""
    assert TOKEN_RE.fullmatch(token)
    assert len(token) >= 32


def test_tools_secret_prefix_prepends_literal_prefix():
    result = subprocess.run(
        ["./scripts/tools.sh", "secret", "--prefix", "prd_"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    token = result.stdout.strip()
    assert result.stderr == ""
    assert token.startswith("prd_")
    assert TOKEN_RE.fullmatch(token)
    assert len(token) >= 36


def test_tools_secret_rejects_non_urlsafe_prefix():
    result = subprocess.run(
        ["./scripts/tools.sh", "secret", "--prefix", "prd/"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "URL-safe" in result.stderr


def test_tools_secret_rejects_missing_prefix_value():
    result = subprocess.run(
        ["./scripts/tools.sh", "secret", "--prefix"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--prefix requires a value" in result.stderr


def test_tools_secret_honors_python_bin_when_path_does_not_have_python3(tmp_path):
    dirname_bin = shutil.which("dirname")
    assert dirname_bin is not None
    (tmp_path / "dirname").symlink_to(dirname_bin)

    env = _env()
    env["PATH"] = str(tmp_path)
    env["PYTHON_BIN"] = sys.executable

    result = subprocess.run(
        ["/bin/bash", "./scripts/tools.sh", "secret"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    token = result.stdout.strip()
    assert result.stderr == ""
    assert TOKEN_RE.fullmatch(token)


def test_tools_secret_rejects_unknown_argument():
    result = subprocess.run(
        ["./scripts/tools.sh", "secret", "--format", "hex"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "用法：" in result.stderr
