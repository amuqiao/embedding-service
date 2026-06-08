from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.infrastructure.aliyun_oss import AliyunOSSClient, AliyunOSSConfig

DEFAULT_TEST_CONTENT = "cms-novel-localize aliyun oss connectivity check\n"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        os.environ.setdefault(name, value.strip().strip('"').strip("'"))


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"missing required env: {name}")
    return value


def load_config() -> AliyunOSSConfig:
    bucket = required_env("OSS_BUCKET")
    region = required_env("OSS_REGION")
    public_endpoint = os.getenv("OSS_PUBLIC_ENDPOINT", "").strip()
    endpoint = os.getenv("OSS_ENDPOINT", "").strip()
    endpoint_style = os.getenv("OSS_ENDPOINT_STYLE", "").strip()

    if not endpoint:
        endpoint = public_endpoint or f"oss-{region}.aliyuncs.com"
    if not endpoint_style:
        endpoint_style = "custom_domain" if public_endpoint and endpoint == public_endpoint else "virtual_host"

    return AliyunOSSConfig(
        bucket=bucket,
        region=region,
        access_key_id=required_env("OSS_ACCESS_KEY_ID"),
        access_key_secret=required_env("OSS_ACCESS_KEY_SECRET"),
        project_root=os.getenv("OSS_PROJECT_ROOT", "").strip().strip("/"),
        endpoint=endpoint.removeprefix("https://").removeprefix("http://").strip("/"),
        endpoint_style=endpoint_style,
        scheme=os.getenv("OSS_SCHEME", "https").strip() or "https",
    )


def print_step(name: str, status: str, detail: str = "") -> None:
    print(f"{name:<8} {status:<8} {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Aliyun OSS read/write/delete connectivity.")
    parser.add_argument("--env-file", type=Path, default=ROOT_DIR / ".env")
    parser.add_argument("--key", default=f"connectivity/check-{int(time.time())}.txt")
    parser.add_argument("--keep", action="store_true", help="Keep the test object after verification.")
    args = parser.parse_args()

    load_dotenv(args.env_file)
    config = load_config()
    client = AliyunOSSClient(config)
    object_key = client.object_key(args.key)
    content = DEFAULT_TEST_CONTENT.encode("utf-8")

    print_step("CONFIG", "OK", f"bucket={config.bucket} region={config.region} endpoint={config.normalized_endpoint}")
    print_step("OBJECT", "INFO", object_key)

    client.put_object(args.key, content, content_type="text/plain; charset=utf-8")
    print_step("PUT", "OK", "uploaded test object")

    body = client.get_object(args.key)
    if body != content:
        raise RuntimeError("GET body does not match uploaded content")
    print_step("GET", "OK", f"bytes={len(body)}")

    headers = client.head_object(args.key)
    print_step("HEAD", "OK", f"content-length={headers.get('Content-Length', '-')}")

    if args.keep:
        print_step("DELETE", "SKIP", "kept by --keep")
    else:
        client.delete_object(args.key)
        print_step("DELETE", "OK", "removed test object")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
