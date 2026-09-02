from __future__ import annotations

import csv
import json
import math
import os
import statistics
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import numpy as np
import typer
import yaml

from app.tools.providers.triton_audio_stem import (
    TritonAudioStemClient,
    TritonAudioStemConfig,
    TritonAudioStemIntegrationError,
)
from scripts.jobs import formatters

ROOT_DIR = Path(__file__).resolve().parents[2]
MODEL_ASSET_PATH = ROOT_DIR / "app/business_packages/audio_stem_separation/model_asset.yaml"
DEFAULT_OUTPUT_DIR = ".run/triton-bench"
DEFAULT_CONCURRENCY = "1,2,4"
SAFE_MAX_CONCURRENCY = 4
SAFE_MAX_REQUESTS_PER_LEVEL = 100
SOURCES = ("drums", "bass", "other", "vocals")

HELP_EPILOG = """\b
作用域：
  直连 audio_stem_separation_triton 使用的 Triton HTTP endpoint，测 htdemucs_ft 四个 expert 的推理并发、延迟和错误率。

\b
不负责：
  不创建 FastAPI Job，不访问 DB/Redis/OSS，不下载输出，不触发 callback，不替代 scripts/load.sh 的业务链路压测。

\b
依赖：
  需要安装可选依赖：uv sync --extra audio-triton
  如果使用 --input-file，还需要 soundfile，已包含在 audio-triton extra 中。

\b
安全边界：
  默认只允许最大 concurrency <= 4 且每档 requests <= 100。
  更高并发或更多请求必须显式传 --confirm-aggressive。
  任一档错误率超过阈值，或 p95 相对基线超过阈值，默认停止后续阶梯。

\b
常用示例：
  ./scripts/triton-bench.sh doctor --url 127.0.0.1:8000
  ./scripts/triton-bench.sh run --url 127.0.0.1:8000 --models drums --concurrency 1,2
  ./scripts/triton-bench.sh run --url 127.0.0.1:8000 --models all --concurrency 1,2,4
  ./scripts/triton-bench.sh run --env-file .env --input-file .data/misc/input.wav --dry-run --json

\b
输出：
  .run/triton-bench/<run_id>/manifest.json
  .run/triton-bench/<run_id>/results.csv

\b
Exit Codes:
  0  成功
  2  参数、配置或依赖错误
  4  Triton ready 检查失败或压测执行失败
"""

RUN_EPILOG = """\b
常用示例：
  ./scripts/triton-bench.sh run --url 127.0.0.1:8000 --models drums --concurrency 1,2
  ./scripts/triton-bench.sh run --url 127.0.0.1:8000 --models all --concurrency 1,2,4 --requests-per-level 20
  ./scripts/triton-bench.sh run --env-file .env --input-file .data/misc/input.wav --models all
"""

app = typer.Typer(
    name="triton-bench.sh",
    help="audio stem Triton 直压入口。",
    epilog=HELP_EPILOG,
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode=None,
    context_settings={"help_option_names": ["-h", "--help"]},
)


class BenchError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class BenchConfig:
    url: str
    token: str
    model_version: str
    request_timeout_seconds: float


@dataclass(frozen=True)
class RequestResult:
    model_name: str
    latency_ms: float
    ok: bool
    error: str | None = None


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help(), err=True)
        raise typer.Exit(2)


def _resolve_repo_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def _read_env_file(env_file: str | None) -> dict[str, str]:
    path = ROOT_DIR / ".env" if env_file is None else _resolve_repo_path(env_file)
    if env_file is not None and not path.is_file():
        raise BenchError(f"env file not found: {path}", exit_code=2)
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _env_value(name: str, env_file_values: dict[str, str]) -> str | None:
    if os.environ.get(name) is not None:
        return os.environ[name]
    return env_file_values.get(name)


def _strip_url_scheme(value: str) -> str:
    stripped = value.strip().rstrip("/")
    if "://" not in stripped:
        return stripped
    parsed = urlparse(stripped)
    if not parsed.hostname:
        raise BenchError(f"Triton URL is invalid: {value}", exit_code=2)
    host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise BenchError("Triton URL must not contain path, query, fragment, or userinfo", exit_code=2)
    return host


def _ready_url(value: str) -> str:
    stripped = value.strip().rstrip("/")
    if stripped.startswith("http://") or stripped.startswith("https://"):
        return f"{stripped}/v2/health/ready"
    return f"http://{stripped}/v2/health/ready"


def _resolve_config(
    *,
    url: str | None,
    token: str | None,
    model_version: str | None,
    request_timeout_seconds: float | None,
    env_file: str | None,
) -> BenchConfig:
    env_values = _read_env_file(env_file)
    raw_url = url or _env_value("AUDIO_STEM_TRITON_URL", env_values)
    if not raw_url:
        raise BenchError("AUDIO_STEM_TRITON_URL is required; pass --url or set it in env", exit_code=2)
    raw_token = token if token is not None else (_env_value("AUDIO_STEM_TRITON_TOKEN", env_values) or "")
    raw_version = model_version or _env_value("AUDIO_STEM_TRITON_MODEL_VERSION", env_values) or "1"
    raw_timeout = request_timeout_seconds
    if raw_timeout is None:
        configured = _env_value("AUDIO_STEM_TRITON_REQUEST_TIMEOUT_SECONDS", env_values)
        raw_timeout = float(configured) if configured else 300.0
    if raw_timeout <= 0:
        raise BenchError("request timeout must be greater than 0", exit_code=2)
    if not raw_version.strip():
        raise BenchError("model version must not be empty", exit_code=2)
    return BenchConfig(
        url=_strip_url_scheme(raw_url),
        token=raw_token,
        model_version=raw_version.strip(),
        request_timeout_seconds=float(raw_timeout),
    )


def _load_asset() -> dict[str, Any]:
    try:
        asset = yaml.safe_load(MODEL_ASSET_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BenchError(f"model asset not found: {MODEL_ASSET_PATH}", exit_code=2) from exc
    if not isinstance(asset, dict):
        raise BenchError("model asset must be an object", exit_code=2)
    return asset


def _model_names_for_sources(asset: dict[str, Any], sources: tuple[str, ...]) -> list[str]:
    experts = asset.get("experts")
    if not isinstance(experts, dict):
        raise BenchError("model asset experts must be an object", exit_code=2)
    names: list[str] = []
    for source in sources:
        spec = experts.get(source)
        if not isinstance(spec, dict):
            raise BenchError(f"model asset missing source: {source}", exit_code=2)
        names.append(f"htdemucs_ft_{source}")
    return names


def _parse_sources(value: str) -> tuple[str, ...]:
    if value.strip() == "all":
        return SOURCES
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items:
        raise BenchError("--models must not be empty", exit_code=2)
    invalid = sorted(set(items) - set(SOURCES))
    if invalid:
        raise BenchError(f"--models contains invalid source(s): {', '.join(invalid)}", exit_code=2)
    return items


def _parse_concurrency(value: str) -> list[int]:
    levels: list[int] = []
    for item in value.split(","):
        stripped = item.strip()
        if not stripped:
            continue
        try:
            level = int(stripped)
        except ValueError as exc:
            raise BenchError(f"concurrency level must be an integer: {stripped}", exit_code=2) from exc
        if level < 1:
            raise BenchError("concurrency levels must be >= 1", exit_code=2)
        levels.append(level)
    if not levels:
        raise BenchError("--concurrency must contain at least one level", exit_code=2)
    if levels != sorted(levels):
        raise BenchError("--concurrency levels must be sorted ascending", exit_code=2)
    if len(levels) != len(set(levels)):
        raise BenchError("--concurrency levels must not contain duplicates", exit_code=2)
    return levels


def _make_random_input(asset: dict[str, Any], *, seed: int) -> np.ndarray:
    runtime = asset["runtime"]
    channels = int(runtime["channels"])
    segment_samples = int(runtime["segment_samples"])
    rng = np.random.default_rng(seed)
    return rng.standard_normal((1, channels, segment_samples), dtype=np.float32)


def _input_from_wav(path: str, asset: dict[str, Any]) -> np.ndarray:
    try:
        import soundfile as sf
    except ModuleNotFoundError as exc:
        raise BenchError("soundfile is required for --input-file; run: uv sync --extra audio-triton", exit_code=2) from exc
    source = _resolve_repo_path(path)
    if not source.is_file():
        raise BenchError(f"input file not found: {source}", exit_code=2)
    try:
        audio, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    except Exception as exc:
        raise BenchError(f"input file must be a readable WAV: {source}", exit_code=2) from exc
    runtime = asset["runtime"]
    expected_rate = int(runtime["sample_rate"])
    channels = int(runtime["channels"])
    segment_samples = int(runtime["segment_samples"])
    if int(sample_rate) != expected_rate:
        raise BenchError(f"input sample_rate must be {expected_rate}, got {sample_rate}", exit_code=2)
    if audio.ndim != 2 or audio.shape[1] != channels:
        actual = int(audio.shape[1]) if audio.ndim == 2 else None
        raise BenchError(f"input audio must have {channels} channels, got {actual}", exit_code=2)
    segment = audio[:segment_samples, :].T
    if segment.shape[1] < segment_samples:
        segment = np.pad(segment, ((0, 0), (0, segment_samples - segment.shape[1])), mode="constant")
    return segment[np.newaxis, ...].astype(np.float32)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    return ordered[lower] * (upper - rank) + ordered[upper] * (rank - lower)


def _infer_once(client: TritonAudioStemClient, *, model_name: str, model_input: np.ndarray, validate_output: bool) -> RequestResult:
    started = time.perf_counter()
    try:
        output = client.infer_stems(model_name=model_name, model_input=model_input)
        if validate_output:
            expected = (1, 4, model_input.shape[1], model_input.shape[2])
            if output.shape != expected:
                raise ValueError(f"unexpected output shape {output.shape}, expected {expected}")
            if output.dtype != np.float32:
                raise ValueError(f"unexpected output dtype {output.dtype}, expected float32")
        latency_ms = (time.perf_counter() - started) * 1000
        return RequestResult(model_name=model_name, latency_ms=latency_ms, ok=True)
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        return RequestResult(model_name=model_name, latency_ms=latency_ms, ok=False, error=str(exc)[:240])


def _run_level(
    *,
    config: BenchConfig,
    model_names: list[str],
    model_input: np.ndarray,
    concurrency: int,
    requests_per_level: int,
    validate_output: bool,
) -> dict[str, Any]:
    local = threading.local()

    def client_for_thread() -> TritonAudioStemClient:
        client = getattr(local, "client", None)
        if client is None:
            client = TritonAudioStemClient(
                TritonAudioStemConfig(
                    url=config.url,
                    token=config.token,
                    model_version=config.model_version,
                    request_timeout_seconds=config.request_timeout_seconds,
                )
            )
            local.client = client
        return client

    def infer_bound(*, model_name: str) -> RequestResult:
        return _infer_once(
            client_for_thread(),
            model_name=model_name,
            model_input=model_input,
            validate_output=validate_output,
        )

    started = time.perf_counter()
    results: list[RequestResult] = []
    submitted = 0

    def next_model(index: int) -> str:
        return model_names[index % len(model_names)]

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = set()
        initial = min(concurrency, requests_per_level)
        for _ in range(initial):
            futures.add(
                executor.submit(
                    infer_bound,
                    model_name=next_model(submitted),
                )
            )
            submitted += 1
        while futures:
            done, futures = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                results.append(future.result())
                if submitted < requests_per_level:
                    futures.add(
                        executor.submit(
                            infer_bound,
                            model_name=next_model(submitted),
                        )
                    )
                    submitted += 1

    elapsed_seconds = time.perf_counter() - started
    ok_results = [item for item in results if item.ok]
    latencies = [item.latency_ms for item in ok_results]
    failures = [item for item in results if not item.ok]
    model_counts: dict[str, int] = {}
    for item in results:
        model_counts[item.model_name] = model_counts.get(item.model_name, 0) + 1
    return {
        "concurrency": concurrency,
        "requests": len(results),
        "successes": len(ok_results),
        "failures": len(failures),
        "error_rate": len(failures) / len(results) if results else 0.0,
        "elapsed_seconds": elapsed_seconds,
        "throughput_rps": len(ok_results) / elapsed_seconds if elapsed_seconds > 0 else 0.0,
        "latency_ms_avg": statistics.fmean(latencies) if latencies else 0.0,
        "latency_ms_min": min(latencies) if latencies else 0.0,
        "latency_ms_p50": _percentile(latencies, 0.50),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "latency_ms_p99": _percentile(latencies, 0.99),
        "latency_ms_max": max(latencies) if latencies else 0.0,
        "model_counts": model_counts,
        "first_error": failures[0].error if failures else None,
    }


def _run_dir(run_id: str, output_dir: str) -> Path:
    base = _resolve_repo_path(output_dir)
    return base / run_id


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _write_results_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "concurrency",
        "requests",
        "successes",
        "failures",
        "error_rate",
        "throughput_rps",
        "latency_ms_avg",
        "latency_ms_p50",
        "latency_ms_p95",
        "latency_ms_p99",
        "latency_ms_max",
        "first_error",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def _print_payload(payload: dict[str, Any]) -> None:
    formatters.section("Triton Bench")
    formatters.event(payload["status"].upper(), "triton", f"run_id={payload['run_id']} url={payload['url']}")
    formatters.print_table(
        [
            {
                "models": ",".join(payload["models"]),
                "concurrency": ",".join(str(item) for item in payload["concurrency_levels"]),
                "requests": payload["requests_per_level"],
                "input": payload["input_source"],
                "guard": payload["stop_reason"] or "-",
            }
        ],
        [
            ("models", "models"),
            ("concurrency", "concurrency"),
            ("requests", "requests"),
            ("input", "input"),
            ("guard", "guard"),
        ],
    )
    if payload["results"]:
        formatters.section("Results")
        rows = [
            {
                "c": row["concurrency"],
                "ok": row["successes"],
                "fail": row["failures"],
                "err": f"{row['error_rate']:.3f}",
                "rps": f"{row['throughput_rps']:.3f}",
                "p50": f"{row['latency_ms_p50']:.1f}",
                "p95": f"{row['latency_ms_p95']:.1f}",
                "p99": f"{row['latency_ms_p99']:.1f}",
            }
            for row in payload["results"]
        ]
        formatters.print_table(
            rows,
            [
                ("c", "c"),
                ("ok", "ok"),
                ("fail", "fail"),
                ("err", "err"),
                ("rps", "rps"),
                ("p50", "p50_ms"),
                ("p95", "p95_ms"),
                ("p99", "p99_ms"),
            ],
        )
    formatters.section("Artifacts")
    formatters.print_table(
        [
            {"name": "manifest", "path": payload["paths"]["manifest"]},
            {"name": "results", "path": payload["paths"]["results_csv"]},
        ],
        [("name", "name"), ("path", "path")],
    )


def _check_ready(config: BenchConfig) -> tuple[bool, str]:
    target = _ready_url(config.url)
    request = Request(target, method="GET")
    if config.token:
        request.add_header("Authorization", config.token)
    try:
        with urlopen(request, timeout=min(config.request_timeout_seconds, 10.0)) as response:
            code = int(response.status)
    except URLError as exc:
        return False, str(exc.reason)
    except Exception as exc:
        return False, str(exc)
    return code == 200, f"HTTP {code}"


@app.command("doctor", help="检查 Triton endpoint readiness 和本地可选依赖。")
def doctor(
    url: Annotated[str | None, typer.Option("--url", help="Triton HTTP endpoint；可带或不带 http://。")] = None,
    env_file: Annotated[str | None, typer.Option("--env-file", help="显式 env 文件；默认读取 .env。")] = None,
    token: Annotated[str | None, typer.Option("--token", help="覆盖 AUDIO_STEM_TRITON_TOKEN。")] = None,
    model_version: Annotated[str | None, typer.Option("--model-version", help="覆盖 AUDIO_STEM_TRITON_MODEL_VERSION。")] = None,
    request_timeout_seconds: Annotated[
        float | None,
        typer.Option("--timeout-seconds", min=0.1, help="覆盖 AUDIO_STEM_TRITON_REQUEST_TIMEOUT_SECONDS。"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")] = False,
) -> None:
    try:
        config = _resolve_config(
            url=url,
            token=token,
            model_version=model_version,
            request_timeout_seconds=request_timeout_seconds,
            env_file=env_file,
        )
        ready, detail = _check_ready(config)
        tritonclient_available = True
        soundfile_available = True
        try:
            import tritonclient.http  # noqa: F401
        except ModuleNotFoundError:
            tritonclient_available = False
        try:
            import soundfile  # noqa: F401
        except ModuleNotFoundError:
            soundfile_available = False
        payload = {
            "ready": ready,
            "ready_detail": detail,
            "url": config.url,
            "model_version": config.model_version,
            "tritonclient_available": tritonclient_available,
            "soundfile_available": soundfile_available,
            "install_hint": "uv sync --extra audio-triton",
        }
    except BenchError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc
    if json_output:
        formatters.print_json(payload)
    else:
        formatters.section("Triton Bench Doctor")
        formatters.print_table(
            [payload],
            [
                ("ready", "ready"),
                ("ready_detail", "detail"),
                ("url", "url"),
                ("model_version", "version"),
                ("tritonclient_available", "tritonclient"),
                ("soundfile_available", "soundfile"),
            ],
        )
    if not payload["ready"]:
        raise typer.Exit(4)


@app.command("run", help="运行保守阶梯 Triton 直压。", epilog=RUN_EPILOG)
def run(
    url: Annotated[str | None, typer.Option("--url", help="Triton HTTP endpoint；可带或不带 http://。")] = None,
    env_file: Annotated[str | None, typer.Option("--env-file", help="显式 env 文件；默认读取 .env。")] = None,
    token: Annotated[str | None, typer.Option("--token", help="覆盖 AUDIO_STEM_TRITON_TOKEN。")] = None,
    model_version: Annotated[str | None, typer.Option("--model-version", help="覆盖 AUDIO_STEM_TRITON_MODEL_VERSION。")] = None,
    request_timeout_seconds: Annotated[
        float | None,
        typer.Option("--timeout-seconds", min=0.1, help="覆盖 AUDIO_STEM_TRITON_REQUEST_TIMEOUT_SECONDS。"),
    ] = None,
    models: Annotated[str, typer.Option("--models", help="逗号分隔 drums,bass,other,vocals，或 all。")] = "all",
    concurrency: Annotated[str, typer.Option("--concurrency", help="升序逗号分隔并发档位。")] = DEFAULT_CONCURRENCY,
    requests_per_level: Annotated[int, typer.Option("--requests-per-level", min=1, help="每个并发档位请求数。")] = 20,
    input_file: Annotated[str | None, typer.Option("--input-file", help="可选本地 44100Hz stereo WAV；默认使用随机 segment。")] = None,
    seed: Annotated[int, typer.Option("--seed", help="随机输入 seed。")] = 1,
    validate_output: Annotated[bool, typer.Option("--validate-output/--no-validate-output", help="校验 Triton 输出 shape/dtype。")] = True,
    max_error_rate: Annotated[float, typer.Option("--max-error-rate", min=0, max=1, help="超过后停止后续档位。")] = 0.0,
    max_p95_multiplier: Annotated[
        float,
        typer.Option("--max-p95-multiplier", min=1, help="相对首个成功档位 p95 超过后停止后续档位。"),
    ] = 3.0,
    stage_cooldown_seconds: Annotated[float, typer.Option("--stage-cooldown-seconds", min=0, help="档位之间冷却秒数。")] = 15.0,
    run_id: Annotated[str | None, typer.Option("--run-id", help="输出目录 run_id。")] = None,
    output_dir: Annotated[str, typer.Option("--output-dir", help="结果目录根路径。")] = DEFAULT_OUTPUT_DIR,
    confirm_aggressive: Annotated[bool, typer.Option("--confirm-aggressive", help="允许高并发或大请求数。")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只生成 manifest，不调用 Triton。")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")] = False,
) -> None:
    try:
        config = _resolve_config(
            url=url,
            token=token,
            model_version=model_version,
            request_timeout_seconds=request_timeout_seconds,
            env_file=env_file,
        )
        sources = _parse_sources(models)
        concurrency_levels = _parse_concurrency(concurrency)
        if (max(concurrency_levels) > SAFE_MAX_CONCURRENCY or requests_per_level > SAFE_MAX_REQUESTS_PER_LEVEL) and not confirm_aggressive:
            raise BenchError(
                "aggressive Triton bench requires --confirm-aggressive when concurrency > 4 or requests-per-level > 100",
                exit_code=2,
            )
        asset = _load_asset()
        model_names = _model_names_for_sources(asset, sources)
        model_input = _input_from_wav(input_file, asset) if input_file else _make_random_input(asset, seed=seed)
        effective_run_id = run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        run_dir = _run_dir(effective_run_id, output_dir)
        manifest_path = run_dir / "manifest.json"
        results_path = run_dir / "results.csv"
        payload: dict[str, Any] = {
            "status": "dry_run" if dry_run else "running",
            "run_id": effective_run_id,
            "url": config.url,
            "model_version": config.model_version,
            "models": model_names,
            "sources": list(sources),
            "concurrency_levels": concurrency_levels,
            "requests_per_level": requests_per_level,
            "input_source": str(_resolve_repo_path(input_file)) if input_file else "random",
            "input_shape": list(model_input.shape),
            "validate_output": validate_output,
            "max_error_rate": max_error_rate,
            "max_p95_multiplier": max_p95_multiplier,
            "stage_cooldown_seconds": stage_cooldown_seconds,
            "confirm_aggressive": confirm_aggressive,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "completed_at": None,
            "results": [],
            "stop_reason": None,
            "paths": {
                "run_dir": str(run_dir),
                "manifest": str(manifest_path),
                "results_csv": str(results_path),
            },
        }
        _write_json(manifest_path, payload)
        if not dry_run:
            baseline_p95: float | None = None
            for index, level in enumerate(concurrency_levels):
                row = _run_level(
                    config=config,
                    model_names=model_names,
                    model_input=model_input,
                    concurrency=level,
                    requests_per_level=requests_per_level,
                    validate_output=validate_output,
                )
                payload["results"].append(row)
                _write_results_csv(results_path, payload["results"])
                _write_json(manifest_path, payload)
                if row["error_rate"] > max_error_rate:
                    payload["stop_reason"] = f"error_rate {row['error_rate']:.4f} exceeded {max_error_rate:.4f}"
                    break
                if row["successes"] > 0 and baseline_p95 is None:
                    baseline_p95 = float(row["latency_ms_p95"])
                if baseline_p95 and row["latency_ms_p95"] > baseline_p95 * max_p95_multiplier:
                    payload["stop_reason"] = (
                        f"p95 {row['latency_ms_p95']:.1f}ms exceeded baseline "
                        f"{baseline_p95:.1f}ms * {max_p95_multiplier:.1f}"
                    )
                    break
                if index < len(concurrency_levels) - 1 and stage_cooldown_seconds > 0:
                    time.sleep(stage_cooldown_seconds)
            payload["status"] = "stopped" if payload["stop_reason"] else "succeeded"
        payload["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_results_csv(results_path, payload["results"])
        _write_json(manifest_path, payload)
    except BenchError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc
    except TritonAudioStemIntegrationError as exc:
        typer.echo(f"ERROR: {exc}; run: uv sync --extra audio-triton", err=True)
        raise typer.Exit(4) from exc

    if json_output:
        formatters.print_json(payload)
    else:
        _print_payload(payload)
    if payload["status"] == "stopped":
        raise typer.Exit(4)


if __name__ == "__main__":
    app(prog_name="./scripts/triton-bench.sh")
