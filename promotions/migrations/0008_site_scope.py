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


def backfill_site_ids(apps, schema_editor):
    sid = _default_site_id(apps)
    if sid is None:
        return

    Coupon = apps.get_model("promotions", "Coupon")
    PromoRule = apps.get_model("promotions", "PromoRule")

    Coupon.objects.filter(site_id__isnull=True).update(site_id=sid)
    PromoRule.objects.filter(site_id__isnull=True).update(site_id=sid)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0003_siteconfig_neopay_and_legal"),
        ("promotions", "0007_rename_promotions__is_acti_3d9e5e_idx_promotions__is_acti_a80be6_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="coupon",
            name="site",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="coupons",
                to="api.site",
            ),
        ),
        migrations.AddField(
            model_name="promorule",
            name="site",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="promo_rules",
                to="api.site",
            ),
        ),
        migrations.RunPython(code=backfill_site_ids, reverse_code=migrations.RunPython.noop),
        migrations.AlterField(
            model_name="coupon",
            name="site",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="coupons",
                to="api.site",
            ),
        ),
        migrations.AlterField(
            model_name="promorule",
            name="site",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="promo_rules",
                to="api.site",
            ),
        ),
        migrations.AlterField(
            model_name="coupon",
            name="code",
            field=models.SlugField(max_length=40),
        ),
        migrations.AddConstraint(
            model_name="coupon",
            constraint=models.UniqueConstraint(fields=("site", "code"), name="uniq_coupon_site_code"),
        ),
        migrations.RemoveIndex(
            model_name="promorule",
            name="promotions__is_acti_a80be6_idx",
        ),
        migrations.AddIndex(
            model_name="promorule",
            index=models.Index(fields=["site", "is_active", "-priority"], name="promotions__site_acti_pri_idx"),
        ),
    ]
