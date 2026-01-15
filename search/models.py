from __future__ import annotations

from django.db import models


class SearchSynonym(models.Model):
    language_code = models.CharField(max_length=10, default="lt")
    term = models.CharField(max_length=200)
    synonyms = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["language_code", "term"], name="uniq_searchsynonym_language_term")
        ]

    def __str__(self) -> str:
        return f"{self.language_code}:{self.term}"
