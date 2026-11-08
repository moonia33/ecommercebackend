"""Veiklos (žvejybos būdai, turizmas, vandens pramogos...) – atskira M:N ašis.

Kategorija sako, kas yra prekė; veikla – kam ji naudojama. Dviejų lygių
medis: sritis (`zvejyba`) → veikla (`karpine`). Prekė priskiriama:
  - taisykle (ActivityRule → source=rule; taisyklė pati prideda ir nuima),
  - rankiniu būdu (source=manual),
  - rankiniu išėmimu (source=exclude – nugali taisykles).
Filtras pagal sritį apima visas jos veiklas. Typesense'e – `activities`.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Activity(models.Model):
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=128, help_text="Rodomas pavadinimas (LT).")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children",
        help_text="Tuščia – sritis (pvz. Žvejyba); užpildyta – veikla srityje.",
    )
    short_description = models.CharField(max_length=255, blank=True, default="")
    content_json = models.JSONField(
        null=True, blank=True, help_text="Nusileidimo puslapio įvadas (Tiptap JSON)."
    )
    image = models.ImageField(upload_to="activities/", blank=True, default="")
    image_alt = models.CharField(max_length=255, blank=True, default="")
    seo_title = models.CharField(max_length=255, blank=True, default="")
    seo_description = models.CharField(max_length=320, blank=True, default="")
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    min_products = models.PositiveIntegerField(
        default=20, help_text="Fronte rodoma tik turint bent tiek prekių."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "name")
        verbose_name_plural = "activities"

    def __str__(self) -> str:
        return f"{self.parent.name} / {self.name}" if self.parent_id else self.name

    def clean(self):
        super().clean()
        if self.parent_id:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "Veikla negali būti sau tėvas."})
            if self.parent.parent_id:
                raise ValidationError({"parent": "Leidžiami tik du lygiai: sritis → veikla."})


class ActivityTranslation(models.Model):
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=10)
    name = models.CharField(max_length=128)
    short_description = models.CharField(max_length=255, blank=True, default="")
    content_json = models.JSONField(null=True, blank=True)
    seo_title = models.CharField(max_length=255, blank=True, default="")
    seo_description = models.CharField(max_length=320, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["activity", "language_code"], name="uniq_activity_translation_lang"
            )
        ]


class ActivityRule(models.Model):
    """Skirtingos sąlygos – AND; sąraše (kategorijos, gamintojai) – OR.

    Tuščia sąlyga netaikoma. Taisyklė be jokios teigiamos sąlygos nieko
    nepriskiria (apsauga nuo „visos prekės“).
    """

    class MatcherType(models.TextChoices):
        CONTAINS = "contains", "Contains"
        REGEX = "regex", "Regex"

    name = models.CharField(max_length=128)
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="rules")
    is_active = models.BooleanField(default=True)

    categories = models.ManyToManyField("Category", blank=True, related_name="+")
    include_descendants = models.BooleanField(default=True)
    exclude_categories = models.ManyToManyField(
        "Category", blank=True, related_name="+",
        help_text="Šių kategorijų (su vaikais) prekės nepriskiriamos.",
    )
    brands = models.ManyToManyField("Brand", blank=True, related_name="+")
    product_group = models.ForeignKey(
        "ProductGroup", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    sku_prefix = models.CharField(max_length=32, blank=True, default="")
    feature = models.ForeignKey(
        "Feature", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    feature_value = models.ForeignKey(
        "FeatureValue", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    matcher_type = models.CharField(
        max_length=16, choices=MatcherType.choices, default=MatcherType.CONTAINS
    )
    pattern = models.CharField(
        max_length=255, blank=True, default="",
        help_text="contains: kelios frazės per „|“ (OR); regex – Python regex.",
    )
    match_in_name = models.BooleanField(default=True)
    match_in_description = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("activity__code", "id")

    def __str__(self) -> str:
        return f"{self.name} → {self.activity.code}"


class ProductActivity(models.Model):
    class Source(models.TextChoices):
        RULE = "rule", "Rule"
        MANUAL = "manual", "Manual"
        EXCLUDE = "exclude", "Exclude"

    product = models.ForeignKey("Product", on_delete=models.CASCADE, related_name="activity_links")
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="product_links")
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.MANUAL)
    rule = models.ForeignKey(
        ActivityRule, null=True, blank=True, on_delete=models.CASCADE, related_name="assignments"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "product activities"
        constraints = [
            models.UniqueConstraint(
                fields=["product", "activity", "rule"],
                condition=Q(rule__isnull=False),
                name="uniq_product_activity_rule",
            ),
            models.UniqueConstraint(
                fields=["product", "activity", "source"],
                condition=Q(rule__isnull=True),
                name="uniq_product_activity_source",
            ),
        ]
        indexes = [models.Index(fields=["activity", "product"])]

    def __str__(self) -> str:
        return f"{self.product_id}:{self.activity_id} ({self.source})"
