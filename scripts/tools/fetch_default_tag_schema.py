from __future__ import annotations

import argparse
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://test-v-adm-api.stardustworld.cn/"
DEFAULT_ENDPOINT = "api/v1/tag-schemas/default"
DEFAULT_LANG = "en"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and print the default tag schema from the test RS API.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--lang", default=DEFAULT_LANG)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def build_url(base_url: str, endpoint: str, lang: str) -> str:
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    return f"{url}?{urlencode({'lang': lang})}"


def main() -> int:
    args = parse_args()
    url = build_url(args.base_url, args.endpoint, args.lang)
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=args.timeout) as response:
            sys.stdout.write(response.read().decode("utf-8"))
            sys.stdout.write("\n")
    except HTTPError as exc:
        sys.stderr.write(f"GET {url} failed with HTTP {exc.code}: {exc.reason}\n")
        body = exc.read().decode("utf-8", errors="replace")
        if body:
            sys.stderr.write(body)
            if not body.endswith("\n"):
                sys.stderr.write("\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
