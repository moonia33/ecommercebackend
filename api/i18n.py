from __future__ import annotations

from django.conf import settings
from django.utils import translation


def get_supported_language_codes() -> list[str]:
    raw = getattr(settings, "SUPPORTED_LANGUAGE_CODES", None)
    if raw is None:
        langs = getattr(settings, "LANGUAGES", [])
        raw = [c for c, _name in langs]

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


def get_request_language_code(
    request,
    *,
    query_param: str | None = None,
) -> str:
    query_param = query_param or getattr(settings, "LANGUAGE_QUERY_PARAM", "lang")

    try:
        kw = getattr(getattr(request, "resolver_match", None), "kwargs", {}) or {}
        path_lang = normalize_language_code(kw.get("language_code") or kw.get("lang"))
    except Exception:
        path_lang = ""

    if path_lang and path_lang in get_supported_language_codes():
        return path_lang

    if not single_language_mode() and query_param:
        try:
            qp = normalize_language_code(getattr(request, "GET", {}).get(query_param))
        except Exception:
            qp = ""
        if qp and qp in get_supported_language_codes():
            return qp

    if not single_language_mode():
        try:
            hdr = normalize_language_code(translation.get_language_from_request(request, check_path=False))
        except Exception:
            hdr = ""
        if hdr and hdr in get_supported_language_codes():
            return hdr

    try:
        site = getattr(request, "site", None)
        if site is not None:
            sc = normalize_language_code(getattr(site, "default_language_code", ""))
            if sc and sc in get_supported_language_codes():
                return sc
            cfg = getattr(site, "config", None)
            cc = normalize_language_code(getattr(cfg, "default_language_code", ""))
            if cc and cc in get_supported_language_codes():
                return cc
    except Exception:
        pass

    return get_default_language_code()


def translation_fallback_chain(language_code: str | None) -> list[str]:
    requested = normalize_language_code(language_code)
    default = get_default_language_code()
    supported = get_supported_language_codes()

    chain: list[str] = []
    if requested:
        chain.append(requested)
    if default:
        chain.append(default)
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


def normalize_country_code(country_code: str | None) -> str:
    cc = (country_code or "").strip().upper()
    if len(cc) != 2:
        return ""
    return cc


def get_default_country_code() -> str:
    cc = normalize_country_code(getattr(settings, "DEFAULT_COUNTRY_CODE", None))
    return cc or "LT"


def get_request_country_code(
    request,
    *,
    query_param: str | None = None,
) -> str:
    query_param = query_param or "country_code"

    try:
        qp = normalize_country_code(getattr(request, "GET", {}).get(query_param))
    except Exception:
        qp = ""
    if qp:
        return qp

    try:
        site = getattr(request, "site", None)
        if site is not None:
            sc = normalize_country_code(getattr(site, "default_country_code", ""))
            if sc:
                return sc
            cfg = getattr(site, "config", None)
            cc = normalize_country_code(getattr(cfg, "default_country_code", ""))
            if cc:
                return cc
    except Exception:
        pass

    return get_default_country_code()
