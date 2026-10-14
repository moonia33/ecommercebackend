from __future__ import annotations

from django.core.exceptions import ValidationError

from api.i18n import get_supported_language_codes, validate_supported_language_code


def _normalize_and_validate_translation_language_code(value: str | None) -> str:
    try:
        return validate_supported_language_code(value)
    except ValueError:
        code = (value or "").strip().lower()
        allowed = get_supported_language_codes()
        raise ValidationError(
            {"language_code": f"Neleistinas language_code '{code}'. Leidziami: {', '.join(sorted(allowed))}"}
        )
