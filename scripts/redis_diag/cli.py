from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import unquote, urlsplit

import typer


ROOT_DIR = Path(__file__).resolve().parents[2]
STREAM_COMMANDS = ["XADD", "XREADGROUP", "XACK", "XAUTOCLAIM"]


HELP_EPILOG = """\b
作用域：
  Redis 只读排障事实源。检查 REDIS_URL、服务端版本、命令能力、内存、keyspace、Stream 和 broker key。
  其他脚本如 k8s.sh / jobs.sh 只能编排或复用本入口能力，不再各自维护 Redis 诊断逻辑。

\b
常用示例：
  ./scripts/redis.sh check
  ./scripts/redis.sh check --show-url
  ./scripts/redis.sh check --no-broker-key --show-url
  ./scripts/redis.sh check --redis-key taskiq --top-keys 20 --scan-limit 5000
  ./scripts/redis.sh broker --redis-key taskiq --json
  ./scripts/redis.sh capability
  ./scripts/redis.sh memory --json

\b
副作用与保护边界：
  只读访问 Redis，不删除 key，不修复队列，不投递消息，不自动切换 TASKIQ_BROKER_KIND。

\b
Exit Codes:
  0  成功
  2  参数非法或 REDIS_URL 缺失
  4  Redis 证据不可获取
"""

CHECK_HELP_EPILOG = """\b
常用示例：
  ./scripts/redis.sh check
  ./scripts/redis.sh check --show-url
  ./scripts/redis.sh check --no-broker-key --show-url
  ./scripts/redis.sh check --redis-key taskiq --top-keys 20 --scan-limit 5000
  ./scripts/redis.sh check --json
"""

BROKER_HELP_EPILOG = """\b
常用示例：
  ./scripts/redis.sh broker
  ./scripts/redis.sh broker --redis-key taskiq --broker-kind redis_stream
  ./scripts/redis.sh broker --json
"""

MEMORY_HELP_EPILOG = """\b
常用示例：
  ./scripts/redis.sh memory
  ./scripts/redis.sh memory --json
"""

KEYSPACE_HELP_EPILOG = """\b
常用示例：
  ./scripts/redis.sh keyspace
  ./scripts/redis.sh keyspace --json
"""

TOP_KEYS_HELP_EPILOG = """\b
常用示例：
  ./scripts/redis.sh top-keys --limit 20 --scan-limit 5000
  ./scripts/redis.sh top-keys --json
"""

CAPABILITY_HELP_EPILOG = """\b
常用示例：
  ./scripts/redis.sh capability
  ./scripts/redis.sh capability --json
"""


app = typer.Typer(
    name="redis.sh",
    help="Redis 只读排障入口。",
    epilog=HELP_EPILOG,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    context_settings={"help_option_names": ["-h", "--help"]},
)

JsonOption = Annotated[bool, typer.Option("--json", help="输出机器可读 JSON。")]
RedisUrlOption = Annotated[str | None, typer.Option("--redis-url", help="覆盖 REDIS_URL；默认读取环境变量、ENV_FILE 或 .env。")]
RedisKeyOption = Annotated[str, typer.Option("--redis-key", help="Redis broker/stream key；默认 taskiq。")]
BrokerKindOption = Annotated[str | None, typer.Option("--broker-kind", help="期望 broker kind：redis_stream 或 redis_list；默认读取 TASKIQ_BROKER_KIND。")]


def _env_file_path() -> Path:
    raw = os.getenv("ENV_FILE")
    if not raw:
        return ROOT_DIR / ".env"
    path = Path(raw)
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def _env_file_value(key: str) -> str | None:
    path = _env_file_path()
    if not path.is_file():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


def env_value(key: str) -> str | None:
    value = os.getenv(key)
    if value is not None:
        return value
    return _env_file_value(key)


def resolve_redis_url(redis_url: str | None = None) -> str:
    value = redis_url or env_value("REDIS_URL")
    if not value:
        raise ValueError("REDIS_URL is required")
    return value


def resolve_broker_kind(broker_kind: str | None = None) -> str:
    value = broker_kind or env_value("TASKIQ_BROKER_KIND") or "redis_stream"
    if value not in {"redis_stream", "redis_list"}:
        raise ValueError("TASKIQ_BROKER_KIND must be redis_stream or redis_list")
    return value


def _decode_redis(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bytes_human(value: int | None) -> str | None:
    if value is None:
        return None
    units = ["B", "K", "M", "G", "T"]
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            return f"{amount:.2f}{unit}" if unit != "B" else f"{int(amount)}B"
        amount /= 1024
    return f"{value}B"


def redis_url_detail(redis_url: str) -> dict[str, Any]:
    parsed = urlsplit(redis_url)
    try:
        port: int | None = parsed.port
        port_error = None
    except ValueError as exc:
        port = None
        port_error = str(exc)
    detail = {
        "url": redis_url,
        "scheme": parsed.scheme,
        "username_encoded": parsed.username or "-",
        "username_decoded": unquote(parsed.username or "") or "-",
        "password_encoded": parsed.password or "-",
        "password_decoded": unquote(parsed.password or "") or "-",
        "hostname": parsed.hostname or "-",
        "port": port,
        "port_error": port_error,
        "path": unquote(parsed.path) or "-",
        "query": parsed.query or "-",
        "fragment": parsed.fragment or "-",
    }
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("invalid REDIS_URL: missing scheme or host")
    if port_error:
        raise ValueError(f"invalid REDIS_URL: {port_error}")
    return detail


def redis_db_index(redis_url: str) -> int:
    parsed = urlsplit(redis_url)
    raw_path = parsed.path.lstrip("/")
    if not raw_path:
        return 0
    try:
        return int(raw_path.split("/", 1)[0])
    except ValueError:
        return 0


def _redis_oom_error_count(errorstats: dict[str, Any]) -> int | None:
    raw = errorstats.get("errorstat_OOM") or errorstats.get(b"errorstat_OOM")
    if raw is None:
        return None
    if isinstance(raw, dict):
        return _int_or_none(raw.get("count") or raw.get(b"count"))
    text = _decode_redis(raw)
    match = re.search(r"(?:^|,)count=(?P<count>[0-9]+)(?:,|$)", text)
    if not match:
        return None
    return int(match.group("count"))


def _redis_info_keyspace_rows(keyspace: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for db_name, raw_stats in keyspace.items():
        name = _decode_redis(db_name)
        if not name.startswith("db"):
            continue
        stats: dict[str, Any] = {}
        if isinstance(raw_stats, dict):
            stats = {_decode_redis(key): value for key, value in raw_stats.items()}
        else:
            for part in _decode_redis(raw_stats).split(","):
                if "=" not in part:
                    continue
                key, value = part.split("=", 1)
                stats[key] = value
        rows.append(
            {
                "db": name,
                "keys": _int_or_none(stats.get("keys")),
                "expires": _int_or_none(stats.get("expires")),
                "avg_ttl": _int_or_none(stats.get("avg_ttl")),
            }
        )
    return sorted(rows, key=lambda row: int(str(row["db"]).removeprefix("db") or 0))


def _redis_info(client: Any, section: str, errors: list[dict[str, str]]) -> dict[str, Any]:
    try:
        return client.info(section)
    except Exception as exc:
        errors.append({"area": f"info:{section}", "error": f"{type(exc).__name__}: {str(exc)[:500]}"})
        return {}


def _redis_key_length(client: Any, key: str, key_type: str) -> int | None:
    if key_type == "none":
        return 0
    if key_type == "string":
        return int(client.strlen(key))
    if key_type == "list":
        return int(client.llen(key))
    if key_type == "stream":
        return int(client.xlen(key))
    if key_type == "set":
        return int(client.scard(key))
    if key_type == "zset":
        return int(client.zcard(key))
    if key_type == "hash":
        return int(client.hlen(key))
    return None


def _redis_group_lag(group: dict[str, Any]) -> int | None:
    value = group.get("lag")
    if value in {None, "", "-"}:
        return None
    return _int_or_none(value)


def _redis_stream_total_lag(groups: list[dict[str, Any]]) -> int | None:
    lag_values = [_redis_group_lag(group) for group in groups]
    known_lags = [value for value in lag_values if value is not None]
    if not known_lags:
        return None
    return sum(known_lags)


def _stream_oldest_age_seconds(client: Any, redis_key: str) -> float | None:
    rows = client.xrange(redis_key, count=1)
    if not rows:
        return None
    raw_id = _decode_redis(rows[0][0])
    timestamp_ms = raw_id.split("-", 1)[0]
    try:
        created_at = datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=timezone.utc)
    except ValueError:
        return None
    return max((datetime.now(timezone.utc) - created_at).total_seconds(), 0.0)


def broker_payload_for_client(client: Any, *, redis_key: str, broker_kind: str | None = None) -> dict[str, Any]:
    ping_ok = bool(client.ping())
    key_type = _decode_redis(client.type(redis_key))
    kind = resolve_broker_kind(broker_kind)
    payload: dict[str, Any] = {
        "broker_kind": kind,
        "redis_key": redis_key,
        "redis_ping": "ok" if ping_ok else "failed",
        "redis_key_type": key_type,
        "length": None,
        "pending": None,
        "lag": None,
        "consumer_groups": [],
        "oldest_message_age_seconds": None,
    }
    if key_type == "list":
        payload["length"] = int(client.llen(redis_key))
    elif key_type == "stream":
        payload["length"] = int(client.xlen(redis_key))
        payload["oldest_message_age_seconds"] = _stream_oldest_age_seconds(client, redis_key)
        groups = client.xinfo_groups(redis_key)
        normalized_groups = [{_decode_redis(key): _decode_redis(value) for key, value in group.items()} for group in groups]
        payload["consumer_groups"] = normalized_groups
        payload["pending"] = sum(int(group.get("pending") or 0) for group in normalized_groups)
        payload["lag"] = _redis_stream_total_lag(normalized_groups)
    elif key_type == "none":
        payload["length"] = 0

    expected_type = {"redis_stream": "stream", "redis_list": "list"}.get(kind)
    if expected_type and key_type not in {expected_type, "none"}:
        verdict = "broker_key_type_mismatch"
    elif key_type not in {"list", "stream", "none"}:
        verdict = "broker_key_type_unsupported"
    elif key_type == "stream" and int(payload.get("pending") or 0) > 0:
        verdict = "broker_has_pending"
    elif key_type == "stream" and int(payload.get("lag") or 0) > 0:
        verdict = "broker_has_lag"
    elif key_type == "stream" and int(payload.get("length") or 0) > 0 and not payload.get("consumer_groups"):
        verdict = "stream_has_entries_no_group"
    elif key_type == "stream":
        verdict = "broker_stream_no_pending"
    elif int(payload.get("length") or 0) > 0:
        verdict = "broker_has_backlog"
    else:
        verdict = "broker_empty"
    payload["verdict"] = verdict
    return payload


def broker_payload(*, redis_url: str | None = None, redis_key: str = "taskiq", broker_kind: str | None = None) -> dict[str, Any]:
    raw_redis_url = resolve_redis_url(redis_url)
    kind = resolve_broker_kind(broker_kind)
    from redis import Redis

    client = Redis.from_url(raw_redis_url, socket_connect_timeout=5, socket_timeout=5)
    try:
        return broker_payload_for_client(client, redis_key=redis_key, broker_kind=kind)
    finally:
        if hasattr(client, "connection_pool"):
            client.connection_pool.disconnect()


def _redis_broker_key_payload(client: Any, *, redis_key: str, broker_kind: str | None, errors: list[dict[str, str]]) -> dict[str, Any]:
    try:
        broker_key = broker_payload_for_client(client, redis_key=redis_key, broker_kind=broker_kind)
    except Exception as exc:
        errors.append({"area": "broker_key", "error": f"{type(exc).__name__}: {str(exc)[:500]}"})
        broker_key = {
            "broker_kind": resolve_broker_kind(broker_kind),
            "redis_key": redis_key,
            "redis_ping": "unknown",
            "redis_key_type": "unknown",
            "length": None,
            "pending": None,
            "lag": None,
            "consumer_groups": [],
            "oldest_message_age_seconds": None,
            "verdict": "broker_evidence_unavailable",
        }
    try:
        broker_key["memory_usage_bytes"] = client.memory_usage(redis_key)
    except Exception as exc:
        errors.append({"area": "broker_key_memory_usage", "error": f"{type(exc).__name__}: {str(exc)[:500]}"})
        broker_key["memory_usage_bytes"] = None
    return broker_key


def _redis_top_keys(client: Any, *, top_keys: int, scan_limit: int, errors: list[dict[str, str]]) -> tuple[list[dict[str, Any]], int]:
    if top_keys <= 0:
        return [], 0
    rows: list[dict[str, Any]] = []
    scanned = 0
    cursor: int | str = 0
    while True:
        cursor, keys = client.scan(cursor=cursor, count=min(500, max(scan_limit - scanned, 1)))
        for raw_key in keys:
            if scanned >= scan_limit:
                break
            scanned += 1
            key = _decode_redis(raw_key)
            try:
                key_type = _decode_redis(client.type(key))
                memory_usage = client.memory_usage(key)
                ttl = int(client.ttl(key))
                length = _redis_key_length(client, key, key_type)
            except Exception as exc:
                errors.append({"area": "top_key", "error": f"{type(exc).__name__}: {str(exc)[:500]}"})
                continue
            if memory_usage is None:
                continue
            rows.append(
                {
                    "key": key,
                    "type": key_type,
                    "memory_usage_bytes": int(memory_usage),
                    "ttl_seconds": ttl,
                    "length": length,
                }
            )
        if str(cursor) == "0" or scanned >= scan_limit:
            break
    rows.sort(key=lambda row: row["memory_usage_bytes"], reverse=True)
    ranked = rows[:top_keys]
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
    return ranked, scanned


def _redis_command_capabilities(client: Any, errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for command in STREAM_COMMANDS:
        try:
            raw = client.execute_command("COMMAND", "INFO", command)
            supported = bool(raw and raw[0])
            rows.append({"command": command, "supported": supported, "error": None})
        except Exception as exc:
            errors.append({"area": f"command:{command}", "error": f"{type(exc).__name__}: {str(exc)[:500]}"})
            rows.append({"command": command, "supported": None, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    return rows


def redis_payload(
    *,
    redis_url: str | None = None,
    redis_key: str = "taskiq",
    broker_kind: str | None = None,
    top_keys: int = 0,
    scan_limit: int = 1000,
    include_url_detail: bool = False,
    include_broker_key: bool = True,
    include_capabilities: bool = True,
) -> dict[str, Any]:
    raw_redis_url = resolve_redis_url(redis_url)
    redis_url_detail(raw_redis_url)
    kind = resolve_broker_kind(broker_kind) if include_broker_key else None
    from redis import Redis

    client = Redis.from_url(raw_redis_url, socket_connect_timeout=5, socket_timeout=5)
    errors: list[dict[str, str]] = []
    try:
        ping_ok = bool(client.ping())
        memory = _redis_info(client, "memory", errors)
        server = _redis_info(client, "server", errors)
        stats = _redis_info(client, "stats", errors)
        keyspace = _redis_info(client, "keyspace", errors)
        errorstats = _redis_info(client, "errorstats", errors)
        clients = _redis_info(client, "clients", errors)
        capabilities = _redis_command_capabilities(client, errors) if include_capabilities else []

        used_memory = _int_or_none(memory.get("used_memory"))
        maxmemory = _int_or_none(memory.get("maxmemory"))
        memory_usage_percent = round(used_memory / maxmemory * 100, 2) if used_memory is not None and maxmemory else None
        oom_errors = _redis_oom_error_count(errorstats)
        if maxmemory is None:
            oom_risk = "unknown"
        elif maxmemory == 0:
            oom_risk = "unbounded"
        elif memory_usage_percent is not None and memory_usage_percent >= 100:
            oom_risk = "critical"
        elif memory_usage_percent is not None and memory_usage_percent >= 90:
            oom_risk = "warning"
        else:
            oom_risk = "ok"
        if oom_errors and oom_risk in {"ok", "unbounded", "unknown"}:
            oom_risk = "warning"

        db_index = redis_db_index(raw_redis_url)
        try:
            dbsize = int(client.dbsize())
        except Exception as exc:
            errors.append({"area": "dbsize", "error": f"{type(exc).__name__}: {str(exc)[:500]}"})
            dbsize = None

        broker_key = (
            _redis_broker_key_payload(client, redis_key=redis_key, broker_kind=kind, errors=errors)
            if include_broker_key
            else None
        )
        top_key_rows, scanned_keys = _redis_top_keys(client, top_keys=top_keys, scan_limit=scan_limit, errors=errors)

        payload = {
            "scope": {
                "redis_key": redis_key,
                "broker_kind": kind,
                "redis_db": db_index,
                "top_keys": top_keys,
                "scan_limit": scan_limit,
                "scanned_keys": scanned_keys,
                "broker_key_checked": include_broker_key,
            },
            "health": {
                "redis_ping": "ok" if ping_ok else "failed",
                "redis_version": server.get("redis_version"),
                "used_memory": used_memory,
                "used_memory_human": memory.get("used_memory_human") or _bytes_human(used_memory),
                "used_memory_peak": _int_or_none(memory.get("used_memory_peak")),
                "maxmemory": maxmemory,
                "maxmemory_human": memory.get("maxmemory_human") or _bytes_human(maxmemory),
                "memory_usage_percent": memory_usage_percent,
                "maxmemory_policy": memory.get("maxmemory_policy"),
                "connected_clients": _int_or_none(clients.get("connected_clients")),
                "evicted_keys": _int_or_none(stats.get("evicted_keys")),
                "total_error_replies": _int_or_none(stats.get("total_error_replies")),
                "oom_errors": oom_errors,
                "oom_risk": oom_risk,
            },
            "capabilities": capabilities,
            "keyspace": _redis_info_keyspace_rows(keyspace),
            "current_db": {"db": f"db{db_index}", "dbsize": dbsize},
            "broker_key": broker_key,
            "top_keys": top_key_rows,
            "errors": errors,
        }
        if include_url_detail:
            payload["redis_url"] = redis_url_detail(raw_redis_url)
        return payload
    finally:
        if hasattr(client, "connection_pool"):
            client.connection_pool.disconnect()


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))


def _section(title: str) -> None:
    print(f"\n== {title} ==")


def _event(status: str, subject: str, detail: str = "") -> None:
    print(f"{status:<9} {subject:<10} {detail}")


def _print_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]], *, empty_message: str = "no records") -> None:
    if not rows:
        print(empty_message)
        return
    widths = []
    for key, title in columns:
        width = len(title)
        for row in rows:
            width = max(width, len(str(row.get(key, ""))))
        widths.append(width)
    print("  ".join(title.ljust(widths[index]) for index, (_key, title) in enumerate(columns)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(row.get(key, "")).ljust(widths[index]) for index, (key, _title) in enumerate(columns)))


def _broker_columns() -> list[tuple[str, str]]:
    return [
        ("broker_kind", "broker_kind"),
        ("redis_key", "redis_key"),
        ("redis_ping", "ping"),
        ("redis_key_type", "key_type"),
        ("length", "length"),
        ("pending", "pending"),
        ("lag", "lag"),
        ("oldest_message_age_seconds", "oldest_age_s"),
        ("verdict", "verdict"),
    ]


def _redis_health_columns() -> list[tuple[str, str]]:
    return [
        ("redis_ping", "ping"),
        ("redis_version", "version"),
        ("used_memory_human", "used_memory"),
        ("maxmemory_human", "maxmemory"),
        ("memory_usage_percent", "usage_pct"),
        ("maxmemory_policy", "policy"),
        ("connected_clients", "clients"),
        ("evicted_keys", "evicted_keys"),
        ("oom_errors", "oom_errors"),
        ("oom_risk", "oom_risk"),
    ]


def _keyspace_columns() -> list[tuple[str, str]]:
    return [("db", "db"), ("keys", "keys"), ("expires", "expires"), ("avg_ttl", "avg_ttl")]


def _current_db_columns() -> list[tuple[str, str]]:
    return [("db", "db"), ("dbsize", "dbsize")]


def _capability_columns() -> list[tuple[str, str]]:
    return [("command", "command"), ("supported", "supported"), ("error", "error")]


def _top_key_columns() -> list[tuple[str, str]]:
    return [
        ("rank", "rank"),
        ("key", "key"),
        ("type", "type"),
        ("memory_usage_bytes", "memory_bytes"),
        ("ttl_seconds", "ttl_s"),
        ("length", "length"),
    ]


def _error_columns() -> list[tuple[str, str]]:
    return [("area", "area"), ("error", "error")]


def _url_columns() -> list[tuple[str, str]]:
    return [
        ("url", "url"),
        ("scheme", "scheme"),
        ("username_decoded", "user"),
        ("password_decoded", "password"),
        ("hostname", "host"),
        ("port", "port"),
        ("path", "path"),
        ("query", "query"),
    ]


def render_broker(payload: dict[str, Any]) -> None:
    _section("Redis Broker Key")
    _event(payload["verdict"].upper(), "broker", f"key={payload['redis_key']} kind={payload['broker_kind']}")
    _print_table([payload], _broker_columns())
    groups = payload.get("consumer_groups") or []
    if groups:
        _section("Consumer Groups")
        _print_table(groups, [("name", "name"), ("consumers", "consumers"), ("pending", "pending"), ("lag", "lag"), ("last-delivered-id", "last_delivered_id")])


def render_redis(payload: dict[str, Any], *, include_url_detail: bool = False) -> None:
    health = payload["health"]
    scope = payload["scope"]
    _section("Redis")
    _event(str(health["oom_risk"]).upper(), "redis", f"db=db{scope['redis_db']} key={scope['redis_key']} scanned_keys={scope['scanned_keys']}")
    if include_url_detail and "redis_url" in payload:
        _section("Redis URL")
        _print_table([payload["redis_url"]], _url_columns())
    _section("Health")
    _print_table([health], _redis_health_columns())
    _section("Command Capabilities")
    _print_table(payload["capabilities"], _capability_columns())
    _section("Keyspace")
    _print_table(payload["keyspace"], _keyspace_columns(), empty_message="no keyspace records")
    _section("Current DB")
    _print_table([payload["current_db"]], _current_db_columns())
    if payload.get("broker_key") is not None:
        _section("Broker Key")
        _print_table([payload["broker_key"]], _broker_columns() + [("memory_usage_bytes", "memory_bytes")])
        if payload["broker_key"].get("consumer_groups"):
            _section("Consumer Groups")
            _print_table(payload["broker_key"]["consumer_groups"], [("name", "name"), ("consumers", "consumers"), ("pending", "pending"), ("lag", "lag"), ("last-delivered-id", "last_delivered_id")])
    _section("Top Keys")
    _print_table(payload["top_keys"], _top_key_columns(), empty_message="no sampled keys")
    if payload["errors"]:
        _section("Evidence Errors")
        _print_table(payload["errors"], _error_columns())


def _run_payload(command: str, fn):
    try:
        return fn()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    except Exception as exc:
        print(f"ERROR: redis evidence unavailable: {exc}", file=sys.stderr)
        raise typer.Exit(4) from exc


def _exit_if_evidence_errors(payload: dict[str, Any]) -> None:
    errors = payload.get("errors") or []
    if not errors:
        return
    print(f"ERROR: redis evidence incomplete; errors={len(errors)}", file=sys.stderr)
    raise typer.Exit(4)


@app.command(help="聚合检查 Redis 连接、版本、能力、内存、keyspace 和 broker key。", epilog=CHECK_HELP_EPILOG)
def check(
    redis_url: RedisUrlOption = None,
    redis_key: RedisKeyOption = "taskiq",
    broker_kind: BrokerKindOption = None,
    top_keys: Annotated[int, typer.Option("--top-keys", min=0, max=100, help="展示当前 DB 内按 MEMORY USAGE 排序的采样 key 数；默认 0 表示不扫描 key。")] = 0,
    scan_limit: Annotated[int, typer.Option("--scan-limit", min=1, max=100000, help="当前 DB 内最多 SCAN 的 key 数。")] = 1000,
    broker_key_check: Annotated[bool, typer.Option("--broker-key/--no-broker-key", help="是否检查 broker/stream key；默认检查。")] = True,
    show_url: Annotated[bool, typer.Option("--show-url", help="在人读和 JSON 输出中包含完整 REDIS_URL 解析结果；会暴露密码。")] = False,
    json_output: JsonOption = False,
) -> None:
    payload = _run_payload("check", lambda: redis_payload(redis_url=redis_url, redis_key=redis_key, broker_kind=broker_kind, top_keys=top_keys, scan_limit=scan_limit, include_url_detail=show_url, include_broker_key=broker_key_check))
    if json_output:
        _print_json(payload)
        _exit_if_evidence_errors(payload)
        return
    render_redis(payload, include_url_detail=show_url)
    _exit_if_evidence_errors(payload)


@app.command(help="查看 Redis/Taskiq broker key 的只读运输层状态。", epilog=BROKER_HELP_EPILOG)
def broker(
    redis_url: RedisUrlOption = None,
    redis_key: RedisKeyOption = "taskiq",
    broker_kind: BrokerKindOption = None,
    json_output: JsonOption = False,
) -> None:
    payload = _run_payload("broker", lambda: broker_payload(redis_url=redis_url, redis_key=redis_key, broker_kind=broker_kind))
    if json_output:
        _print_json(payload)
        return
    render_broker(payload)


@app.command(help="查看 Redis 内存、maxmemory、policy、OOM 和 client 证据。", epilog=MEMORY_HELP_EPILOG)
def memory(redis_url: RedisUrlOption = None, json_output: JsonOption = False) -> None:
    payload = _run_payload("memory", lambda: redis_payload(redis_url=redis_url, include_broker_key=False, include_capabilities=False))
    memory_payload = {"health": payload["health"], "errors": payload["errors"]}
    if json_output:
        _print_json(memory_payload)
        _exit_if_evidence_errors(payload)
        return
    _section("Redis Memory")
    _print_table([payload["health"]], _redis_health_columns())
    if payload["errors"]:
        _section("Evidence Errors")
        _print_table(payload["errors"], _error_columns())
    _exit_if_evidence_errors(payload)


@app.command(help="查看 Redis keyspace 和当前 DB size。", epilog=KEYSPACE_HELP_EPILOG)
def keyspace(redis_url: RedisUrlOption = None, json_output: JsonOption = False) -> None:
    payload = _run_payload("keyspace", lambda: redis_payload(redis_url=redis_url, include_broker_key=False, include_capabilities=False))
    keyspace_payload = {"keyspace": payload["keyspace"], "current_db": payload["current_db"], "errors": payload["errors"]}
    if json_output:
        _print_json(keyspace_payload)
        _exit_if_evidence_errors(payload)
        return
    _section("Keyspace")
    _print_table(payload["keyspace"], _keyspace_columns(), empty_message="no keyspace records")
    _section("Current DB")
    _print_table([payload["current_db"]], _current_db_columns())
    if payload["errors"]:
        _section("Evidence Errors")
        _print_table(payload["errors"], _error_columns())
    _exit_if_evidence_errors(payload)


@app.command("top-keys", help="采样查看当前 Redis DB 中 MEMORY USAGE 最大的 key。", epilog=TOP_KEYS_HELP_EPILOG)
def top_keys(
    redis_url: RedisUrlOption = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100, help="展示 key 数。")] = 20,
    scan_limit: Annotated[int, typer.Option("--scan-limit", min=1, max=100000, help="当前 DB 内最多 SCAN 的 key 数。")] = 5000,
    json_output: JsonOption = False,
) -> None:
    payload = _run_payload("top-keys", lambda: redis_payload(redis_url=redis_url, top_keys=limit, scan_limit=scan_limit, include_broker_key=False, include_capabilities=False))
    top_payload = {"scope": payload["scope"], "top_keys": payload["top_keys"], "errors": payload["errors"]}
    if json_output:
        _print_json(top_payload)
        _exit_if_evidence_errors(payload)
        return
    _section("Top Keys")
    _print_table(payload["top_keys"], _top_key_columns(), empty_message="no sampled keys")
    if payload["errors"]:
        _section("Evidence Errors")
        _print_table(payload["errors"], _error_columns())
    _exit_if_evidence_errors(payload)


@app.command(help="查看 Redis 服务端版本和 Stream 相关命令能力。", epilog=CAPABILITY_HELP_EPILOG)
def capability(redis_url: RedisUrlOption = None, json_output: JsonOption = False) -> None:
    payload = _run_payload("capability", lambda: redis_payload(redis_url=redis_url, include_broker_key=False))
    capability_payload = {"health": {"redis_ping": payload["health"]["redis_ping"], "redis_version": payload["health"]["redis_version"]}, "capabilities": payload["capabilities"], "errors": payload["errors"]}
    if json_output:
        _print_json(capability_payload)
        _exit_if_evidence_errors(payload)
        return
    _section("Redis Capability")
    _print_table([capability_payload["health"]], [("redis_ping", "ping"), ("redis_version", "version")])
    _section("Command Capabilities")
    _print_table(payload["capabilities"], _capability_columns())
    if payload["errors"]:
        _section("Evidence Errors")
        _print_table(payload["errors"], _error_columns())
    _exit_if_evidence_errors(payload)


if __name__ == "__main__":
    app()
