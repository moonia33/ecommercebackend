from django.db import migrations


def seed(apps, schema_editor):
    Menu = apps.get_model("cms", "Menu")
    MenuItem = apps.get_model("cms", "MenuItem")
    MenuItemTranslation = apps.get_model("cms", "MenuItemTranslation")

    # Historical models have no MPTT manager — populate tree fields
    # manually. Every item here is a root of its own tree.
    next_tree_id = [1]

    def _item(**kw):
        it = MenuItem.objects.create(
            level=0, tree_id=next_tree_id[0], lft=1, rght=2, **kw)
        next_tree_id[0] += 1
        return it

    # Promo strip above the header — one link item, text via translation.
    promo, _ = Menu.objects.get_or_create(
        code="promo", shop=None, defaults={"name": "Promo juosta"})
    item = _item(menu=promo, kind="link", link_url="")
    for lang, title in (
        ("lt", "Nemokamas pristatymas užsakymams nuo 50 €"),
        ("en", "Free shipping on orders over €50"),
        ("pl", "Darmowa dostawa od 50 €"),
    ):
        MenuItemTranslation.objects.create(
            item=item, language_code=lang, title=title)

    header, _ = Menu.objects.get_or_create(
        code="header", shop=None, defaults={"name": "Header"})
    defaults = [
        ("discounted", "percent", True, "Akcija",
         {"lt": "Nuolaidos", "en": "Sale", "pl": "Promocje"}),
        ("bestsellers", "thumbs-up", False, "",
         {"lt": "Populiariausios", "en": "Bestsellers", "pl": "Bestsellery"}),
        ("new", "package", False, "",
         {"lt": "Naujienos", "en": "New", "pl": "Nowości"}),
        ("all_categories", "layout-grid", False, "",
         {"lt": "Visos kategorijos", "en": "All categories",
          "pl": "Wszystkie kategorie"}),
        ("all_products", "store", False, "",
         {"lt": "Visos prekės", "en": "All products", "pl": "Wszystkie"}),
        ("help", "info", False, "",
         {"lt": "Pagalba", "en": "Help", "pl": "Pomoc"}),
        ("stores", "map-pin", False, "",
         {"lt": "Parduotuvės", "en": "Stores", "pl": "Sklepy"}),
    ]
    for preset, icon, highlight, badge, titles in defaults:
        it = _item(
            menu=header, kind="preset", preset=preset, icon=icon,
            highlight=highlight, badge=badge)
        for lang, title in titles.items():
            MenuItemTranslation.objects.create(
                item=it, language_code=lang, title=title)


def unseed(apps, schema_editor):
    Menu = apps.get_model("cms", "Menu")
    Menu.objects.filter(code__in=["promo", "header"], shop=None).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cms", "0009_megamenufeature_menu_menuitem_menuitemtranslation_and_more"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
