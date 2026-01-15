from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SearchSynonym",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language_code", models.CharField(default="lt", max_length=10)),
                ("term", models.CharField(max_length=200)),
                ("synonyms", models.JSONField(blank=True, default=list)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={},
        ),
        migrations.AddConstraint(
            model_name="searchsynonym",
            constraint=models.UniqueConstraint(
                fields=("language_code", "term"),
                name="uniq_searchsynonym_language_term",
            ),
        ),
    ]
