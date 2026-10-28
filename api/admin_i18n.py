from __future__ import annotations

from api.i18n import get_supported_language_choices, normalize_language_code


def apply_language_code_choices(form, *, field_name: str = "language_code", exclude_default: bool = True):
    field = form.fields.get(field_name)
    if field is None:
        return
    choices = get_supported_language_choices(exclude_default=exclude_default)
    # Existing rows in a language that is no longer enabled must keep their code;
    # otherwise the select silently shows the first choice and saving duplicates it.
    instance = getattr(form, "instance", None)
    current = normalize_language_code(getattr(instance, field_name, "") if instance is not None else "")
    if current and current not in {code for code, _ in choices}:
        choices = [*choices, (current, f"{current.upper()} ({current}) – neįjungta")]
    field.choices = choices
