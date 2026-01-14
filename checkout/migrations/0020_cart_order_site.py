from __future__ import annotations

from django.conf import settings
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


def backfill_cart_order_site(apps, schema_editor):
    sid = _default_site_id(apps)
    if sid is None:
        return

    Cart = apps.get_model("checkout", "Cart")
    Order = apps.get_model("checkout", "Order")

    Cart.objects.filter(site_id__isnull=True).update(site_id=sid)
    Order.objects.filter(site_id__isnull=True).update(site_id=sid)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0003_siteconfig_neopay_and_legal"),
        ("checkout", "0019_alter_order_shipping_method"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="cart",
            name="site",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="carts",
                to="api.site",
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="site",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="orders",
                to="api.site",
            ),
        ),
        migrations.RunPython(code=backfill_cart_order_site, reverse_code=migrations.RunPython.noop),
        migrations.AlterField(
            model_name="cart",
            name="site",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="carts",
                to="api.site",
            ),
        ),
        migrations.AlterField(
            model_name="order",
            name="site",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="orders",
                to="api.site",
            ),
        ),
        migrations.AlterField(
            model_name="cart",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="carts",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="cart",
            name="uniq_cart_session_key",
        ),
        migrations.AddConstraint(
            model_name="cart",
            constraint=models.UniqueConstraint(
                condition=~models.Q(("session_key", "")),
                fields=("site", "session_key"),
                name="uniq_cart_session_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="cart",
            constraint=models.UniqueConstraint(
                condition=models.Q(("user__isnull", False)),
                fields=("site", "user"),
                name="uniq_cart_site_user",
            ),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["site", "user", "-created_at"], name="checkout_or_site_user_ca_idx"),
        ),
        migrations.RemoveConstraint(
            model_name="order",
            name="uniq_order_idempotency_key_per_user",
        ),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.UniqueConstraint(
                condition=~models.Q(("idempotency_key", "")),
                fields=("site", "user", "idempotency_key"),
                name="uniq_order_idempotency_key_per_user",
            ),
        ),
    ]
