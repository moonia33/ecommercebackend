from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from api.client_ip import client_ip  # noqa: F401  (re-export)
from api.i18n import translation_fallback_chain

from .models import (
    LegalAcceptance,
    LegalDocument,
    LegalDocumentVersion,
    LegalDocumentVersionTranslation,
)


def documents_for_shop(shop) -> dict[str, LegalDocument]:
    """Active documents by kind; a shop's own document overrides the global one."""
    qs = LegalDocument.objects.filter(is_active=True)
    if shop is not None:
        qs = qs.filter(Q(shop_id=shop.id) | Q(shop__isnull=True))
    else:
        qs = qs.filter(shop__isnull=True)
    out: dict[str, LegalDocument] = {}
    for doc in qs.order_by("sort_order", "id"):
        if doc.kind not in out or doc.shop_id is not None:
            out[doc.kind] = doc
    return out


def document_by_slug(shop, slug: str) -> LegalDocument | None:
    docs = documents_for_shop(shop)
    for doc in docs.values():
        if doc.slug == slug:
            return doc
    # A shop override may use its own slug; keep the global URL working.
    glob = LegalDocument.objects.filter(
        is_active=True, shop__isnull=True, slug=slug).first()
    return docs.get(glob.kind) if glob else None


def _published(doc_ids):
    return LegalDocumentVersion.objects.filter(
        document_id__in=list(doc_ids), status=LegalDocumentVersion.Status.PUBLISHED)


def current_version(doc: LegalDocument, *, now=None) -> LegalDocumentVersion | None:
    now = now or timezone.now()
    return _published([doc.id]).filter(effective_from__lte=now).order_by(
        "-effective_from", "-id").first()


def upcoming_version(doc: LegalDocument, *, now=None) -> LegalDocumentVersion | None:
    now = now or timezone.now()
    return _published([doc.id]).filter(effective_from__gt=now).order_by(
        "effective_from", "id").first()


def history(doc: LegalDocument, *, now=None) -> list[LegalDocumentVersion]:
    now = now or timezone.now()
    return list(_published([doc.id]).filter(effective_from__lte=now).order_by(
        "-effective_from", "-id"))


def pick_translation(
    version: LegalDocumentVersion, language_code: str | None, *, shop=None,
) -> LegalDocumentVersionTranslation | None:
    langs = translation_fallback_chain(language_code, shop=shop)
    rows = {t.language_code.lower(): t for t in version.translations.all()}
    for lang in langs:
        if lang in rows:
            return rows[lang]
    return next(iter(rows.values()), None)


def document_url(doc: LegalDocument) -> str:
    return f"/info/{doc.slug}"


def _title(doc, version, language_code, shop) -> str:
    t = pick_translation(version, language_code, shop=shop)
    return (t.title if t else "") or doc.get_kind_display()


# ---------- checkout ----------

def checkout_definitions(shop, language_code: str | None) -> list[dict] | None:
    """Consents a buyer must give at checkout, or None when no legal
    documents are configured (caller falls back to settings)."""
    docs = [d for d in documents_for_shop(shop).values() if d.required_at_checkout]
    if not docs:
        return None
    out = []
    for doc in docs:
        ver = current_version(doc)
        if ver is None:
            continue
        out.append({
            "kind": doc.kind,
            "name": _title(doc, ver, language_code, shop),
            "document_version": ver.version,
            "required": True,
            "url": document_url(doc),
            "version_obj": ver,
        })
    if not out:
        return None
    # Terms/privacy are always mandatory: keep the settings-based consent
    # for a kind that has no published legal document yet.
    have = {d["kind"] for d in out}
    for legacy in _legacy_definitions():
        if legacy["kind"] not in have:
            out.append(legacy)
    return out


def _legacy_definitions() -> list[dict]:
    return [
        {"kind": "terms", "name": "Pirkimo sąlygos",
         "document_version": getattr(settings, "CHECKOUT_TERMS_VERSION", "v1"),
         "required": True, "url": getattr(settings, "CHECKOUT_TERMS_URL", "/terms"),
         "version_obj": None},
        {"kind": "privacy", "name": "Privatumo politika",
         "document_version": getattr(settings, "CHECKOUT_PRIVACY_VERSION", "v1"),
         "required": True, "url": getattr(settings, "CHECKOUT_PRIVACY_URL", "/privacy"),
         "version_obj": None},
    ]


class ConsentError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def validate_checkout_consents(
    shop, language_code: str | None, submitted: dict[str, str],
) -> dict[str, LegalDocumentVersion] | None:
    """Returns kind → accepted version; None = legacy (settings) mode."""
    defs = checkout_definitions(shop, language_code)
    if defs is None:
        return None
    missing = [d["kind"] for d in defs if d["kind"] not in submitted]
    if missing:
        raise ConsentError(400, f"Missing required consents: {', '.join(missing)}")
    stale = [d["kind"] for d in defs if submitted[d["kind"]] != d["document_version"]]
    if stale:
        raise ConsentError(409, "Consent versions are outdated; refresh /checkout/consents")
    return {d["kind"]: d["version_obj"] for d in defs}


# ---------- re-consent ----------

@dataclass
class PendingItem:
    kind: str
    slug: str
    url: str
    title: str
    version: str
    effective_from: object
    change_summary: str


def pending_for_user(user, shop, language_code: str | None) -> list[PendingItem]:
    """Documents whose wording changed materially (`requires_reconsent`)
    after the user last accepted them. Users who never accepted a document
    are not asked — they accept it at their next checkout."""
    if user is None or not getattr(user, "is_authenticated", False):
        return []
    docs = documents_for_shop(shop)
    if not docs:
        return []
    now = timezone.now()
    by_doc: dict[int, list[LegalDocumentVersion]] = {}
    for v in _published(d.id for d in docs.values()).filter(
            effective_from__lte=now).order_by("-effective_from", "-id"):
        by_doc.setdefault(v.document_id, []).append(v)
    if not by_doc:
        return []

    accepted_eff: dict[int, object] = {}
    for doc_id, eff in LegalAcceptance.objects.filter(
            user=user, version__document_id__in=list(by_doc)).values_list(
            "version__document_id", "version__effective_from"):
        if doc_id not in accepted_eff or eff > accepted_eff[doc_id]:
            accepted_eff[doc_id] = eff

    out: list[PendingItem] = []
    for doc in docs.values():
        versions = by_doc.get(doc.id) or []
        if not versions or doc.id not in accepted_eff:
            continue
        current = versions[0]
        trigger = next((v for v in versions if v.requires_reconsent), None)
        if trigger is None or accepted_eff[doc.id] >= trigger.effective_from:
            continue
        t = pick_translation(current, language_code, shop=shop)
        out.append(PendingItem(
            kind=doc.kind, slug=doc.slug, url=document_url(doc),
            title=(t.title if t else "") or doc.get_kind_display(),
            version=current.version, effective_from=current.effective_from,
            change_summary=(t.change_summary if t else "") or "",
        ))
    return out


def record_acceptance(
    *, user, version: LegalDocumentVersion, source: str, request=None,
    order=None, language_code: str = "",
) -> LegalAcceptance:
    return LegalAcceptance.objects.create(
        user=user, version=version, order=order, source=source,
        language_code=language_code or "",
        ip_address=client_ip(request) if request is not None else None,
        user_agent=((request.META.get("HTTP_USER_AGENT") or "").strip()
                    if request is not None else ""),
    )


# ---------- public page payload ----------

def page_payload(shop, slug: str, language_code: str | None, version: str | None = None):
    """CmsPageOut-shaped dict for a legal document, or None if `slug` is
    not a legal document. Raises LookupError for an unknown version."""
    doc = document_by_slug(shop, slug)
    if doc is None:
        return None
    versions = history(doc)
    if not versions:
        return None
    current = versions[0]
    upcoming = upcoming_version(doc)
    if version:
        candidates = versions + ([upcoming] if upcoming else [])
        shown = next((v for v in candidates if v.version == version), None)
        if shown is None:
            raise LookupError(version)
    else:
        shown = current
    t = pick_translation(shown, language_code, shop=shop)
    blocks = []
    if t and t.body_markdown.strip():
        blocks.append({"type": "rich_text", "payload": {"markdown": t.body_markdown}})
    title = (t.title if t else "") or doc.get_kind_display()
    return {
        "slug": doc.slug,
        "title": title,
        "seo_title": title,
        "seo_description": "",
        "updated_at": shown.published_at or shown.effective_from,
        "content_blocks": blocks,
        "legal": {
            "kind": doc.kind,
            "version": shown.version,
            "effective_from": shown.effective_from,
            "is_current": shown.id == current.id,
            "is_upcoming": bool(upcoming and shown.id == upcoming.id),
            "change_summary": (t.change_summary if t else "") or "",
            "upcoming": (
                {"version": upcoming.version, "effective_from": upcoming.effective_from}
                if upcoming else None),
            "versions": [
                {"version": v.version, "effective_from": v.effective_from,
                 "is_current": v.id == current.id}
                for v in versions
            ],
        },
    }
