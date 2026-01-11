from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="CmsPage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(max_length=200, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["slug"],
            },
        ),
        migrations.CreateModel(
            name="CmsPageTranslation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language_code", models.CharField(max_length=8)),
                ("title", models.CharField(blank=True, default="", max_length=255)),
                ("seo_title", models.CharField(blank=True, default="", max_length=255)),
                ("seo_description", models.TextField(blank=True, default="")),
                ("body_markdown", models.TextField(blank=True, default="")),
                (
                    "cms_page",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="translations",
                        to="cms.cmspage",
                    ),
                ),
            ],
            options={
                "unique_together": {("cms_page", "language_code")},
            },
        ),
        migrations.AddIndex(
            model_name="cmspagetranslation",
            index=models.Index(fields=["cms_page", "language_code"], name="cms_cmspag_cms_pag_77e9c7_idx"),
        ),
    ]
