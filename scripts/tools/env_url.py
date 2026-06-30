from __future__ import annotations

import argparse
import sys
from urllib.parse import quote, unquote, urlsplit


def _read_password(args: argparse.Namespace, *, required: bool) -> str | None:
    has_password = args.password is not None
    if has_password and args.password_stdin:
        raise ValueError("--password cannot be combined with --password-stdin")
    if args.password_stdin:
        password = sys.stdin.read().rstrip("\r\n")
    elif has_password:
        password = args.password
    else:
        if required:
            raise ValueError("password is required; use --password-stdin or --password")
        return None
    if not password:
        raise ValueError("password must not be empty")
    return password


def _encoded(value: str) -> str:
    return quote(value, safe="")


def _validate_host(value: str) -> str:
    if not value:
        raise ValueError("host is required")
    if any(char in value for char in "/?#@"):
        raise ValueError("host must not contain / ? # or @")
    return value


def _validate_port(value: int) -> int:
    if value < 1 or value > 65535:
        raise ValueError("port must be between 1 and 65535")
    return value


def _comment_value(value: str | None) -> str:
    return value if value else "-"


def _print_parsed_url(env_name: str, url: str, *, database_label: str) -> None:
    parsed = urlsplit(url)
    has_password = bool(parsed.password)
    print(f"# Parsed {env_name}:")
    print(f"# {env_name}_scheme={_comment_value(parsed.scheme)}")
    print(f"# {env_name}_username_encoded={_comment_value(parsed.username)}")
    print(f"# {env_name}_username_decoded={_comment_value(unquote(parsed.username or ''))}")
    print(f"# {env_name}_password_present={'true' if has_password else 'false'}")
    print(f"# {env_name}_host={_comment_value(parsed.hostname)}")
    print(f"# {env_name}_port={parsed.port if parsed.port is not None else '-'}")
    path_value = parsed.path.lstrip("/")
    print(f"# {env_name}_{database_label}_encoded={_comment_value(path_value)}")
    print(f"# {env_name}_{database_label}_decoded={_comment_value(unquote(path_value))}")
    print("# URL encode rule: encode username/password/path component; do not encode host/port.")


def _build_postgres_url(args: argparse.Namespace) -> str:
    host = _validate_host(args.host)
    port = _validate_port(args.port)
    password = _read_password(args, required=True)
    assert password is not None
    if not args.username:
        raise ValueError("--username is required")
    if not args.database:
        raise ValueError("--database is required")
    return (
        "postgresql+asyncpg://"
        f"{_encoded(args.username)}:{_encoded(password)}@{host}:{port}/{_encoded(args.database)}"
    )


def _build_redis_url(args: argparse.Namespace) -> str:
    host = _validate_host(args.host)
    port = _validate_port(args.port)
    password = _read_password(args, required=False)
    if args.db < 0:
        raise ValueError("--db must be greater than or equal to 0")

    if args.username and password is None:
        raise ValueError("--username requires --password-stdin or --password")
    if args.username:
        encoded_password = _encoded(password or "")
        auth = f"{_encoded(args.username)}:{encoded_password}@"
    elif password is not None:
        encoded_password = _encoded(password)
        auth = f":{encoded_password}@"
    else:
        auth = ""
    return f"redis://{auth}{host}:{port}/{args.db}"


def _add_password_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--password", help="Raw password. Prefer --password-stdin to avoid shell history.")
    parser.add_argument("--password-stdin", action="store_true", help="Read the raw password from stdin.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render project .env database or Redis URL lines with fixed URL encoding rules.",
    )
    subparsers = parser.add_subparsers(dest="target", required=True)

    postgres = subparsers.add_parser("postgres", help="Render DATABASE_URL for PostgreSQL.")
    postgres.add_argument("--username", "--user", dest="username", required=True, help="Raw PostgreSQL username.")
    _add_password_options(postgres)
    postgres.add_argument("--host", required=True, help="PostgreSQL host. Host is not URL-encoded.")
    postgres.add_argument("--port", type=int, default=5432, help="PostgreSQL port; defaults to 5432.")
    postgres.add_argument("--database", "--db", dest="database", required=True, help="Raw PostgreSQL database name.")

    redis = subparsers.add_parser("redis", help="Render REDIS_URL for Redis.")
    redis.add_argument("--username", "--user", dest="username", help="Raw Redis ACL username, when used.")
    _add_password_options(redis)
    redis.add_argument("--host", required=True, help="Redis host. Host is not URL-encoded.")
    redis.add_argument("--port", type=int, default=6379, help="Redis port; defaults to 6379.")
    redis.add_argument("--db", type=int, default=0, help="Redis database number; defaults to 0.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.target == "postgres":
        url = _build_postgres_url(args)
        print(f"DATABASE_URL={url}")
        _print_parsed_url("DATABASE_URL", url, database_label="database")
        return 0
    if args.target == "redis":
        url = _build_redis_url(args)
        print(f"REDIS_URL={url}")
        _print_parsed_url("REDIS_URL", url, database_label="db")
        return 0
    raise RuntimeError(f"unsupported env-url target: {args.target}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
