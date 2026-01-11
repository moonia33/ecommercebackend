from __future__ import annotations

from django.conf import settings


def translation_fallback_chain(language_code: str | None) -> list[str]:
    chain: list[str] = []
    if language_code:
        chain.append(language_code)
    default = (getattr(settings, "LANGUAGE_CODE", "") or "").split("-")[0]
    if default:
        chain.append(default)

    chain.extend(["lt", "en"])

    seen: set[str] = set()
    out: list[str] = []
    for c in chain:
        c = (c or "").strip().lower()
        if not c or c in seen:
            continue
        out.append(c)
        seen.add(c)
    return out
