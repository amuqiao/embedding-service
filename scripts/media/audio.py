from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXIT_USAGE = 2
EXIT_VERIFY_FAILED = 4
HTDEMUCS_SAMPLE_RATE = 44100
HTDEMUCS_CHANNELS = 2


@dataclass(frozen=True)
class AudioProbe:
    path: Path
    codec_name: str | None
    sample_rate: int | None
    channels: int | None
    duration_seconds: float | None
    format_name: str | None

    @property
    def is_wav(self) -> bool:
        if self.format_name is None:
            return False
        return "wav" in {part.strip().lower() for part in self.format_name.split(",")}

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": str(self.path),
            "codec_name": self.codec_name,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "duration_seconds": self.duration_seconds,
            "format_name": self.format_name,
        }


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def die(message: str, code: int = EXIT_USAGE) -> None:
    eprint(f"ERROR: {message}")
    raise SystemExit(code)


def section(title: str) -> None:
    print(f"\n== {title} ==")


def row(key: str, value: object | None) -> None:
    text = "-" if value is None else str(value)
    print(f"  {key:<14} {text}")


def event(status: str, subject: str, detail: str = "") -> None:
    print(f"{status:<9} {subject:<18} {detail}")


def resolve_tool(env_key: str, default_name: str) -> str:
    configured = os.environ.get(env_key)
    if configured:
        if Path(configured).is_absolute() or "/" in configured:
            if not os.access(configured, os.X_OK):
                die(f"{env_key} is not executable: {configured}")
            return configured
        resolved = shutil.which(configured)
        if resolved is None:
            die(f"{env_key} command is not available: {configured}")
        return resolved

    resolved = shutil.which(default_name)
    if resolved is None:
        die(f"{default_name} is not available; install ffmpeg or set {env_key}")
    return resolved


def require_input_file(path: Path) -> None:
    if not path.exists():
        die(f"input file not found: {path}")
    if not path.is_file():
        die(f"input path is not a file: {path}")


def parse_int(value: object | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def parse_float(value: object | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def run_ffprobe(path: Path, ffprobe: str | None = None) -> AudioProbe:
    require_input_file(path)
    if ffprobe is None:
        ffprobe = resolve_tool("FFPROBE_BIN", "ffprobe")
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name,sample_rate,channels,duration:format=format_name,duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "ffprobe failed"
        die(detail)

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        die(f"ffprobe returned invalid JSON: {exc}")

    streams = payload.get("streams") or []
    if not streams:
        die(f"no audio stream found: {path}")
    stream = streams[0]
    fmt = payload.get("format") or {}
    duration = parse_float(stream.get("duration"))
    if duration is None:
        duration = parse_float(fmt.get("duration"))

    return AudioProbe(
        path=path,
        codec_name=stream.get("codec_name"),
        sample_rate=parse_int(stream.get("sample_rate")),
        channels=parse_int(stream.get("channels")),
        duration_seconds=duration,
        format_name=fmt.get("format_name"),
    )


def htdemucs_checks(probe: AudioProbe, max_duration_seconds: float | None) -> list[dict[str, Any]]:
    checks = [
        {
            "name": "format",
            "expected": "wav",
            "actual": probe.format_name,
            "ok": probe.is_wav,
        },
        {
            "name": "sample_rate",
            "expected": HTDEMUCS_SAMPLE_RATE,
            "actual": probe.sample_rate,
            "ok": probe.sample_rate == HTDEMUCS_SAMPLE_RATE,
        },
        {
            "name": "channels",
            "expected": HTDEMUCS_CHANNELS,
            "actual": probe.channels,
            "ok": probe.channels == HTDEMUCS_CHANNELS,
        },
    ]
    if max_duration_seconds is not None:
        checks.append(
            {
                "name": "duration_seconds",
                "expected": f"<= {max_duration_seconds:g}",
                "actual": probe.duration_seconds,
                "ok": probe.duration_seconds is not None
                and probe.duration_seconds <= max_duration_seconds,
            }
        )
    return checks


def print_probe(probe: AudioProbe) -> None:
    section("Audio Probe")
    row("file", probe.path)
    row("codec", probe.codec_name)
    row("sample-rate", probe.sample_rate)
    row("channels", probe.channels)
    row("duration-sec", None if probe.duration_seconds is None else f"{probe.duration_seconds:.3f}")
    row("format", probe.format_name)


def run_probe(args: argparse.Namespace) -> int:
    path = Path(args.file)
    probe = run_ffprobe(path)
    if args.json:
        print(json.dumps(probe.to_dict(), ensure_ascii=False, separators=(",", ":")))
    else:
        print_probe(probe)
    return 0


def run_verify(args: argparse.Namespace) -> int:
    if args.target != "htdemucs-input":
        die(f"unknown audio verify target '{args.target}'")

    path = Path(args.file)
    probe = run_ffprobe(path)
    checks = htdemucs_checks(probe, args.max_duration_seconds)
    valid = all(check["ok"] for check in checks)

    if args.json:
        payload = {
            "target": args.target,
            "valid": valid,
            "probe": probe.to_dict(),
            "checks": checks,
        }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        section("HTDemucs Input")
        row("file", probe.path)
        for check in checks:
            status = "OK" if check["ok"] else "FAIL"
            event(status, check["name"], f"actual={check['actual']} expected={check['expected']}")

    if not valid:
        die("audio does not satisfy htdemucs-input", EXIT_VERIFY_FAILED)
    return 0


def compact_tool_output(stdout: str, stderr: str) -> str:
    combined = "\n".join(part.strip() for part in (stderr, stdout) if part.strip())
    if not combined:
        return ""
    lines = combined.splitlines()
    return "\n".join(lines[-20:])


def run_ffmpeg_prepare(input_path: Path, output_path: Path, force: bool, ffmpeg: str) -> None:
    require_input_file(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not force:
        die(f"output already exists: {output_path}; pass --force to overwrite")

    command = [
        ffmpeg,
        "-hide_banner",
        "-y" if force else "-n",
        "-i",
        str(input_path),
        "-vn",
        "-ar",
        str(HTDEMUCS_SAMPLE_RATE),
        "-ac",
        str(HTDEMUCS_CHANNELS),
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = compact_tool_output(result.stdout, result.stderr)
        suffix = f": {detail}" if detail else ""
        die(f"ffmpeg failed with exit code {result.returncode}{suffix}", EXIT_VERIFY_FAILED)


def run_prepare(args: argparse.Namespace) -> int:
    if args.target != "htdemucs-input":
        die(f"unknown audio prepare target '{args.target}'")

    input_path = Path(args.input)
    output_path = Path(args.output)
    require_input_file(input_path)
    ffprobe = resolve_tool("FFPROBE_BIN", "ffprobe")
    ffmpeg = resolve_tool("FFMPEG_BIN", "ffmpeg")
    run_ffmpeg_prepare(input_path, output_path, args.force, ffmpeg)

    probe = run_ffprobe(output_path, ffprobe)
    checks = htdemucs_checks(probe, args.max_duration_seconds)
    valid = all(check["ok"] for check in checks)

    if args.json:
        payload = {
            "target": args.target,
            "input": str(input_path),
            "output": str(output_path),
            "valid": valid,
            "probe": probe.to_dict(),
            "checks": checks,
        }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        section("Audio Prepare")
        row("target", args.target)
        row("input", input_path)
        row("output", output_path)
        event("OK" if valid else "FAIL", "prepared", "44100 Hz stereo WAV")
        section("HTDemucs Input")
        for check in checks:
            status = "OK" if check["ok"] else "FAIL"
            event(status, check["name"], f"actual={check['actual']} expected={check['expected']}")

    if not valid:
        die("prepared audio does not satisfy htdemucs-input", EXIT_VERIFY_FAILED)
    return 0


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./scripts/media.sh audio",
        description=(
            "音频素材探测、业务输入校验和本地转换准备。"
            "probe/verify 不写文件；prepare 写入 --output 指定文件。"
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    probe = subparsers.add_parser("probe", help="查看音频第一条 audio stream 的关键元数据。")
    probe.add_argument("file", help="待探测的本地音频文件。")
    probe.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    probe.set_defaults(func=run_probe)

    verify = subparsers.add_parser("verify", help="按目标业务输入规格校验音频文件。")
    verify.add_argument("target", choices=["htdemucs-input"], help="校验目标规格。")
    verify.add_argument("file", help="待校验的本地音频文件。")
    verify.add_argument(
        "--max-duration-seconds",
        type=positive_float,
        help="可选时长上限；超限返回 4。",
    )
    verify.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    verify.set_defaults(func=run_verify)

    prepare = subparsers.add_parser("prepare", help="把输入媒体转换为目标业务输入规格。")
    prepare.add_argument("target", choices=["htdemucs-input"], help="准备目标规格。")
    prepare.add_argument("input", help="本地输入文件，可由 ffmpeg 读取。")
    prepare.add_argument("--output", required=True, help="输出 WAV 路径，例如 .data/audio/input.wav。")
    prepare.add_argument("--force", action="store_true", help="允许覆盖已存在的输出文件。")
    prepare.add_argument(
        "--max-duration-seconds",
        type=positive_float,
        help="转换后执行可选时长上限校验；超限返回 4。",
    )
    prepare.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    prepare.set_defaults(func=run_prepare)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_usage(sys.stderr)
        return EXIT_USAGE
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
