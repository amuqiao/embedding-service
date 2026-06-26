import pytest

from scripts.verify import oss_config_check

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


def test_oss_config_check_prints_non_secret_summary(tmp_path, capsys, monkeypatch):
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

    assert oss_config_check.main(["--env-file", str(env_file)]) == 0

    captured = capsys.readouterr()
    assert "bucket=bucket-a" in captured.out
    assert "region=cn-hangzhou" in captured.out
    assert "project_root=project-a" in captured.out
    assert "access-key-id-value" not in captured.out
    assert "secret-value" not in captured.out


def test_oss_config_check_upload_image_requires_confirmation(tmp_path, monkeypatch):
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

    with pytest.raises(RuntimeError, match="--confirm-upload"):
        oss_config_check.main(["--env-file", str(env_file), "--upload-image", str(image)])


def test_oss_config_check_upload_image_prints_signed_url_without_secret(tmp_path, capsys, monkeypatch):
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
        oss_config_check.oss_image_upload,
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
        oss_config_check.main(
            [
                "--env-file",
                str(env_file),
                "--upload-image",
                str(image),
                "--confirm-upload",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "signed_url=https://signed.example.com/reference.png?Signature=sig" in captured.out
    assert "secret-value" not in captured.out
