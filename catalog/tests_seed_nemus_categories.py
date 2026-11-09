from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from catalog.models import Category


class SeedNemusCategoriesTests(TestCase):
    def _parents(self):
        electronics = Category.objects.create(name="Elektronika", slug="elektronika")
        Category.objects.create(name="Echolotai", slug="echolotai", parent=electronics)
        Category.objects.create(name="Valčių varikliai", slug="valciu-varikliai", parent=electronics)
        Category.objects.create(name="Vandens Sportas", slug="vandens-sportas")

    def test_dry_run_writes_nothing(self):
        self._parents()
        out = StringIO()
        call_command("seed_nemus_categories", "--dry-run", stdout=out)
        self.assertEqual(Category.objects.count(), 4)
        self.assertIn("Būtų sukurta: 24", out.getvalue())

    def test_creates_missing_once_and_keeps_existing(self):
        self._parents()
        kept = Category.objects.create(
            name="Mano kajakai", slug="kajakai", parent=Category.objects.get(slug="vandens-sportas")
        )
        call_command("seed_nemus_categories", stdout=StringIO())
        call_command("seed_nemus_categories", stdout=StringIO())
        kept.refresh_from_db()
        self.assertEqual(kept.name, "Mano kajakai")
        self.assertEqual(Category.objects.count(), 4 + 24)
        vest = Category.objects.get(slug="vyriskos-liemenes")
        self.assertEqual(vest.parent.slug, "liemenes")
        self.assertEqual(vest.parent.parent.slug, "vandens-sportas")
        self.assertIsNone(Category.objects.get(slug="pramoginiai-laivai").parent)
        motors = Category.objects.get(slug="valciu-varikliai")
        for slug in ("propeleriai", "gale-pakabinami-varikliai"):
            self.assertTrue(Category.objects.get(slug=slug).is_descendant_of(motors))
        sonar = Category.objects.get(slug="sonarai")
        self.assertTrue(sonar.is_descendant_of(Category.objects.get(slug="echolotai")))
        self.assertFalse(sonar.is_descendant_of(motors))

    def test_missing_parent_stops(self):
        with self.assertRaises(CommandError):
            call_command("seed_nemus_categories", stdout=StringIO())
