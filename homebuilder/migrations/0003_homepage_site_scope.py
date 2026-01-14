from __future__ import annotations

from django.db import migrations, models


def _default_site_id(apps) -> int | None:
    Site = apps.get_model("api", "Site")
    s = Site.objects.filter(is_active=True, code="default").only("id").first()
    if s is None:
        s = Site.objects.filter(is_active=True).only("id").order_by("code").first()
    if s is None:
        return None
    return int(s.id)


def backfill_homepage_site(apps, schema_editor):
    sid = _default_site_id(apps)
    if sid is None:
        return

    HomePage = apps.get_model("homebuilder", "HomePage")
    HomePage.objects.filter(site_id__isnull=True).update(site_id=sid)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0001_initial"),
        (
            "homebuilder",
            "0002_rename_homebuilder_h_hero_sl_0c9c5b_idx_homebuilder_hero_sl_f30c9b_idx_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="homepage",
            name="site",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="home_pages",
                to="api.site",
            ),
        ),
        migrations.AlterField(
            model_name="homepage",
            name="code",
            field=models.SlugField(default="home", max_length=64),
        ),
        migrations.RunPython(backfill_homepage_site, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="homepage",
            name="site",
            field=models.ForeignKey(
                on_delete=models.deletion.PROTECT,
                related_name="home_pages",
                to="api.site",
            ),
        ),
        migrations.AddConstraint(
            model_name="homepage",
            constraint=models.UniqueConstraint(
                fields=("site", "code"),
                name="uniq_homepage_site_code",
            ),
        ),
    ]
