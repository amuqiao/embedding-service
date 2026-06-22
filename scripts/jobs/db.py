from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg2
from psycopg2.extensions import connection
from psycopg2.extras import RealDictCursor


ROOT_DIR = Path(__file__).resolve().parents[2]


def _env_file_value(key: str, env_file: Path | None = None) -> str | None:
    path = env_file or ROOT_DIR / ".env"
    if not path.is_file():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        return value.strip().strip('"').strip("'")
    return None


def env_value(key: str) -> str | None:
    value = os.getenv(key)
    if value is not None:
        return value
    return _env_file_value(key)


def normalize_database_url(database_url: str, *, db_ssl: str | None) -> str:
    if not database_url:
        raise ValueError("DATABASE_URL is required")
    normalized = database_url
    for scheme in ("postgresql+asyncpg://", "postgresql+psycopg2://"):
        if normalized.startswith(scheme):
            normalized = normalized.replace(scheme, "postgresql://", 1)
            break
    if db_ssl is None or db_ssl.strip().lower() not in {"0", "false", "no", "off"}:
        return normalized

    parts = urlsplit(normalized)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("sslmode", "disable")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def connect_readonly(database_url: str | None = None, *, statement_timeout_ms: int = 5000) -> connection:
    raw_url = database_url or env_value("DATABASE_URL")
    if raw_url is None:
        raise ValueError("DATABASE_URL is required")
    conn = psycopg2.connect(
        normalize_database_url(raw_url, db_ssl=env_value("DB_SSL")),
        cursor_factory=RealDictCursor,
    )
    conn.autocommit = False
    with conn.cursor() as cursor:
        cursor.execute("BEGIN READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = %s", (f"{statement_timeout_ms}ms",))
    return conn
