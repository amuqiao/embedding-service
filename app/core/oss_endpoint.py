def normalize_oss_endpoint(value: str) -> str:
    return value.strip().removeprefix("https://").removeprefix("http://").strip("/").lower()
