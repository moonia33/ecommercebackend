from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


def _default_site_id(apps) -> int | None:
    Site = apps.get_model("api", "Site")
    s = Site.objects.filter(is_active=True, code="default").only("id").first()
    if s is None:
        s = Site.objects.filter(is_active=True).only("id").order_by("code").first()
    if s is None:
        return None
    return int(s.id)


def backfill_notification_sites(apps, schema_editor):
    sid = _default_site_id(apps)
    if sid is None:
        return

    EmailTemplate = apps.get_model("notifications", "EmailTemplate")
    OutboundEmail = apps.get_model("notifications", "OutboundEmail")

    EmailTemplate.objects.filter(site_id__isnull=True).update(site_id=sid)
    OutboundEmail.objects.filter(site_id__isnull=True).update(site_id=sid)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0003_siteconfig_neopay_and_legal"),
        ("notifications", "0004_emailtemplate_language_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="emailtemplate",
            name="site",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="email_templates",
                to="api.site",
            ),
        ),
        migrations.AddField(
            model_name="outboundemail",
            name="site",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="outbound_emails",
                to="api.site",
            ),
        ),
        migrations.RunPython(backfill_notification_sites, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="emailtemplate",
            name="site",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="email_templates",
                to="api.site",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="emailtemplate",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="emailtemplate",
            constraint=models.UniqueConstraint(
                fields=("site", "key", "language_code"),
                name="uniq_emailtemplate_site_key_lang",
            ),
        ),
    ]
