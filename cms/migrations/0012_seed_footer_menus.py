from django.db import migrations


PAGES = [
    ("apie-mus", "Apie mus", "About us", "O nas"),
    ("taisykles", "Pirkimo taisyklės", "Terms of service",
     "Regulamin"),
    ("privatumas", "Privatumo politika", "Privacy policy",
     "Polityka prywatności"),
]

BODIES = {
    "apie-mus": (
        "## Apie mus\n\n"
        "„Baltijos Žvynas“ — žvejybos prekių parduotuvė. "
        "Ruošiame asortimentą patyrusiam ir pradedančiam žvejui: "
        "meškerės, ritės, masalai, aksesuarai ir atsarginės dalys.\n\n"
        "Parduotuvių adresus rasite skiltyje „Parduotuvės“."
    ),
    "taisykles": (
        "## Pirkimo taisyklės\n\n"
        "1. Užsakymas laikomas pateiktu po apmokėjimo patvirtinimo.\n"
        "2. Kainos nurodytos su PVM.\n"
        "3. Prekių rezervacija galioja ribotą laiką — "
        "neapmokėjus užsakymas atšaukiamas.\n"
        "4. Grąžinimo sąlygos aprašytos puslapyje „Grąžinimas“.\n\n"
        "Išsamios taisyklės pateikiamos užsakymo patvirtinime."
    ),
    "privatumas": (
        "## Privatumo politika\n\n"
        "Asmens duomenis (vardas, el. paštas, telefonas, adresas) "
        "naudojame užsakymams vykdyti ir sąskaitoms išrašyti.\n\n"
        "- Duomenų neperduodame tretiesiems asmenims, išskyrus "
        "vežėjus ir mokėjimo operatorius, kiek to reikia "
        "užsakymui įvykdyti.\n"
        "- Naujienlaiškį galite atsisakyti bet kada paskyroje.\n"
        "- Susisiekite dėl duomenų peržiūros ar ištrynimo."
    ),
}


def seed(apps, schema_editor):
    Shop = apps.get_model("api", "Shop")
    CmsPage = apps.get_model("cms", "CmsPage")
    CmsPageTranslation = apps.get_model("cms", "CmsPageTranslation")
    Menu = apps.get_model("cms", "Menu")
    MenuItem = apps.get_model("cms", "MenuItem")
    MenuItemTranslation = apps.get_model("cms", "MenuItemTranslation")

    # --- info pages (same per-shop + global pattern as 0011) ---
    shops = [None] + list(Shop.objects.filter(is_active=True))
    global_pages = {}
    for shop in shops:
        for i, (slug, lt, en, pl) in enumerate(PAGES):
            page, _ = CmsPage.objects.get_or_create(
                slug=slug, shop=shop,
                defaults={"sort_order": 10 + i, "is_active": True})
            if shop is None:
                global_pages[slug] = page
            body = BODIES[slug]
            for lang, title in (("lt", lt), ("en", en), ("pl", pl)):
                CmsPageTranslation.objects.get_or_create(
                    cms_page=page, language_code=lang,
                    defaults={"title": title,
                              "body_markdown": body if lang == "lt" else "",
                              "seo_title": title})

    next_tree_id = [
        (MenuItem.objects.order_by("-tree_id").values_list(
            "tree_id", flat=True).first() or 0) + 1]

    def _item(menu, parent=None, lft=1, rght=2, level=0, **kw):
        tree_id = next_tree_id[0] if parent is None else parent.tree_id
        if parent is None:
            next_tree_id[0] += 1
        return MenuItem.objects.create(
            menu=menu, parent=parent, level=level, tree_id=tree_id,
            lft=lft, rght=rght, **kw)

    def _titles(item, lt, en, pl):
        for lang, t in (("lt", lt), ("en", en), ("pl", pl)):
            MenuItemTranslation.objects.create(
                item=item, language_code=lang, title=t)

    # --- footer: roots = columns, children = links ---
    footer, _ = Menu.objects.get_or_create(
        code="footer", shop=None, defaults={"name": "Footer"})
    if not footer.items.exists():
        columns = [
            ("Pagalba", "Help", "Pomoc", [
                ("preset", "help", "Pagalbos centras", "Help center",
                 "Centrum pomocy"),
                ("link", "/grazinimas", "Keitimas ir grąžinimas",
                 "Returns & exchanges", "Zwroty"),
                ("preset", "stores", "Parduotuvės", "Stores", "Sklepy"),
                ("page", "duk", "DUK", "FAQ", "FAQ"),
            ]),
            ("Informacija", "Information", "Informacje", [
                ("page", "apie-mus", "Apie mus", "About us", "O nas"),
                ("page", "taisykles", "Taisyklės", "Terms", "Regulamin"),
                ("page", "privatumas", "Privatumas", "Privacy",
                 "Prywatność"),
                ("page", "pristatymas", "Pristatymas", "Delivery",
                 "Dostawa"),
            ]),
            ("Katalogas", "Catalog", "Katalog", [
                ("preset", "all_categories", "Visos kategorijos",
                 "All categories", "Wszystkie kategorie"),
                ("preset", "discounted", "Nuolaidos", "Sale", "Promocje"),
                ("preset", "new", "Naujienos", "New", "Nowości"),
                ("preset", "bestsellers", "Populiariausios",
                 "Bestsellers", "Bestsellery"),
                ("preset", "all_products", "Visos prekės",
                 "All products", "Wszystkie"),
            ]),
            ("Kontaktai", "Contacts", "Kontakt", [
                ("link", "tel:+37060000000", "+370 600 00000",
                 "+370 600 00000", "+370 600 00000"),
                ("link", "mailto:info@baltijoszvynas.lt",
                 "info@baltijoszvynas.lt", "info@baltijoszvynas.lt",
                 "info@baltijoszvynas.lt"),
                ("preset", "stores", "Parduotuvių kontaktai",
                 "Store contacts", "Kontakty sklepów"),
            ]),
        ]
        for col_lt, col_en, col_pl, children in columns:
            parent = _item(footer, kind="link", link_url="",
                           rght=2 + 2 * len(children))
            _titles(parent, col_lt, col_en, col_pl)
            lft = 2
            for kind, target, lt, en, pl in children:
                kw = {}
                if kind == "link":
                    kw = {"kind": "link", "link_url": target}
                elif kind == "preset":
                    kw = {"kind": "preset", "preset": target}
                else:
                    page = global_pages.get(target)
                    if page is None:
                        # 0011 seeded pristatymas/duk as well — look up.
                        page = CmsPage.objects.filter(
                            slug=target, shop=None).first()
                    if page is None:
                        continue
                    kw = {"kind": "cms_page", "cms_page": page}
                child = _item(footer, parent=parent, lft=lft,
                              rght=lft + 1, level=1, **kw)
                lft += 2
                _titles(child, lt, en, pl)

    # --- footer_bottom: legal strip links ---
    bottom, _ = Menu.objects.get_or_create(
        code="footer_bottom", shop=None,
        defaults={"name": "Footer apačia"})
    if not bottom.items.exists():
        for slug, lt, en, pl in (
                ("taisykles", "Taisyklės", "Terms", "Regulamin"),
                ("privatumas", "Privatumas", "Privacy", "Prywatność")):
            page = global_pages.get(slug) or CmsPage.objects.filter(
                slug=slug, shop=None).first()
            if page is None:
                continue
            it = _item(bottom, kind="cms_page", cms_page=page)
            _titles(it, lt, en, pl)

    # Historical models don't fire post_save — bump the nav cache
    # version manually so storefronts see the new menus immediately.
    try:
        from django.core.cache import cache
        cache.incr("cms:nav:ver")
    except (ImportError, ValueError):
        pass


def unseed(apps, schema_editor):
    Menu = apps.get_model("cms", "Menu")
    CmsPage = apps.get_model("cms", "CmsPage")
    Menu.objects.filter(
        code__in=["footer", "footer_bottom"], shop=None).delete()
    CmsPage.objects.filter(slug__in=[p[0] for p in PAGES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cms", "0011_seed_info_pages"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
