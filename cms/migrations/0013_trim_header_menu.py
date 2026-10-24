from django.db import migrations


def trim(apps, schema_editor):
    MenuItem = apps.get_model("cms", "MenuItem")

    # Remove the generic catalog presets — categories are already
    # reachable via the catalog menu / drill-down.
    MenuItem.objects.filter(
        kind="preset", preset__in=["all_categories", "all_products"]
    ).delete()

    # "Puslapiai" dropdown merged into "Pagalba" (help center) and the
    # footer Info/Help columns — remove the header item and its
    # cms_page children (MPTT delete removes the subtree).
    MenuItem.objects.filter(
        kind="link", children__kind="cms_page"
    ).filter(menu__code="header").delete()

    try:
        from django.core.cache import cache
        cache.incr("cms:nav:ver")
    except (ImportError, ValueError):
        pass


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("cms", "0012_seed_footer_menus"),
    ]

    operations = [migrations.RunPython(trim, noop)]
