from __future__ import annotations

from django.conf import settings
from django.utils import translation


DEFAULT_LANGUAGE_NAMES = {
    "lt": "Lithuanian",
    "pl": "Polish",
    "en": "English",
    "de": "German",
    "lv": "Latvian",
    "et": "Estonian",
}


def get_supported_languages() -> list[tuple[str, str]]:
    langs = list(getattr(settings, "LANGUAGES", []) or [])
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for code, name in langs:
        c = normalize_language_code(code)
        if not c or c in seen:
            continue
        out.append((c, name or DEFAULT_LANGUAGE_NAMES.get(c, c.upper())))
        seen.add(c)

    if not out:
        defaults = [("lt", DEFAULT_LANGUAGE_NAMES["lt"]), ("pl", DEFAULT_LANGUAGE_NAMES["pl"]), ("en", DEFAULT_LANGUAGE_NAMES["en"])]
        for c, name in defaults:
            out.append((c, name))
    return out


def get_supported_language_codes() -> list[str]:
    raw = getattr(settings, "SUPPORTED_LANGUAGE_CODES", None)
    if raw is None:
        raw = [c for c, _name in get_supported_languages()]

    out: list[str] = []
    for c in (raw or []):
        c = (c or "").strip().lower()
        if not c:
            continue
        out.append(c)

    if not out:
        default = (getattr(settings, "LANGUAGE_CODE", "") or "").split("-")[0].strip().lower()
        if default:
            out = [default]

    return out


def get_supported_language_choices(*, exclude_default: bool = False) -> list[tuple[str, str]]:
    default_code = get_default_language_code()
    out: list[tuple[str, str]] = []
    for code, name in get_supported_languages():
        if exclude_default and code == default_code:
            continue
        out.append((code, f"{name} ({code})"))
    return out


def get_default_language_code() -> str:
    default = (getattr(settings, "LANGUAGE_CODE", "") or "").split("-")[0].strip().lower()
    supported = get_supported_language_codes()
    if supported and default not in supported:
        default = supported[0]
    return default or (supported[0] if supported else "en")


def single_language_mode() -> bool:
    return len(get_supported_language_codes()) <= 1


def normalize_language_code(language_code: str | None) -> str:
    if not language_code:
        return ""
    return (language_code or "").split("-")[0].strip().lower()


def validate_supported_language_code(language_code: str | None) -> str:
    code = normalize_language_code(language_code)
    supported = get_supported_language_codes()
    if code and supported and code not in supported:
        raise ValueError(f"Unsupported language code '{code}'. Allowed: {', '.join(sorted(supported))}")
    return code


def get_shop_enabled_language_codes(shop) -> list[str]:
    if shop is None:
        return []

    supported = set(get_supported_language_codes())
    out: list[str] = []
    seen: set[str] = set()

    try:
        rows = shop.languages.filter(is_active=True).order_by("-is_default", "sort_order", "id")
    except Exception:
        rows = []

    for row in rows:
        code = normalize_language_code(getattr(row, "language_code", ""))
        if not code or code in seen or (supported and code not in supported):
            continue
        out.append(code)
        seen.add(code)

    shop_default = normalize_language_code(getattr(shop, "default_language_code", ""))
    if shop_default and shop_default not in seen and (not supported or shop_default in supported):
        out.insert(0, shop_default)
        seen.add(shop_default)

    return out


def get_request_language_code(
    request,
    *,
    query_param: str | None = None,
) -> str:
    query_param = query_param or getattr(settings, "LANGUAGE_QUERY_PARAM", "lang")
    supported = get_supported_language_codes()
    shop = getattr(request, "shop", None)
    shop_enabled = get_shop_enabled_language_codes(shop)
    allowed = shop_enabled or supported

    if not single_language_mode() and query_param:
        try:
            qp = normalize_language_code(getattr(request, "GET", {}).get(query_param))
        except Exception:
            qp = ""
        if qp and qp in allowed:
            return qp

    if not single_language_mode():
        try:
            hdr = normalize_language_code(translation.get_language_from_request(request, check_path=False))
        except Exception:
            hdr = ""
        if hdr and hdr in allowed:
            return hdr

    # Shop-level fallback (per-domain locale baseline)
    try:
        shop_lang = normalize_language_code(getattr(shop, "default_language_code", ""))
        if shop_lang and shop_lang in allowed:
            return shop_lang
    except Exception:
        pass

    return get_default_language_code()


def translation_fallback_chain(language_code: str | None, *, shop=None) -> list[str]:
    requested = normalize_language_code(language_code)
    default = get_default_language_code()
    supported = get_supported_language_codes()
    shop_default = normalize_language_code(getattr(shop, "default_language_code", "")) if shop is not None else ""
    shop_enabled = get_shop_enabled_language_codes(shop)

    chain: list[str] = []
    if requested:
        chain.append(requested)
    if shop_default:
        chain.append(shop_default)
    if default:
        chain.append(default)
    chain.extend(shop_enabled)
    chain.extend(supported)

    seen: set[str] = set()
    out: list[str] = []
    for c in chain:
        c = normalize_language_code(c)
        if not c or c in seen:
            continue
        out.append(c)
        seen.add(c)
    return out
