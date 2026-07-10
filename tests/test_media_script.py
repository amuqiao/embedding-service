import json
import os
import subprocess
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _env(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("FFMPEG_BIN", None)
    env.pop("FFPROBE_BIN", None)
    env.pop("PYTHON_BIN", None)
    env.update(overrides)
    return env


def _write_fake_ffprobe(path: Path, *, sample_rate: str = "44100", channels: int = 2, format_name: str = "wav") -> Path:
    payload = {
        "streams": [
            {
                "codec_name": "pcm_s16le",
                "sample_rate": sample_rate,
                "channels": channels,
                "duration": "12.345",
            }
        ],
        "format": {"format_name": format_name, "duration": "12.345"},
    }
    path.write_text(
        "#!/usr/bin/env bash\n"
        f"cat <<'JSON'\n{json.dumps(payload)}\nJSON\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _write_fake_ffmpeg(path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'ffmpeg stdout noise\\n'\n"
        "printf 'ffmpeg stderr noise\\n' >&2\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_FFMPEG_ARGS\"\n"
        "out=\"${@: -1}\"\n"
        "mkdir -p \"$(dirname \"$out\")\"\n"
        "printf 'fake wav\\n' > \"$out\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _write_failing_ffmpeg(path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'boom stdout\\n'\n"
        "printf 'boom stderr\\n' >&2\n"
        "exit 7\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_media_audio_probe_json_uses_ffprobe_output(tmp_path):
    input_file = tmp_path / "input.wav"
    input_file.write_text("audio\n", encoding="utf-8")
    ffprobe = _write_fake_ffprobe(tmp_path / "ffprobe")

    result = subprocess.run(
        ["./scripts/media.sh", "audio", "probe", str(input_file), "--json"],
        cwd=ROOT_DIR,
        env=_env(FFPROBE_BIN=str(ffprobe)),
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["file"] == str(input_file)
    assert payload["codec_name"] == "pcm_s16le"
    assert payload["sample_rate"] == 44100
    assert payload["channels"] == 2
    assert payload["format_name"] == "wav"
    assert result.stderr == ""


def test_media_audio_verify_htdemucs_input_passes_for_wav_44100_stereo(tmp_path):
    input_file = tmp_path / "input.wav"
    input_file.write_text("audio\n", encoding="utf-8")
    ffprobe = _write_fake_ffprobe(tmp_path / "ffprobe")

    result = subprocess.run(
        ["./scripts/media.sh", "audio", "verify", "htdemucs-input", str(input_file)],
        cwd=ROOT_DIR,
        env=_env(FFPROBE_BIN=str(ffprobe)),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "HTDemucs Input" in result.stdout
    assert "sample_rate" in result.stdout
    assert "channels" in result.stdout
    assert result.stderr == ""


def test_media_audio_verify_returns_4_for_wrong_sample_rate(tmp_path):
    input_file = tmp_path / "input.wav"
    input_file.write_text("audio\n", encoding="utf-8")
    ffprobe = _write_fake_ffprobe(tmp_path / "ffprobe", sample_rate="48000")

    result = subprocess.run(
        ["./scripts/media.sh", "audio", "verify", "htdemucs-input", str(input_file)],
        cwd=ROOT_DIR,
        env=_env(FFPROBE_BIN=str(ffprobe)),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "FAIL" in result.stdout
    assert "audio does not satisfy htdemucs-input" in result.stderr


def test_media_audio_probe_requires_ffprobe(tmp_path):
    input_file = tmp_path / "input.wav"
    input_file.write_text("audio\n", encoding="utf-8")

    result = subprocess.run(
        ["./scripts/media.sh", "audio", "probe", str(input_file)],
        cwd=ROOT_DIR,
        env=_env(FFPROBE_BIN=str(tmp_path / "missing-ffprobe")),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "FFPROBE_BIN is not executable" in result.stderr


def test_media_audio_prepare_htdemucs_input_invokes_ffmpeg_and_verifies_output(tmp_path):
    input_file = tmp_path / "input.mp3"
    input_file.write_text("audio\n", encoding="utf-8")
    output_file = tmp_path / "prepared" / "input.wav"
    ffprobe = _write_fake_ffprobe(tmp_path / "ffprobe")
    ffmpeg = _write_fake_ffmpeg(tmp_path / "ffmpeg")
    captured = tmp_path / "ffmpeg.args"

    result = subprocess.run(
        [
            "./scripts/media.sh",
            "audio",
            "prepare",
            "htdemucs-input",
            str(input_file),
            "--output",
            str(output_file),
            "--json",
        ],
        cwd=ROOT_DIR,
        env=_env(FFPROBE_BIN=str(ffprobe), FFMPEG_BIN=str(ffmpeg), FAKE_FFMPEG_ARGS=str(captured)),
        capture_output=True,
        text=True,
        check=True,
    )

    args = captured.read_text(encoding="utf-8").splitlines()
    assert "-i" in args
    assert str(input_file) in args
    assert "-ar" in args
    assert "44100" in args
    assert "-ac" in args
    assert "2" in args
    assert "-c:a" in args
    assert "pcm_s16le" in args
    assert str(output_file) in args
    assert output_file.exists()

    payload = json.loads(result.stdout)
    assert payload["target"] == "htdemucs-input"
    assert payload["output"] == str(output_file)
    assert payload["valid"] is True
    assert result.stderr == ""


def test_media_audio_prepare_refuses_existing_output_without_force(tmp_path):
    input_file = tmp_path / "input.mp3"
    input_file.write_text("audio\n", encoding="utf-8")
    output_file = tmp_path / "input.wav"
    output_file.write_text("existing\n", encoding="utf-8")
    ffprobe = _write_fake_ffprobe(tmp_path / "ffprobe")
    ffmpeg = _write_fake_ffmpeg(tmp_path / "ffmpeg")

    result = subprocess.run(
        [
            "./scripts/media.sh",
            "audio",
            "prepare",
            "htdemucs-input",
            str(input_file),
            "--output",
            str(output_file),
        ],
        cwd=ROOT_DIR,
        env=_env(FFPROBE_BIN=str(ffprobe), FFMPEG_BIN=str(ffmpeg), FAKE_FFMPEG_ARGS=str(tmp_path / "args")),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "output already exists" in result.stderr


def test_media_audio_prepare_returns_4_when_ffmpeg_fails(tmp_path):
    input_file = tmp_path / "input.mp3"
    input_file.write_text("audio\n", encoding="utf-8")
    output_file = tmp_path / "input.wav"
    ffprobe = _write_fake_ffprobe(tmp_path / "ffprobe")
    ffmpeg = _write_failing_ffmpeg(tmp_path / "ffmpeg")

    result = subprocess.run(
        [
            "./scripts/media.sh",
            "audio",
            "prepare",
            "htdemucs-input",
            str(input_file),
            "--output",
            str(output_file),
        ],
        cwd=ROOT_DIR,
        env=_env(FFPROBE_BIN=str(ffprobe), FFMPEG_BIN=str(ffmpeg)),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert result.stdout == ""
    assert "ffmpeg failed with exit code 7" in result.stderr
    assert "boom stderr" in result.stderr
