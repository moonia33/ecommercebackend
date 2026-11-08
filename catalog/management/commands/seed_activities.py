"""Pradinis veiklų medis ir taisyklės (BZ-28). Idempotentiška.

Naujos taisyklės kuriamos IŠJUNGTOS (is_active=False): pirma
`apply_activity_rules --dry-run`, peržiūra, tada įjungiama admine. Be
`--update` esamų įrašų nekeičia – tik sukuria trūkstamus. Su `--update`
esamoms taisyklėms (pagal veiklą + pavadinimą) perrašo kategorijas,
gamintojus ir tekstą (is_active nekeičia). Nerasti slug'ai parodomi išvestyje.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import Activity, ActivityRule, Brand, Category

EXCLUDE = ["atsargines-dalys"]

TREE = [
    ("zvejyba", "Žvejyba", 10, [
        ("karpine", "Karpinė žvejyba", 10),
        ("spiningas", "Spiningas ir plėšrūnai", 20),
        ("pludine", "Plūdinė žvejyba", 30),
        ("feeder", "Feeder ir dugninė žvejyba", 40),
        ("samu", "Šamų žvejyba", 50),
        ("jurine", "Jūrinė žvejyba", 60),
        ("museline", "Muselinė žvejyba", 70),
        ("poledine", "Poledinė žvejyba", 80),
    ]),
    ("turizmas", "Turizmas ir išgyvenimas", 20, [
        ("stovyklavimas", "Stovyklavimas", 10),
    ]),
    ("vandens-pramogos", "Vandens pramogos", 30, [
        ("vandens-sportas", "Vandens sportas", 10),
    ]),
    ("ziemos-sportas", "Žiemos sportas", 40, []),
    ("aktyvus-laisvalaikis", "Aktyvus laisvalaikis", 50, []),
]

# (veikla, pavadinimas, kategorijos, gamintojai, pattern[, matcher_type])
RULES = [
    ("karpine", "Karpinės kategorijos", [
        "karpiniai-meskerykociai", "karpines-rites", "karpines-sistemeles",
        "karpiniai-aksesuarai", "karpiniai-kabliukai", "boiliai", "peletes", "pva",
    ], [], ""),
    ("karpine", "Karpiniai gamintojai", [], [
        "korda", "nash", "mainline", "trakker", "ridgemonkey", "cc-moore",
        "dynamite", "forgotten-flavours",
    ], ""),
    ("spiningas", "Spiningo kategorijos", ["spiningai", "rites-spiningavimui", "spiningui"], [], ""),
    ("spiningas", "Plėšrūnų gamintojai", [], ["fox-rage", "savage-gear", "keitech"], ""),
    ("spiningas", "Spiningavimo svareliai", ["spiningavimo-svareliai"], [], ""),
    ("pludine", "Plūdinės kategorijos", ["pludiniai-meskerykociai", "pludes", "pludines-rites"], [], ""),
    ("karpine", "Kibimo indikatoriai ir stovai", [
        "elektroniniai-kibimo-indikatoriai", "mechaniniai-kibimo-indikatoriai", "svingai",
        "stovai", "meskeriu-laikikliai",
    ], [], ""),
    ("feeder", "Feeder kategorijos", [
        "meskerykociai-dugninei", "rites-dugninei-zvejybai", "serykleles", "feederio-aksesuarai",
    ], [], ""),
    ("samu", "Šamų kategorijos", ["samu-aksesuarai"], [], ""),
    ("samu", "Šamai pavadinime", [], [], r"\b(šamų|šamui|šamams|catfish|wels)\b", "regex"),
    ("jurine", "Jūrinės kategorijos", ["juriniai-meskerykociai", "jurines-rites", "velkiavimui"], [], ""),
    ("jurine", "Jūrinė pavadinime", [], [], "jūrin|jurin|sea fishing|pilker"),
    ("museline", "Muselinės kategorijos", ["muselines-meskerykociai", "muslines", "muselines"], [], ""),
    ("poledine", "Poledinės kategorijos", ["poledine-zukle"], [], ""),
    ("stovyklavimas", "Stovyklos kategorijos", ["stovykla", "palapines-ir-skeciai"], [], ""),
    ("vandens-sportas", "Vandens sporto kategorijos", ["vandens-sportas"], [], ""),
]


class Command(BaseCommand):
    help = "Sukuria pradinį veiklų medį ir (išjungtas) taisykles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--update",
            action="store_true",
            help="Esamoms taisyklėms perrašyti kategorijas, gamintojus ir tekstą.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        acts: dict[str, Activity] = {}
        for code, name, order, children in TREE:
            root, created = Activity.objects.get_or_create(
                code=code, defaults={"name": name, "sort_order": order}
            )
            acts[code] = root
            self.stdout.write(f"{'+' if created else '='} {code}")
            for ccode, cname, corder in children:
                child, created = Activity.objects.get_or_create(
                    code=ccode, defaults={"name": cname, "sort_order": corder, "parent": root}
                )
                acts[ccode] = child
                self.stdout.write(f"  {'+' if created else '='} {ccode}")

        excl = list(Category.objects.filter(slug__in=EXCLUDE))
        for act_code, name, cats, brands, pattern, *rest in RULES:
            matcher = rest[0] if rest else ActivityRule.MatcherType.CONTAINS
            existing = ActivityRule.objects.filter(activity=acts[act_code], name=name).first()
            if existing and not opts["update"]:
                self.stdout.write(f"= rule {act_code}: {name}")
                continue
            cat_objs = list(Category.objects.filter(slug__in=cats))
            brand_objs = list(Brand.objects.filter(slug__in=brands))
            missing = sorted(set(cats) - {c.slug for c in cat_objs}) + sorted(
                set(brands) - {b.slug for b in brand_objs}
            )
            if (cats and not cat_objs) or (brands and not brand_objs):
                self.stdout.write(f"! rule {act_code}: {name} praleista, nerasta: {missing}")
                continue
            if existing:
                rule = existing
                rule.pattern = pattern
                rule.matcher_type = matcher
                rule.save(update_fields=["pattern", "matcher_type", "updated_at"])
            else:
                rule = ActivityRule.objects.create(
                    activity=acts[act_code],
                    name=name,
                    is_active=False,
                    pattern=pattern,
                    matcher_type=matcher,
                )
            rule.categories.set(cat_objs)
            rule.brands.set(brand_objs)
            rule.exclude_categories.set(excl)
            self.stdout.write(
                f"{'~' if existing else '+'} rule {act_code}: {name}"
                + (f" (nerasta: {missing})" if missing else "")
            )
