from __future__ import annotations

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecentlyViewedProduct",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("visitor_id", models.CharField(blank=True, default="", max_length=64)),
                ("last_viewed_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recently_viewed_by",
                        to="catalog.product",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recently_viewed_products",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="recentlyviewedproduct",
            constraint=models.UniqueConstraint(
                fields=("user", "product"),
                condition=models.Q(("user__isnull", False)),
                name="analytics_rvp_unique_user_product",
            ),
        ),
        migrations.AddConstraint(
            model_name="recentlyviewedproduct",
            constraint=models.UniqueConstraint(
                fields=("visitor_id", "product"),
                condition=~models.Q(("visitor_id", "")),
                name="analytics_rvp_unique_visitor_product",
            ),
        ),
        migrations.AddIndex(
            model_name="recentlyviewedproduct",
            index=models.Index(fields=["user", "last_viewed_at"], name="analytics_r_user_id_5b7cb1_idx"),
        ),
        migrations.AddIndex(
            model_name="recentlyviewedproduct",
            index=models.Index(fields=["visitor_id", "last_viewed_at"], name="analytics_r_visito_496f5c_idx"),
        ),
        migrations.AddIndex(
            model_name="recentlyviewedproduct",
            index=models.Index(fields=["product", "last_viewed_at"], name="analytics_r_product_6f7731_idx"),
        ),
    ]
