from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction


def _normalized_language_codes() -> set[str]:
    out: set[str] = set()
    for code, _name in getattr(settings, "LANGUAGES", []):
        c = (code or "").split("-")[0].strip().lower()
        if c:
            out.add(c)
    if not out:
        fallback = (getattr(settings, "LANGUAGE_CODE", "lt") or "lt").split("-")[0].strip().lower()
        if fallback:
            out.add(fallback)
    return out


def _normalize_domain(v: str) -> str:
    return (v or "").strip().lower()


def _normalized_domain_list(csv_value: str) -> list[str]:
    out: list[str] = []
    for raw in (csv_value or "").split(","):
        d = _normalize_domain(raw)
        if d and d not in out:
            out.append(d)
    return out


class Shop(models.Model):
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    primary_domain = models.CharField(max_length=255, unique=True)
    additional_domains = models.TextField(
        blank=True,
        default="",
        help_text="Comma-separated domains mapped to this shop.",
    )

    default_language_code = models.CharField(max_length=8, default="lt")
    default_country_code = models.CharField(max_length=2, default="LT")
    timezone = models.CharField(max_length=64, default="Europe/Vilnius")
    currency = models.CharField(max_length=3, default="EUR")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        indexes = [
            models.Index(fields=["is_active", "code"]),
            models.Index(fields=["is_active", "primary_domain"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} ({self.primary_domain})"

    @property
    def all_domains(self) -> list[str]:
        extra = _normalized_domain_list(self.additional_domains)
        primary = _normalize_domain(self.primary_domain)
        out = [primary] if primary else []
        for d in extra:
            if d not in out:
                out.append(d)
        return out

    def clean(self):
        supported = _normalized_language_codes()
        self.default_language_code = (self.default_language_code or "").split("-")[0].strip().lower()
        self.default_country_code = (self.default_country_code or "").strip().upper()
        self.primary_domain = _normalize_domain(self.primary_domain)

        if not self.primary_domain:
            raise ValidationError({"primary_domain": "Primary domain is required."})
        if self.default_language_code not in supported:
            raise ValidationError({"default_language_code": f"Unsupported language code: {self.default_language_code}"})
        if len(self.default_country_code) != 2:
            raise ValidationError({"default_country_code": "Country code must be ISO-2."})
        if self.currency:
            self.currency = self.currency.strip().upper()
            if len(self.currency) != 3:
                raise ValidationError({"currency": "Currency must be ISO-3."})

        domains = self.all_domains
        if len(domains) != len(set(domains)):
            raise ValidationError({"additional_domains": "Duplicate domains are not allowed."})

        q = Shop.objects.exclude(id=self.id)
        conflict = q.filter(primary_domain=self.primary_domain).first()
        if conflict:
            raise ValidationError({"primary_domain": "Domain already used by another shop."})

        # Exact-match conflict check for additional domains (CSV list).
        if domains[1:]:
            for other in q.only("id", "primary_domain", "additional_domains"):
                other_domains = {_normalize_domain(other.primary_domain)}
                other_domains.update(_normalized_domain_list(other.additional_domains))
                for d in domains[1:]:
                    if d in other_domains:
                        raise ValidationError({"additional_domains": f"Domain already used: {d}"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def enabled_language_codes(self) -> list[str]:
        rows = list(
            self.languages.filter(is_active=True).order_by(
                "-is_default", "sort_order", "id"
            )
        )
        out: list[str] = []
        seen: set[str] = set()
        for row in rows:
            c = (row.language_code or "").strip().lower()
            if not c or c in seen:
                continue
            out.append(c)
            seen.add(c)
        if not out and self.default_language_code:
            out = [self.default_language_code]
        return out


class ShopLanguage(models.Model):
    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="languages",
    )
    language_code = models.CharField(max_length=8)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["shop__code", "-is_default", "sort_order", "language_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "language_code"],
                name="uniq_shop_language",
            ),
            models.UniqueConstraint(
                fields=["shop"],
                condition=models.Q(is_default=True),
                name="uniq_shop_default_language",
            ),
        ]
        indexes = [
            models.Index(fields=["shop", "is_active", "sort_order"]),
        ]

    def __str__(self) -> str:
        return f"{self.shop.code}:{self.language_code}"

    def clean(self):
        supported = _normalized_language_codes()
        self.language_code = (self.language_code or "").split("-")[0].strip().lower()
        if self.language_code not in supported:
            raise ValidationError({"language_code": f"Unsupported language code: {self.language_code}"})
        if self.is_default and not self.is_active:
            raise ValidationError({"is_default": "Default language must be active."})

    def save(self, *args, **kwargs):
        self.full_clean()
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.is_default:
                ShopLanguage.objects.filter(shop=self.shop).exclude(id=self.id).update(is_default=False)
                if self.shop.default_language_code != self.language_code:
                    self.shop.default_language_code = self.language_code
                    self.shop.save(update_fields=["default_language_code", "updated_at"])


class ShopConfig(models.Model):
    shop = models.OneToOneField(Shop, on_delete=models.CASCADE, related_name="config")

    site_name = models.CharField(max_length=200, blank=True, default="")
    logo = models.ImageField(upload_to="shops/%Y/%m/", null=True, blank=True)
    default_title = models.CharField(max_length=255, blank=True, default="")
    default_description = models.TextField(blank=True, default="")

    terms_url = models.CharField(max_length=255, blank=True, default="")
    privacy_url = models.CharField(max_length=255, blank=True, default="")
    terms_version = models.CharField(max_length=50, blank=True, default="")
    privacy_version = models.CharField(max_length=50, blank=True, default="")

    enable_checkout = models.BooleanField(default=True)
    enable_catalog = models.BooleanField(default=True)
    metadata = models.JSONField(blank=True, default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["shop__code"]

    def __str__(self) -> str:
        return f"{self.shop.code} config"


class ShopEmailConfig(models.Model):
    shop = models.OneToOneField(Shop, on_delete=models.CASCADE, related_name="email_config")

    is_active = models.BooleanField(default=True)
    from_email = models.CharField(max_length=255, blank=True, default="")
    reply_to = models.CharField(max_length=255, blank=True, default="")
    smtp_host = models.CharField(max_length=255, blank=True, default="")
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_user = models.CharField(max_length=255, blank=True, default="")
    smtp_password = models.CharField(max_length=255, blank=True, default="")
    smtp_use_tls = models.BooleanField(default=True)
    smtp_use_ssl = models.BooleanField(default=False)
    smtp_timeout = models.PositiveIntegerField(default=10)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["shop__code"]

    def __str__(self) -> str:
        return f"{self.shop.code} email"


def get_default_shop() -> Shop | None:
    s = Shop.objects.filter(is_active=True, code="default").first()
    if s is not None:
        return s
    return Shop.objects.filter(is_active=True).order_by("code", "id").first()


def get_default_shop_id() -> int | None:
    s = get_default_shop()
    return int(s.id) if s else None
