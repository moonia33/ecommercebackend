"""Kategorijos pagal nemus.lt medį (Albatros: laivai, kateriai, vandens pramogos, elektronika).

Idempotentiška: kuria tik trūkstamas kategorijas pagal slug, esamų nekeičia ir neperkelia.
Tėvinė kategorija nurodoma esamu slug'u arba None (šakninė). `--dry-run` – tik planas.
Po paleidimo normalyzer'yje: `sync_shop_categories`, tada `map_albatros_categories`.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import Category

# (tėvo slug, slug, pavadinimas, vaikai[(slug, pavadinimas, vaikai)])
TREE = [
    ("vandens-sportas", "liemenes", "Liemenės", [
        ("vyriskos-liemenes", "Vyriškos liemenės", []),
        ("moteriskos-liemenes", "Moteriškos liemenės", []),
        ("vaikiskos-liemenes", "Vaikiškos liemenės", []),
    ]),
    ("vandens-sportas", "hidrokostiumai", "Hidrokostiumai", [
        ("vyriski-hidrokostiumai", "Vyriški hidrokostiumai", []),
        ("moteriski-hidrokostiumai", "Moteriški hidrokostiumai", []),
        ("vaikiski-hidrokostiumai", "Vaikiški hidrokostiumai", []),
    ]),
    ("vandens-sportas", "vandens-atrakcionai", "Vandens atrakcionai", []),
    ("vandens-sportas", "kajakai", "Kajakai", []),
    (None, "pramoginiai-laivai", "Pramoginiai laivai", []),
    (None, "zvejybiniai-kateriai", "Žvejybiniai kateriai", []),
    (None, "laivu-aksesuarai", "Laivų aksesuarai", [
        ("laivu-laikikliai", "Laikikliai", []),
        ("laivu-priedai", "Priedai", []),
    ]),
    ("echolotai", "echolotu-priedai", "Echolotų priedai", [
        ("ethernet-tinklo-iranga", "Ethernet tinklo įranga", []),
        ("sonarai", "Sonarai", []),
        ("kiti-echolotu-priedai", "Kiti priedai", []),
    ]),
    ("valciu-varikliai", "gale-pakabinami-varikliai", "Gale pakabinami varikliai", []),
    ("valciu-varikliai", "priekyje-tvirtinami-varikliai", "Priekyje tvirtinami varikliai", []),
    ("valciu-varikliai", "elektriniu-varikliu-priedai", "Elektrinių variklių priedai", [
        ("propeleriai", "Propeleriai el. varikliams", []),
        ("tvirtinimo-ir-montavimo-iranga", "Tvirtinimo ir montavimo įranga", []),
        ("valdymo-iranga", "Valdymo įranga", []),
    ]),
    ("elektronika", "akumuliatoriai-ir-pakrovejai", "Akumuliatoriai ir pakrovėjai", []),
    ("elektronika", "audio-sistemos", "Audio sistemos", []),
]


class Command(BaseCommand):
    help = "Sukuria trūkstamas kategorijas pagal nemus.lt medį (esamų nekeičia)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Tik planas, DB nepaliečiama.")

    def handle(self, *args, dry_run: bool = False, **options):
        existing = {c.slug: c for c in Category.objects.all()}
        parents = {p for p, *_ in TREE if p}
        missing_parents = sorted(parents - existing.keys())
        if missing_parents:
            raise CommandError(f"Nėra tėvinių kategorijų: {', '.join(missing_parents)}")
        created = []
        with transaction.atomic():
            for parent_slug, slug, name, children in TREE:
                parent = existing[parent_slug] if parent_slug else None
                node = self._ensure(existing, parent, slug, name, dry_run, created)
                for child_slug, child_name, _ in children:
                    self._ensure(existing, node, child_slug, child_name, dry_run, created)
        verb = "Būtų sukurta" if dry_run else "Sukurta"
        self.stdout.write(f"{verb}: {len(created)}")

    def _ensure(self, existing, parent, slug, name, dry_run, created):
        node = existing.get(slug)
        parent_label = parent.slug if parent else "(šaknis)"
        if node is not None:
            actual = node.parent.slug if node.parent_id else "(šaknis)"
            note = "" if actual == parent_label else f" (DĖMESIO: tėvas {actual}, ne {parent_label})"
            self.stdout.write(f"  yra   {parent_label} > {slug}{note}")
            return node
        self.stdout.write(f"  nauja {parent_label} > {slug} „{name}“")
        created.append(slug)
        if dry_run:
            return Category(slug=slug, name=name, parent=parent)
        if parent is not None:
            parent.refresh_from_db()  # MPTT lft/rght pasikeičia po kiekvieno įterpimo
        node = Category.objects.create(slug=slug, name=name, parent=parent)
        existing[slug] = node
        return node
