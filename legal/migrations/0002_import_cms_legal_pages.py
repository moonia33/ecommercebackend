"""Moves existing terms/privacy CMS pages into versioned legal documents.

The imported version keeps the label from CHECKOUT_*_VERSION so existing
OrderConsent rows map onto it; those rows are backfilled as acceptances.
"""
import hashlib

from django.conf import settings
from django.db import migrations

PAGES = (
    ("terms", "taisykles", "CHECKOUT_TERMS_VERSION"),
    ("privacy", "privatumas", "CHECKOUT_PRIVACY_VERSION"),
)


def forwards(apps, schema_editor):
    CmsPage = apps.get_model("cms", "CmsPage")
    LegalDocument = apps.get_model("legal", "LegalDocument")
    Version = apps.get_model("legal", "LegalDocumentVersion")
    Translation = apps.get_model("legal", "LegalDocumentVersionTranslation")
    Acceptance = apps.get_model("legal", "LegalAcceptance")
    OrderConsent = apps.get_model("checkout", "OrderConsent")

    for kind, slug, setting in PAGES:
        label = getattr(settings, setting, "v1") or "v1"
        for page in CmsPage.objects.filter(slug=slug):
            translations = [t for t in page.translations.all() if t.body_markdown.strip()]
            if not translations:
                continue
            if LegalDocument.objects.filter(shop_id=page.shop_id, kind=kind).exists():
                continue
            doc = LegalDocument.objects.create(
                shop_id=page.shop_id, kind=kind, slug=slug,
                required_at_checkout=True, is_active=page.is_active)
            eff = page.created_at
            ver = Version.objects.create(
                document=doc, version=label, status="published",
                effective_from=eff, published_at=eff)
            for t in translations:
                Translation.objects.create(
                    version=ver, language_code=t.language_code, title=t.title,
                    body_markdown=t.body_markdown,
                    content_hash=hashlib.sha256(
                        f"{t.title}\n{t.body_markdown}".encode()).hexdigest())
            consents = OrderConsent.objects.filter(
                kind=kind, document_version=label, order__user__isnull=False)
            if page.shop_id:
                consents = consents.filter(order__shop_id=page.shop_id)
            consents.update(legal_version=ver)
            Acceptance.objects.bulk_create([
                Acceptance(
                    user_id=c.order.user_id, version=ver, order_id=c.order_id,
                    source="checkout", accepted_at=c.accepted_at,
                    ip_address=c.ip_address, user_agent=c.user_agent or "")
                for c in consents.select_related("order")
            ], batch_size=500)
            page.is_active = False
            page.save(update_fields=["is_active"])


class Migration(migrations.Migration):
    dependencies = [
        ("legal", "0001_initial"),
        ("cms", "0013_trim_header_menu"),
        ("checkout", "0042_orderconsent_legal_version"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
