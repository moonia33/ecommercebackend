from __future__ import annotations

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("checkout", "0006_order_label_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="cart",
            name="session_key",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AlterField(
            model_name="cart",
            name="user",
            field=models.OneToOneField(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="cart", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddConstraint(
            model_name="cart",
            constraint=models.CheckConstraint(check=(~models.Q(("user", None)) | ~models.Q(
                ("session_key", ""))), name="chk_cart_user_or_session"),
        ),
        migrations.AddConstraint(
            model_name="cart",
            constraint=models.UniqueConstraint(condition=~models.Q(
                ("session_key", "")), fields=("session_key",), name="uniq_cart_session_key"),
        ),
    ]
