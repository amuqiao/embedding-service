SUPPORTED_BUSINESS_LANGUAGES: tuple[str, ...] = (
    "zh",
    "zh-TW",
    "en",
    "es",
    "pt",
    "in",
    "th",
    "de",
    "fr",
    "hi",
    "fil",
    "tr",
    "ko",
    "ja",
    "ru",
    "ar",
    "it",
    "pl",
    "ro",
    "cs",
    "bg",
    "vi",
)


def validate_business_language(value: str) -> str:
    if value not in SUPPORTED_BUSINESS_LANGUAGES:
        raise ValueError(f"unsupported business language: {value}")
    return value
