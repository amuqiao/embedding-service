import json
from pathlib import Path

import pytest

from app.object_storage import (
    AliyunOSSConfig,
    AliyunOSSRepository,
    ObjectMeta,
    ObjectRef,
    ObjectStorageRepository,
    PutObjectResult,
    join_key,
)
from scripts.oss import cli as oss_cli
from smoke.harness.errors import FlowError

STORAGE_ENV_KEYS = [
    "STORAGE_BACKEND",
    "LOCAL_OBJECT_STORAGE_PATH",
    "OSS_INPUT_MAX_BYTES",
    "OSS_BUCKET",
    "OSS_REGION",
    "OSS_ACCESS_KEY_ID",
    "OSS_ACCESS_KEY_SECRET",
    "OSS_PROJECT_ROOT",
    "OSS_OUTPUT_PREFIX",
    "OSS_PUBLIC_ENDPOINT",
    "OSS_ENDPOINT",
]


class FakeRepository(ObjectStorageRepository):
    def __init__(self, config):
        self.config = config
        self.put_keys = []
        self.get_keys = []
        self.head_keys = []
        self.objects = {}

    def put_bytes(self, key, data, *, content_type, content_disposition=None):
        self.put_keys.append(key)
        object_key = join_key(str(self.config.options.get("key_prefix") or ""), key)
        self.objects[object_key] = (bytes(data), content_type)
        return PutObjectResult(
            provider=self.config.provider,
            bucket=str(self.config.options["bucket"]),
            region=str(self.config.options["region"]),
            key=object_key,
            content_type=content_type,
            size_bytes=len(data),
            sha256=oss_cli.sha256_digest(data),
            public_url=f"{self.config.options.get('public_base_url')}/{object_key}"
            if self.config.options.get("public_base_url")
            else "",
        )

    def get_bytes(self, ref: ObjectRef) -> bytes:
        self.get_keys.append(ref.key)
        return self.objects[ref.key][0]

    def head(self, ref: ObjectRef) -> ObjectMeta:
        self.head_keys.append(ref.key)
        data, content_type = self.objects[ref.key]
        return ObjectMeta(
            provider=ref.provider,
            bucket=ref.bucket,
            region=ref.region,
            key=ref.key,
            content_type=content_type,
            size_bytes=len(data),
        )

    def delete(self, ref: ObjectRef) -> None:
        raise AssertionError("delete must not be called by oss check")


def clear_storage_env(monkeypatch):
    for key in STORAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_oss_cli_check_prints_non_secret_summary(tmp_path, capsys, monkeypatch):
    clear_storage_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket-a",
                "OSS_REGION=cn-hangzhou",
                "OSS_ACCESS_KEY_ID=access-key-id-value",
                "OSS_ACCESS_KEY_SECRET=secret-value",
                "OSS_PROJECT_ROOT=project-a",
            ]
        ),
        encoding="utf-8",
    )

    assert oss_cli.main(["check", "--env-file", str(env_file)]) == 0

    captured = capsys.readouterr()
    assert "bucket=bucket-a" in captured.out
    assert "region=cn-hangzhou" in captured.out
    assert "project_root=project-a" in captured.out
    assert "access-key-id-value" not in captured.out
    assert "secret-value" not in captured.out


def test_oss_cli_check_uses_explicit_env_file_over_runtime_env(tmp_path, capsys, monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setenv("OSS_BUCKET", "runtime-bucket")
    monkeypatch.setenv("OSS_REGION", "cn-shanghai")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=file-bucket",
                "OSS_REGION=cn-hangzhou",
                "OSS_ACCESS_KEY_ID=id",
                "OSS_ACCESS_KEY_SECRET=secret",
                "OSS_PROJECT_ROOT=project-a",
            ]
        ),
        encoding="utf-8",
    )

    assert oss_cli.main(["check", "--env-file", str(env_file)]) == 0

    captured = capsys.readouterr()
    assert "bucket=file-bucket" in captured.out
    assert "region=cn-hangzhou" in captured.out
    assert "runtime-bucket" not in captured.out


def test_oss_cli_check_json_outputs_pure_json(tmp_path, capsys, monkeypatch):
    clear_storage_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket-a",
                "OSS_REGION=cn-hangzhou",
                "OSS_ACCESS_KEY_ID=access-key-id-value",
                "OSS_ACCESS_KEY_SECRET=secret-value",
                "OSS_PROJECT_ROOT=project-a",
            ]
        ),
        encoding="utf-8",
    )

    assert oss_cli.main(["check", "--env-file", str(env_file), "--json"]) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["config"]["bucket"] == "bucket-a"
    assert payload["config"]["endpoint"] == "oss-cn-hangzhou.aliyuncs.com"
    assert "access-key-id-value" not in captured.out
    assert "secret-value" not in captured.out
    assert "[OK]" not in captured.out


def test_oss_cli_public_endpoint_does_not_drive_api_endpoint(tmp_path, capsys, monkeypatch):
    clear_storage_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket-a",
                "OSS_REGION=cn-hangzhou",
                "OSS_ACCESS_KEY_ID=id",
                "OSS_ACCESS_KEY_SECRET=secret",
                "OSS_PROJECT_ROOT=project-a",
                "OSS_PUBLIC_ENDPOINT=cdn.example.com",
            ]
        ),
        encoding="utf-8",
    )

    assert oss_cli.main(["check", "--env-file", str(env_file)]) == 0

    captured = capsys.readouterr()
    assert "endpoint=oss-cn-hangzhou.aliyuncs.com" in captured.out
    assert "endpoint_style=virtual_host" in captured.out
    assert "public_endpoint=cdn.example.com" in captured.out


def test_aliyun_object_storage_custom_domain_uses_endpoint_host_without_bucket():
    repository = AliyunOSSRepository(
        AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
            endpoint="cdn.example.com",
            endpoint_style="custom_domain",
        )
    )

    assert repository._object_url("project-a/image.png") == "https://cdn.example.com/project-a/image.png"
    assert repository._public_url("project-a/image.png") == "https://cdn.example.com/project-a/image.png"


def test_oss_cli_normalizes_custom_domain_endpoint_style(tmp_path, capsys, monkeypatch):
    clear_storage_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket-a",
                "OSS_REGION=cn-hangzhou",
                "OSS_ACCESS_KEY_ID=id",
                "OSS_ACCESS_KEY_SECRET=secret",
                "OSS_PROJECT_ROOT=project-a",
                "OSS_PUBLIC_ENDPOINT=cdn.example.com",
                "OSS_ENDPOINT=https://cdn.example.com/",
            ]
        ),
        encoding="utf-8",
    )
    repositories = []

    def fake_build_repository(config):
        repository = FakeRepository(config)
        repositories.append(repository)
        return repository

    monkeypatch.setattr(oss_cli, "build_repository", fake_build_repository)

    assert oss_cli.main(["check", "--env-file", str(env_file)]) == 0

    captured = capsys.readouterr()
    assert repositories[0].config.options["endpoint_style"] == "custom_domain"
    assert "endpoint=cdn.example.com" in captured.out
    assert "endpoint_style=custom_domain" in captured.out
    assert "public_endpoint=cdn.example.com" in captured.out


def test_oss_cli_remote_default_key_uses_output_prefix(tmp_path, capsys, monkeypatch):
    clear_storage_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket-a",
                "OSS_REGION=cn-hangzhou",
                "OSS_ACCESS_KEY_ID=id",
                "OSS_ACCESS_KEY_SECRET=secret",
                "OSS_PROJECT_ROOT=project-a",
                "OSS_OUTPUT_PREFIX=ai-jobs",
            ]
        ),
        encoding="utf-8",
    )

    seen_keys = []
    repositories = []

    def fake_build_repository(config):
        repository = FakeRepository(config)
        repositories.append(repository)
        return repository

    monkeypatch.setattr(oss_cli, "build_repository", fake_build_repository)

    assert oss_cli.main(["check", "--env-file", str(env_file), "--remote", "--confirm"]) == 0

    repository = repositories[0]
    seen_keys.extend(repository.put_keys + repository.head_keys + repository.get_keys)
    captured = capsys.readouterr()
    assert seen_keys
    assert repository.put_keys[0].startswith("ai-jobs/oss-check/")
    assert all(key.startswith("project-a/ai-jobs/oss-check/") for key in repository.head_keys + repository.get_keys)
    assert "key=project-a/ai-jobs/oss-check/" in captured.out
    assert "retained=true" in captured.out


def test_oss_cli_upload_image_requires_confirmation(tmp_path, monkeypatch):
    clear_storage_env(monkeypatch)
    env_file = tmp_path / ".env"
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")
    env_file.write_text(
        "\n".join(
            [
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket-a",
                "OSS_REGION=cn-hangzhou",
                "OSS_ACCESS_KEY_ID=id",
                "OSS_ACCESS_KEY_SECRET=secret",
                "OSS_PROJECT_ROOT=project-a",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(FlowError, match="--confirm-upload"):
        oss_cli.main(["upload-image", str(image), "--env-file", str(env_file)])


def test_oss_cli_upload_image_prints_public_url_without_secret(tmp_path, capsys, monkeypatch):
    clear_storage_env(monkeypatch)
    env_file = tmp_path / ".env"
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")
    env_file.write_text(
        "\n".join(
            [
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket-a",
                "OSS_REGION=cn-hangzhou",
                "OSS_ACCESS_KEY_ID=access-key-id-value",
                "OSS_ACCESS_KEY_SECRET=secret-value",
                "OSS_PROJECT_ROOT=project-a",
                "OSS_PUBLIC_ENDPOINT=cdn.example.com",
            ]
        ),
        encoding="utf-8",
    )
    repositories = []

    def fake_build_repository(config):
        repository = FakeRepository(config)
        repositories.append(repository)
        return repository

    monkeypatch.setattr(oss_cli, "build_repository", fake_build_repository)

    assert (
        oss_cli.main(
            [
                "upload-image",
                str(image),
                "--env-file",
                str(env_file),
                "--confirm-upload",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert repositories[0].put_keys[0].startswith("ai-jobs/oss/uploads/images/")
    assert "public_url=https://cdn.example.com/project-a/ai-jobs/oss/uploads/images/" in captured.out
    assert "signed_url=" not in captured.out
    assert "secret-value" not in captured.out


def test_oss_cli_no_longer_imports_legacy_oss_paths():
    source = Path(oss_cli.__file__).read_text(encoding="utf-8")

    assert "app.integrations.aliyun_oss" not in source
    assert "smoke.flows.oss.image_upload" not in source
