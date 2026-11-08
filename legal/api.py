from __future__ import annotations

from django.db import transaction
from ninja import Router
from ninja.errors import HttpError

from accounts.auth import JWTAuth
from api.i18n import get_request_language_code
from api.shop_context import get_request_shop, shop_context_enabled, shop_context_strict

from . import services
from .models import LegalAcceptance
from .schemas import (
    LegalAcceptIn,
    LegalAcceptOut,
    LegalDocumentListOut,
    LegalPendingOut,
)

router = Router(tags=["legal"])
_auth = JWTAuth()


def _shop(request):
    shop = get_request_shop(request)
    if shop_context_enabled() and shop_context_strict() and shop is None:
        raise HttpError(404, "Shop not resolved for host")
    return shop


def _pending_out(items) -> list[dict]:
    return [i.__dict__ for i in items]


@router.get("/documents", response=list[LegalDocumentListOut])
def legal_documents(request, language_code: str | None = None):
    shop = _shop(request)
    lang = language_code or get_request_language_code(request)
    out = []
    for doc in services.documents_for_shop(shop).values():
        ver = services.current_version(doc)
        if ver is None:
            continue
        t = services.pick_translation(ver, lang, shop=shop)
        up = services.upcoming_version(doc)
        out.append({
            "kind": doc.kind,
            "slug": doc.slug,
            "url": services.document_url(doc),
            "title": (t.title if t else "") or doc.get_kind_display(),
            "version": ver.version,
            "effective_from": ver.effective_from,
            "required_at_checkout": doc.required_at_checkout,
            "upcoming": (
                {"version": up.version, "effective_from": up.effective_from} if up else None),
        })
    return out


@router.get("/pending", response=list[LegalPendingOut], auth=_auth)
def legal_pending(request, language_code: str | None = None):
    shop = _shop(request)
    lang = language_code or get_request_language_code(request)
    return _pending_out(services.pending_for_user(request.auth, shop, lang))


@router.post("/accept", response=LegalAcceptOut, auth=_auth)
def legal_accept(request, payload: LegalAcceptIn):
    user = request.auth
    shop = _shop(request)
    lang = get_request_language_code(request)
    docs = services.documents_for_shop(shop)
    to_record = []
    for item in payload.items or []:
        doc = docs.get((item.kind or "").strip().lower())
        if doc is None:
            raise HttpError(400, f"Unknown document: {item.kind}")
        ver = services.current_version(doc)
        if ver is None or ver.version != (item.version or "").strip():
            raise HttpError(409, "Document version is outdated; reload it")
        to_record.append(ver)
    with transaction.atomic():
        for ver in to_record:
            services.record_acceptance(
                user=user, version=ver, request=request, language_code=lang,
                source=LegalAcceptance.Source.RECONSENT)
    return {
        "accepted": len(to_record),
        "pending": _pending_out(services.pending_for_user(user, shop, lang)),
    }
