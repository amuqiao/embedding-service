import pytest

from app.core.language_catalog import (
    is_supported_language,
    language_catalog,
    list_languages_response,
    require_supported_language,
    supported_language_codes,
)


def test_language_catalog_exposes_shared_business_codes():
    languages = language_catalog()
    codes = [entry.language for entry in languages]

    assert len(codes) == 22
    assert len(codes) == len(set(codes))
    assert codes[:6] == ["zh", "zh-TW", "en", "es", "pt", "in"]
    assert "in" in codes
    assert "id" not in codes
    assert supported_language_codes() == frozenset(codes)


def test_language_catalog_response_uses_public_display_fields():
    response = list_languages_response()
    language = next(item for item in response.languages if item.language == "in")

    assert language.display_name == "Indonesian"
    assert language.native_name == "Bahasa Indonesia"
    assert response.model_dump()["languages"][0] == {
        "language": "zh",
        "display_name": "Chinese (Simplified)",
        "native_name": "中文（简体）",
    }


def test_language_catalog_validates_without_bcp47_fallback_mapping():
    assert is_supported_language("in") is True
    assert is_supported_language("id") is False
    assert require_supported_language("in").display_name == "Indonesian"

    with pytest.raises(ValueError, match="unsupported language: id"):
        require_supported_language("id")
