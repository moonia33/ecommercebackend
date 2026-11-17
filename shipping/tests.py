from __future__ import annotations

from datetime import date

from django.core.cache import cache
from django.test import TestCase

from .models import Holiday
from .services import add_business_days, is_business_day


class HolidayCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_business_days_use_single_query_per_country(self):
        Holiday.objects.create(country_code="LT", date=date(2026, 12, 24))
        Holiday.objects.create(country_code="LT", date=date(2026, 12, 25))
        with self.assertNumQueries(1):
            self.assertEqual(
                add_business_days(start=date(2026, 12, 22), days=5, country_code="LT"),
                date(2026, 12, 31),
            )
            self.assertFalse(is_business_day(d=date(2026, 12, 24), country_code="LT"))

    def test_cache_invalidated_on_save_and_delete(self):
        d = date(2026, 12, 23)
        self.assertTrue(is_business_day(d=d, country_code="LT"))
        h = Holiday.objects.create(country_code="LT", date=d)
        self.assertFalse(is_business_day(d=d, country_code="LT"))
        h.is_active = False
        h.save()
        self.assertTrue(is_business_day(d=d, country_code="LT"))
        h.delete()
        self.assertTrue(is_business_day(d=d, country_code="LT"))

    def test_countries_are_separate(self):
        Holiday.objects.create(country_code="DE", date=date(2026, 10, 3))
        self.assertTrue(is_business_day(d=date(2026, 10, 5), country_code="LT"))
        self.assertTrue(is_business_day(d=date(2026, 10, 2), country_code="DE"))
        Holiday.objects.create(country_code="DE", date=date(2026, 10, 2))
        self.assertFalse(is_business_day(d=date(2026, 10, 2), country_code="DE"))
        self.assertTrue(is_business_day(d=date(2026, 10, 2), country_code="LT"))
