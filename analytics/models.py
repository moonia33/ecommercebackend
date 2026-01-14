from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class AnalyticsEvent(models.Model):
    class Name(models.TextChoices):
        PRODUCT_VIEW = "product_view", "Product view"
        ADD_TO_CART = "add_to_cart", "Add to cart"
        REMOVE_FROM_CART = "remove_from_cart", "Remove from cart"
        VIEW_CART = "view_cart", "View cart"
        BEGIN_CHECKOUT = "begin_checkout", "Begin checkout"
        PURCHASE = "purchase", "Purchase"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    site = models.ForeignKey(
        "api.Site",
        on_delete=models.PROTECT,
        related_name="analytics_events",
    )

    name = models.CharField(max_length=64, choices=Name.choices)
    occurred_at = models.DateTimeField()

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    visitor_id = models.CharField(max_length=64, blank=True, default="")

    object_type = models.CharField(max_length=64, blank=True, default="")
    object_id = models.BigIntegerField(null=True, blank=True)

    country_code = models.CharField(max_length=2, blank=True, default="")
    channel = models.CharField(max_length=32, blank=True, default="")
    language_code = models.CharField(max_length=8, blank=True, default="")

    payload = models.JSONField(blank=True, default=dict)

    idempotency_key = models.CharField(max_length=64, unique=True)

    class Meta:
        indexes = [
            models.Index(fields=["name", "occurred_at"]),
            models.Index(fields=["user", "occurred_at"]),
            models.Index(fields=["visitor_id", "occurred_at"]),
            models.Index(fields=["object_type", "object_id"]),
        ]


class VisitorLink(models.Model):
    site = models.ForeignKey(
        "api.Site",
        on_delete=models.PROTECT,
        related_name="visitor_links",
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    visitor_id = models.CharField(max_length=64)

    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("site", "user", "visitor_id")
        indexes = [models.Index(fields=["visitor_id"])]


class AnalyticsOutbox(models.Model):
    class Provider(models.TextChoices):
        NEWSMAN = "newsman", "Newsman"
        FACEBOOK = "facebook", "Facebook"
        GOOGLE = "google", "Google"

    event = models.ForeignKey(AnalyticsEvent, on_delete=models.CASCADE, related_name="outbox")
    provider = models.CharField(max_length=32, choices=Provider.choices)

    status = models.CharField(max_length=32, default="pending")
    attempts = models.IntegerField(default=0)
    last_error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("event", "provider")
        indexes = [models.Index(fields=["provider", "status", "created_at"]) ]


class RecentlyViewedProduct(models.Model):
    site = models.ForeignKey(
        "api.Site",
        on_delete=models.PROTECT,
        related_name="recently_viewed_products",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="recently_viewed_products",
    )
    visitor_id = models.CharField(max_length=64, blank=True, default="")

    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="recently_viewed_by",
    )

    last_viewed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "user", "product"],
                condition=models.Q(user__isnull=False),
                name="analytics_rvp_unique_user_product",
            ),
            models.UniqueConstraint(
                fields=["site", "visitor_id", "product"],
                condition=~models.Q(visitor_id=""),
                name="analytics_rvp_unique_visitor_product",
            ),
        ]
        indexes = [
            models.Index(fields=["site", "user", "last_viewed_at"]),
            models.Index(fields=["site", "visitor_id", "last_viewed_at"]),
            models.Index(fields=["product", "last_viewed_at"]),
        ]
