from __future__ import annotations

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0005_remove_recentlyviewedproduct_analytics_r_user_id_078de6_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="FavoriteProduct",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("visitor_id", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "product",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="favorited_by", to="catalog.product"),
                ),
                (
                    "site",
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="favorite_products", to="api.site"),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="favorite_products",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="favoriteproduct",
            index=models.Index(fields=["site", "user", "created_at"], name="analytics_fa_site_id_4a3ab6_idx"),
        ),
        migrations.AddIndex(
            model_name="favoriteproduct",
            index=models.Index(fields=["site", "visitor_id", "created_at"], name="analytics_fa_site_id_2f3b73_idx"),
        ),
        migrations.AddIndex(
            model_name="favoriteproduct",
            index=models.Index(fields=["product", "created_at"], name="analytics_fa_product_id_1df62d_idx"),
        ),
        migrations.AddConstraint(
            model_name="favoriteproduct",
            constraint=models.UniqueConstraint(
                condition=models.Q(("user__isnull", False)),
                fields=("site", "user", "product"),
                name="analytics_fav_unique_user_product",
            ),
        ),
        migrations.AddConstraint(
            model_name="favoriteproduct",
            constraint=models.UniqueConstraint(
                condition=~models.Q(("visitor_id", "")),
                fields=("site", "visitor_id", "product"),
                name="analytics_fav_unique_visitor_product",
            ),
        ),
    ]
