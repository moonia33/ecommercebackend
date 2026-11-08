from __future__ import annotations

import hashlib

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from api.i18n import get_supported_language_codes, validate_supported_language_code


class LegalDocument(models.Model):
    """A legal text (terms, privacy policy, ...) whose wording is versioned.

    Public URL is `/info/<slug>`; the text itself lives in
    `LegalDocumentVersion` rows, which become immutable once in force.
    """

    class Kind(models.TextChoices):
        TERMS = "terms", "Pirkimo taisyklės"
        PRIVACY = "privacy", "Privatumo politika"
        COOKIES = "cookies", "Slapukų politika"
        RETURNS = "returns", "Grąžinimo sąlygos"
        OTHER = "other", "Kita"

    shop = models.ForeignKey(
        "api.Shop", null=True, blank=True, on_delete=models.PROTECT,
        related_name="legal_documents",
        help_text="Tuščia = bendras visoms parduotuvėms (parduotuvės dokumentas jį perrašo).")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    slug = models.SlugField(
        max_length=200, help_text="Viešas adresas /info/<slug>, pvz. taisykles.")
    required_at_checkout = models.BooleanField(
        default=False, help_text="Pirkėjas turi sutikti su galiojančia versija užsakymo metu.")
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "kind"]
        verbose_name = "Teisinis dokumentas"
        verbose_name_plural = "Teisiniai dokumentai"
        constraints = [
            models.UniqueConstraint(fields=["shop", "kind"], name="uniq_legaldoc_shop_kind"),
            models.UniqueConstraint(fields=["shop", "slug"], name="uniq_legaldoc_shop_slug"),
            models.UniqueConstraint(
                fields=["kind"], condition=models.Q(shop__isnull=True),
                name="uniq_legaldoc_global_kind"),
            models.UniqueConstraint(
                fields=["slug"], condition=models.Q(shop__isnull=True),
                name="uniq_legaldoc_global_slug"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} ({self.shop.code if self.shop_id else 'visos'})"


class LegalDocumentVersion(models.Model):
    """One wording of a document.

    Locked (no edits, no delete) once published and in force — accepted
    consents point at it, so the text a customer agreed to never changes.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Juodraštis"
        PUBLISHED = "published", "Publikuota"

    document = models.ForeignKey(
        LegalDocument, on_delete=models.PROTECT, related_name="versions")
    version = models.CharField(
        max_length=80, blank=True,
        help_text="Tuščia = įsigaliojimo data (pvz. 2026-11-08).")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    effective_from = models.DateTimeField(
        default=timezone.now, help_text="Nuo kada galioja ši redakcija.")
    published_at = models.DateTimeField(null=True, blank=True)
    requires_reconsent = models.BooleanField(
        default=False,
        help_text="Esminis pakeitimas: anksčiau sutikusių vartotojų bus paprašyta sutikti iš naujo.")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from", "-id"]
        verbose_name = "Dokumento versija"
        verbose_name_plural = "Dokumentų versijos"
        constraints = [
            models.UniqueConstraint(fields=["document", "version"], name="uniq_legalver_doc_version"),
        ]
        indexes = [models.Index(fields=["document", "status", "-effective_from"])]

    def __str__(self) -> str:
        return f"{self.document} @ {self.version}"

    @property
    def is_locked(self) -> bool:
        return (
            self.status == self.Status.PUBLISHED
            and self.effective_from is not None
            and self.effective_from <= timezone.now()
        )

    def _locked_in_db(self) -> bool:
        if not self.pk:
            return False
        prev = type(self).objects.filter(pk=self.pk).first()
        return bool(prev and prev.is_locked)

    def _default_version_label(self) -> str:
        base = timezone.localtime(self.effective_from or timezone.now()).date().isoformat()
        label, n = base, 2
        qs = type(self).objects.filter(document_id=self.document_id)
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        while qs.filter(version=label).exists():
            label = f"{base}-{n}"
            n += 1
        return label

    def save(self, *args, **kwargs):
        if self._locked_in_db():
            raise ValidationError("Galiojanti publikuota versija nekeičiama — sukurkite naują versiją.")
        self.version = (self.version or "").strip()
        if not self.version:
            self.version = self._default_version_label()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._locked_in_db():
            raise ValidationError("Galiojanti publikuota versija netrinama.")
        return super().delete(*args, **kwargs)

    def publish(self) -> None:
        if self.status == self.Status.PUBLISHED:
            return
        if not self.translations.exclude(body_markdown="").exists():
            raise ValidationError("Versija be teksto negali būti publikuota.")
        now = timezone.now()
        self.status = self.Status.PUBLISHED
        self.published_at = now
        # A version never takes effect retroactively.
        if not self.effective_from or self.effective_from < now:
            self.effective_from = now
        self.save()


class LegalDocumentVersionTranslation(models.Model):
    version = models.ForeignKey(
        LegalDocumentVersion, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=8)
    title = models.CharField(max_length=255)
    body_markdown = models.TextField(blank=True, default="")
    change_summary = models.TextField(
        blank=True, default="",
        help_text="Trumpai, kas pasikeitė — rodoma prašant sutikti iš naujo.")
    content_hash = models.CharField(max_length=64, blank=True, default="", editable=False)

    class Meta:
        unique_together = ("version", "language_code")
        verbose_name = "Vertimas"
        verbose_name_plural = "Vertimai"

    def __str__(self) -> str:
        return f"{self.version} [{self.language_code}]"

    def clean(self):
        super().clean()
        try:
            self.language_code = validate_supported_language_code(self.language_code)
        except ValueError:
            allowed = ", ".join(sorted(get_supported_language_codes()))
            raise ValidationError({"language_code": f"Leidžiami: {allowed}"})

    def _version_locked(self) -> bool:
        v = LegalDocumentVersion.objects.filter(pk=self.version_id).first()
        return bool(v and v.is_locked)

    def save(self, *args, **kwargs):
        if self._version_locked():
            raise ValidationError("Galiojančios versijos tekstas nekeičiamas.")
        self.content_hash = hashlib.sha256(
            f"{self.title}\n{self.body_markdown}".encode()).hexdigest()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._version_locked():
            raise ValidationError("Galiojančios versijos tekstas netrinamas.")
        return super().delete(*args, **kwargs)


class LegalAcceptance(models.Model):
    """Append-only proof that a user accepted a specific version."""

    class Source(models.TextChoices):
        CHECKOUT = "checkout", "Užsakymas"
        RECONSENT = "reconsent", "Pakartotinis sutikimas"
        ACCOUNT = "account", "Paskyra"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="legal_acceptances")
    version = models.ForeignKey(
        LegalDocumentVersion, on_delete=models.PROTECT, related_name="acceptances")
    order = models.ForeignKey(
        "checkout.Order", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="legal_acceptances")
    source = models.CharField(max_length=16, choices=Source.choices)
    language_code = models.CharField(max_length=8, blank=True, default="")
    accepted_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-accepted_at", "-id"]
        verbose_name = "Sutikimas"
        verbose_name_plural = "Sutikimai"
        indexes = [models.Index(fields=["user", "version"])]

    def __str__(self) -> str:
        return f"{self.user_id} → {self.version}"
