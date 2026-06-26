from __future__ import annotations

from dataclasses import dataclass

from app.schemas.meta import LanguageOut, LanguagesResponse


@dataclass(frozen=True)
class LanguageCatalogEntry:
    language: str
    display_name: str
    native_name: str


LANGUAGE_CATALOG: tuple[LanguageCatalogEntry, ...] = (
    LanguageCatalogEntry("zh", "Chinese (Simplified)", "中文（简体）"),
    LanguageCatalogEntry("zh-TW", "Chinese (Traditional)", "繁體中文"),
    LanguageCatalogEntry("en", "English", "English"),
    LanguageCatalogEntry("es", "Spanish", "Español"),
    LanguageCatalogEntry("pt", "Portuguese", "Português"),
    LanguageCatalogEntry("in", "Indonesian", "Bahasa Indonesia"),
    LanguageCatalogEntry("th", "Thai", "ไทย"),
    LanguageCatalogEntry("de", "German", "Deutsch"),
    LanguageCatalogEntry("fr", "French", "Français"),
    LanguageCatalogEntry("hi", "Hindi", "हिन्दी"),
    LanguageCatalogEntry("fil", "Filipino", "Filipino"),
    LanguageCatalogEntry("tr", "Turkish", "Türkçe"),
    LanguageCatalogEntry("ko", "Korean", "한국어"),
    LanguageCatalogEntry("ja", "Japanese", "日本語"),
    LanguageCatalogEntry("ru", "Russian", "Русский"),
    LanguageCatalogEntry("ar", "Arabic", "العربية"),
    LanguageCatalogEntry("it", "Italian", "Italiano"),
    LanguageCatalogEntry("pl", "Polish", "Polski"),
    LanguageCatalogEntry("ro", "Romanian", "Română"),
    LanguageCatalogEntry("cs", "Czech", "Čeština"),
    LanguageCatalogEntry("bg", "Bulgarian", "Български"),
    LanguageCatalogEntry("vi", "Vietnamese", "Tiếng Việt"),
)

_LANGUAGE_BY_CODE = {entry.language: entry for entry in LANGUAGE_CATALOG}


def language_catalog() -> tuple[LanguageCatalogEntry, ...]:
    return LANGUAGE_CATALOG


def supported_language_codes() -> frozenset[str]:
    return frozenset(_LANGUAGE_BY_CODE)


def is_supported_language(language: str) -> bool:
    return language in _LANGUAGE_BY_CODE


def require_supported_language(language: str) -> LanguageCatalogEntry:
    try:
        return _LANGUAGE_BY_CODE[language]
    except KeyError as exc:
        raise ValueError(f"unsupported language: {language}") from exc


def list_languages_response() -> LanguagesResponse:
    return LanguagesResponse(
        languages=[
            LanguageOut(
                language=entry.language,
                display_name=entry.display_name,
                native_name=entry.native_name,
            )
            for entry in LANGUAGE_CATALOG
        ]
    )
