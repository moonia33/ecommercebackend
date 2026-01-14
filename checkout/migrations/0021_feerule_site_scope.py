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


def backfill_feerule_site(apps, schema_editor):
    sid = _default_site_id(apps)
    if sid is None:
        return

    FeeRule = apps.get_model("checkout", "FeeRule")
    FeeRule.objects.filter(site_id__isnull=True).update(site_id=sid)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0003_siteconfig_neopay_and_legal"),
        ("checkout", "0020_cart_order_site"),
    ]

    operations = [
        migrations.AddField(
            model_name="feerule",
            name="site",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="fee_rules",
                to="api.site",
            ),
        ),
        migrations.AlterField(
            model_name="feerule",
            name="code",
            field=models.SlugField(max_length=50),
        ),
        migrations.RunPython(backfill_feerule_site, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="feerule",
            name="site",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="fee_rules",
                to="api.site",
            ),
        ),
        migrations.AddConstraint(
            model_name="feerule",
            constraint=models.UniqueConstraint(
                fields=("site", "code"),
                name="uniq_feerule_site_code",
            ),
        ),
        migrations.AddIndex(
            model_name="feerule",
            index=models.Index(
                fields=["site", "is_active", "country_code", "sort_order"],
                name="checkout_fe_site_i_3cc8b1_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="feerule",
            index=models.Index(
                fields=["site", "payment_method_code"],
                name="checkout_fe_site_p_58d2a3_idx",
            ),
        ),
    ]
