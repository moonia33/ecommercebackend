from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cms", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="cmspagetranslation",
            name="hero_image",
            field=models.ImageField(blank=True, null=True, upload_to="cms/pages/"),
        ),
        migrations.AddField(
            model_name="cmspagetranslation",
            name="hero_image_alt",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
