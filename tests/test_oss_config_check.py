import pytest

from scripts.oss import cli as oss_cli
from smoke.flows import llm_job_billing

STORAGE_ENV_KEYS = [
    "STORAGE_BACKEND",
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

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def object_key(self, key):
            return f"{self.config.normalized_project_root}/{key}"

        def put_object(self, key, data, *, content_type):
            seen_keys.append(key)
            return {}

        def get_object(self, key):
            seen_keys.append(key)
            return oss_cli.DEFAULT_TEST_CONTENT

        def head_object(self, key):
            seen_keys.append(key)
            return {"Content-Length": str(len(oss_cli.DEFAULT_TEST_CONTENT))}

    monkeypatch.setattr(oss_cli, "AliyunOSSClient", FakeClient)

    assert oss_cli.main(["check", "--env-file", str(env_file), "--remote", "--confirm"]) == 0

    captured = capsys.readouterr()
    assert seen_keys
    assert all(key.startswith("ai-jobs/oss-check/") for key in seen_keys)
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

    with pytest.raises(llm_job_billing.FlowError, match="--confirm-upload"):
        oss_cli.main(["upload-image", str(image), "--env-file", str(env_file)])


def test_oss_cli_upload_image_prints_signed_url_without_secret(tmp_path, capsys, monkeypatch):
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
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        oss_cli.oss_image_upload,
        "upload_image",
        lambda **_kwargs: {
            "provider": "aliyun_oss",
            "bucket": "bucket-a",
            "region": "cn-hangzhou",
            "key": "project-a/reference.png",
            "signed_url": "https://signed.example.com/reference.png?Signature=sig",
            "url_ref": {"public_url": "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/reference.png"},
        },
    )

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
    assert "signed_url=https://signed.example.com/reference.png?Signature=sig" in captured.out
    assert "secret-value" not in captured.out
