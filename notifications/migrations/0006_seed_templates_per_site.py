from __future__ import annotations

from django.db import migrations


TEMPLATE_KEYS = [
    "auth_otp_code",
    "catalog_back_in_stock",
]


def _default_site(apps):
    Site = apps.get_model("api", "Site")
    s = Site.objects.filter(is_active=True, code="default").first()
    if s is None:
        s = Site.objects.filter(is_active=True).order_by("code").first()
    return s


def seed_templates_per_site(apps, schema_editor):
    Site = apps.get_model("api", "Site")
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")

    default_site = _default_site(apps)
    if default_site is None:
        return

    default_templates = list(
        EmailTemplate.objects.filter(
            site_id=int(default_site.id),
            key__in=TEMPLATE_KEYS,
        )
    )
    if not default_templates:
        return

    for site in Site.objects.filter(is_active=True).only("id").iterator():
        sid = int(site.id)
        for tmpl in default_templates:
            EmailTemplate.objects.get_or_create(
                site_id=sid,
                key=tmpl.key,
                language_code=tmpl.language_code,
                defaults={
                    "name": tmpl.name,
                    "subject": tmpl.subject,
                    "body_text": tmpl.body_text,
                    "body_html": tmpl.body_html,
                    "is_active": bool(tmpl.is_active),
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0005_emailtemplate_site_scope"),
    ]

    operations = [
        migrations.RunPython(seed_templates_per_site, migrations.RunPython.noop),
    ]
