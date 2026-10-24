from django.db import migrations


PAGES = [
    # slug, lt title, en title, pl title
    ("pristatymas", "Pristatymas", "Delivery", "Dostawa"),
    ("grazinimas", "Grąžinimas", "Returns", "Zwroty"),
    ("duk", "Dažnai užduodami klausimai", "FAQ", "FAQ"),
    ("kontaktai", "Kontaktai", "Contacts", "Kontakt"),
]

BODIES = {
    "pristatymas": (
        "## Pristatymas\n\n"
        "Užsakymus pristatome per DPD paštomatus ir kurjerius, "
        "Unisend / LP Express terminalus bei atsiėmimą parduotuvėje.\n\n"
        "- Paštomatai: pristatymas per 1–2 darbo dienas.\n"
        "- Kurjeris: pristatymas per 1–3 darbo dienas.\n"
        "- Skaitmeninės prekės (dovanų kortelės): kodą atsiunčiame "
        "el. paštu iškart po apmokėjimo.\n\n"
        "Apie siuntos būsenos pasikeitimus informuojame el. paštu."
    ),
    "grazinimas": (
        "## Grąžinimas\n\n"
        "Prekes galite grąžinti per 14 dienų nuo jų gavimo.\n\n"
        "- Prekė turi būti nenaudota, originalioje pakuotėje.\n"
        "- Dovanų kortelės ir kitos skaitmeninės prekės "
        "negrąžinamos.\n"
        "- Grąžinimo prašymą galite pateikti paskyroje: "
        "„Grąžinimai“ → „Naujas grąžinimas“.\n\n"
        "Pinigai grąžinami tuo pačiu mokėjimo būdu per 3–5 "
        "darbo dienas po prekės patikrinimo."
    ),
    "duk": (
        "## Dažnai užduodami klausimai\n\n"
        "**Ar galiu pakeisti užsakymą po apmokėjimo?** "
        "Taip, kol jis dar nėra išsiųstas — susisiekite su mumis.\n\n"
        "**Kaip panaudoti dovanų kortelę?** Įveskite kodą "
        "apmokėjimo žingsnyje — suma nurašoma iš kortelės likučio.\n\n"
        "**Ar dovanų kortelę galima grąžinti?** Ne — skaitmeninės "
        "prekės negrąžinamos."
    ),
    "kontaktai": (
        "## Kontaktai\n\n"
        "Klausimais dėl užsakymų, grąžinimų ar prekių kreipkitės:\n\n"
        "- El. paštas: info@baltijoszvynas.lt\n"
        "- Parduotuvių adresus ir darbo laiką rasite "
        "skiltyje „Parduotuvės“."
    ),
}


def seed(apps, schema_editor):
    Shop = apps.get_model("api", "Shop")
    CmsPage = apps.get_model("cms", "CmsPage")
    CmsPageTranslation = apps.get_model("cms", "CmsPageTranslation")
    Menu = apps.get_model("cms", "Menu")
    MenuItem = apps.get_model("cms", "MenuItem")
    MenuItemTranslation = apps.get_model("cms", "MenuItemTranslation")

    # Info pages per shop + a global (shop=None) copy — CmsPage is
    # shop-scoped in the API (no global fallback like menus have).
    shops = [None] + list(Shop.objects.filter(is_active=True))
    pages_by_shop = {}
    for shop in shops:
        for i, (slug, lt, en, pl) in enumerate(PAGES):
            page, _ = CmsPage.objects.get_or_create(
                slug=slug, shop=shop,
                defaults={"sort_order": i, "is_active": True})
            pages_by_shop[shop.id if shop else None] = pages_by_shop.get(
                shop.id if shop else None, {})
            pages_by_shop[shop.id if shop else None][slug] = page
            body = BODIES[slug]
            for lang, title in (("lt", lt), ("en", en), ("pl", pl)):
                CmsPageTranslation.objects.get_or_create(
                    cms_page=page, language_code=lang,
                    defaults={"title": title,
                              "body_markdown": body if lang == "lt" else "",
                              "seo_title": title})

    # "Puslapiai" parent with cms_page children appended to every
    # header menu that doesn't have a pages dropdown yet.
    for menu in Menu.objects.filter(code="header", is_active=True):
        if menu.items.filter(
                children__kind="cms_page").exists() or menu.items.filter(
                translations__title__in=["Puslapiai", "Pages"]).exists():
            continue
        next_tree_id = (
            menu.items.order_by("-tree_id").values_list(
                "tree_id", flat=True).first() or 0) + 1
        shop_pages = pages_by_shop.get(menu.shop_id) \
            or pages_by_shop.get(None) or {}
        parent = MenuItem.objects.create(
            menu=menu, kind="link", link_url="", icon="layers",
            tree_id=next_tree_id, lft=1, rght=2 + 2 * len(PAGES),
            level=0)
        for lang, title in (
                ("lt", "Puslapiai"), ("en", "Pages"), ("pl", "Strony")):
            MenuItemTranslation.objects.create(
                item=parent, language_code=lang, title=title)
        lft = 2
        for slug, *_ in PAGES:
            page = shop_pages.get(slug)
            if page is None:
                continue
            child = MenuItem.objects.create(
                menu=menu, parent=parent, kind="cms_page", cms_page=page,
                tree_id=next_tree_id, lft=lft, rght=lft + 1, level=1)
            lft += 2
            title = {p[0]: (p[1], p[2], p[3]) for p in PAGES}[slug]
            for lang, t in zip(("lt", "en", "pl"), title):
                MenuItemTranslation.objects.create(
                    item=child, language_code=lang, title=t)

    # Historical models don't fire post_save — bump the nav cache
    # version manually so storefronts see the new menus immediately.
    try:
        from django.core.cache import cache
        cache.incr("cms:nav:ver")
    except (ImportError, ValueError):
        pass


def unseed(apps, schema_editor):
    CmsPage = apps.get_model("cms", "CmsPage")
    CmsPage.objects.filter(slug__in=[p[0] for p in PAGES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cms", "0010_seed_header_menu"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
