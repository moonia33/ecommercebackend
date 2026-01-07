from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.conf import settings
from django.utils import timezone
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email: str, password: str | None = None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(email=email, password=password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    date_joined = models.DateTimeField(auto_now_add=True)

    # Business segments (not Django permissions groups)
    customer_groups = models.ManyToManyField(
        "CustomerGroup",
        related_name="users",
        blank=True,
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    def __str__(self) -> str:
        return self.email

    def get_active_customer_groups(self):
        return self.customer_groups.filter(is_active=True)

    def get_primary_customer_group(self):
        """Returns the highest priority active group, or None."""
        return self.get_active_customer_groups().order_by("-priority", "code").first()


class CustomerGroup(models.Model):
    class PricingType(models.TextChoices):
        RETAIL = "retail", "Retail"
        WHOLESALE = "wholesale", "Wholesale"

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=150)
    priority = models.IntegerField(default=0, help_text="Higher wins")
    pricing_type = models.CharField(
        max_length=16, choices=PricingType.choices, default=PricingType.RETAIL
    )
    allow_additional_discounts = models.BooleanField(
        default=True,
        help_text="If false, later discount engine should ignore other promotions/coupons for this group.",
    )
    allow_coupons = models.BooleanField(
        default=True,
        help_text="If false, later discount engine should ignore coupon codes for this group.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-priority", "code"]

    def __str__(self) -> str:
        return f"{self.code} ({self.priority})"


class ConsentType(models.Model):
    key = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    version = models.CharField(max_length=40, blank=True)
    is_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "key"]

    def __str__(self) -> str:
        return self.key


class UserConsent(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="consents"
    )
    consent_type = models.ForeignKey(
        ConsentType, on_delete=models.CASCADE, related_name="user_consents"
    )
    accepted = models.BooleanField(default=False)
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    source = models.CharField(max_length=50, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "consent_type")
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.consent_type.key}={self.accepted}"

    def set_status(self, accepted: bool, *, source: str = "") -> None:
        self.accepted = bool(accepted)
        self.source = source
        if self.accepted:
            self.accepted_at = timezone.now()
            self.revoked_at = None
        else:
            self.revoked_at = timezone.now()
        self.save(
            update_fields=[
                "accepted",
                "accepted_at",
                "revoked_at",
                "source",
                "updated_at",
            ]
        )


class EmailOTP(models.Model):
    email = models.EmailField(db_index=True)
    code_hash = models.CharField(max_length=256)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)

    attempts = models.PositiveIntegerField(default=0)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["email", "expires_at"]),
        ]

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    def mark_used(self) -> None:
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    @classmethod
    def new_expires_at(cls, ttl_minutes: int) -> timezone.datetime:
        return timezone.now() + timedelta(minutes=ttl_minutes)


class UserPhone(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="phones"
    )
    phone = models.CharField(
        max_length=32, help_text="Prefer E.164 format, e.g. +3706... ")
    label = models.CharField(max_length=50, blank=True)

    is_primary = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "phone"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "phone"],
                name="uniq_user_phone",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_primary=True),
                name="uniq_primary_phone_per_user",
            ),
        ]
        ordering = ["-is_primary", "phone"]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.phone}"


class UserAddress(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="addresses"
    )

    label = models.CharField(max_length=50, blank=True)
    full_name = models.CharField(max_length=200, blank=True)
    company = models.CharField(max_length=200, blank=True)
    company_reg_no = models.CharField(max_length=64, blank=True)
    company_vat_no = models.CharField(max_length=64, blank=True)

    line1 = models.CharField(max_length=255)
    city = models.CharField(max_length=120)
    postal_code = models.CharField(max_length=32)
    country_code = models.CharField(max_length=2, default="LT")

    phone = models.CharField(max_length=32, blank=True)

    is_default_shipping = models.BooleanField(default=False)
    is_default_billing = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "country_code"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_default_shipping=True),
                name="uniq_default_shipping_per_user",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_default_billing=True),
                name="uniq_default_billing_per_user",
            ),
        ]
        ordering = ["-is_default_shipping",
                    "-is_default_billing", "-updated_at"]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.country_code}:{self.city}"
