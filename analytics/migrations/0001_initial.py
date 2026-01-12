from __future__ import annotations

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0007_remove_useraddress_line2_remove_useraddress_region_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnalyticsEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(choices=[("product_view", "Product view"), ("add_to_cart", "Add to cart"), ("remove_from_cart", "Remove from cart"), ("view_cart", "View cart"), ("begin_checkout", "Begin checkout"), ("purchase", "Purchase")], max_length=64)),
                ("occurred_at", models.DateTimeField()),
                ("visitor_id", models.CharField(blank=True, default="", max_length=64)),
                ("object_type", models.CharField(blank=True, default="", max_length=64)),
                ("object_id", models.BigIntegerField(blank=True, null=True)),
                ("country_code", models.CharField(blank=True, default="", max_length=2)),
                ("channel", models.CharField(blank=True, default="", max_length=32)),
                ("language_code", models.CharField(blank=True, default="", max_length=8)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("idempotency_key", models.CharField(max_length=64, unique=True)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="VisitorLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("visitor_id", models.CharField(max_length=64)),
                ("first_seen_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={"unique_together": {("user", "visitor_id")}},
        ),
        migrations.CreateModel(
            name="AnalyticsOutbox",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(choices=[("newsman", "Newsman"), ("facebook", "Facebook"), ("google", "Google")], max_length=32)),
                ("status", models.CharField(default="pending", max_length=32)),
                ("attempts", models.IntegerField(default=0)),
                ("last_error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="outbox", to="analytics.analyticsevent")),
            ],
            options={"unique_together": {("event", "provider")}},
        ),
        migrations.AddIndex(
            model_name="analyticsevent",
            index=models.Index(fields=["name", "occurred_at"], name="analytics_an_name_3a9193_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsevent",
            index=models.Index(fields=["user", "occurred_at"], name="analytics_an_user_i_60f6cc_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsevent",
            index=models.Index(fields=["visitor_id", "occurred_at"], name="analytics_an_visito_62c9b1_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsevent",
            index=models.Index(fields=["object_type", "object_id"], name="analytics_an_object_59840b_idx"),
        ),
        migrations.AddIndex(
            model_name="visitorlink",
            index=models.Index(fields=["visitor_id"], name="analytics_vi_visito_21d4f5_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsoutbox",
            index=models.Index(fields=["provider", "status", "created_at"], name="analytics_an_provid_a5a60c_idx"),
        ),
    ]
