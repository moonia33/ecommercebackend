from __future__ import annotations

from api.i18n import get_supported_language_choices


def apply_language_code_choices(form, *, field_name: str = "language_code", exclude_default: bool = True):
    field = form.fields.get(field_name)
    if field is None:
        return
    field.choices = get_supported_language_choices(exclude_default=exclude_default)
