from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models


class Cart(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="cart",
    )
    # Anonymous carts are stored per Django session.
    session_key = models.CharField(max_length=40, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(~models.Q(user=None) | ~models.Q(session_key="")),
                name="chk_cart_user_or_session",
            ),
            models.UniqueConstraint(
                fields=["session_key"],
                condition=~models.Q(session_key=""),
                name="uniq_cart_session_key",
            ),
        ]

    def __str__(self) -> str:
        if self.user_id:
            return f"cart:user:{self.user_id}"
        if self.session_key:
            return f"cart:session:{self.session_key}"
        return f"cart:{self.id}"


class CartItem(models.Model):
    cart = models.ForeignKey(
        Cart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(
        "catalog.Variant", on_delete=models.PROTECT, related_name="cart_items"
    )
    qty = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "variant"], name="uniq_cart_variant"),
            models.CheckConstraint(check=models.Q(
                qty__gte=1), name="chk_cart_qty_gte_1"),
        ]
        ordering = ["-updated_at", "-id"]

    def __str__(self) -> str:
        return f"cart:{self.cart_id} variant:{self.variant_id} x{self.qty}"


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", "Pending payment"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    class DeliveryStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        LABEL_CREATED = "label_created", "Label created"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        ERROR = "error", "Error"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )

    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.PENDING_PAYMENT)

    # Idempotency: unique per user (so "confirm" can be safely retried)
    idempotency_key = models.CharField(max_length=80, blank=True, default="")

    currency = models.CharField(max_length=3, default="EUR")
    country_code = models.CharField(max_length=2, default="LT")

    # Totals (net/vat/gross)
    items_net = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    items_vat = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    items_gross = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))

    shipping_method = models.CharField(max_length=50, default="lpexpress")

    # Delivery / carrier
    delivery_status = models.CharField(
        max_length=32, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )
    carrier_code = models.CharField(max_length=32, blank=True, default="")
    carrier_shipment_id = models.CharField(
        max_length=80, blank=True, default="")
    tracking_number = models.CharField(max_length=64, blank=True, default="")

    # Shipping label (PDF)
    shipping_label_pdf = models.FileField(
        upload_to="shipping_labels/%Y/%m/", null=True, blank=True
    )
    shipping_label_generated_at = models.DateTimeField(null=True, blank=True)

    # For pickup-point (locker) shipments
    pickup_locker = models.ForeignKey(
        "dpd.DpdLocker",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )
    pickup_point_id = models.CharField(max_length=80, blank=True, default="")
    pickup_point_name = models.CharField(
        max_length=255, blank=True, default="")
    pickup_point_raw = models.JSONField(default=dict, blank=True)
    shipping_net = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    shipping_vat = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    shipping_gross = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # Admin-only manual override (net). If set, shipping_* totals are derived from it.
    shipping_net_manual = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    total_net = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_vat = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_gross = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # Shipping address snapshot (copied from accounts.UserAddress)
    shipping_full_name = models.CharField(
        max_length=200, blank=True, default="")
    shipping_company = models.CharField(max_length=200, blank=True, default="")
    shipping_line1 = models.CharField(max_length=255, blank=True, default="")
    shipping_city = models.CharField(max_length=120, blank=True, default="")
    shipping_postal_code = models.CharField(
        max_length=32, blank=True, default="")
    shipping_country_code = models.CharField(
        max_length=2, blank=True, default="")
    shipping_phone = models.CharField(max_length=32, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["carrier_code", "tracking_number"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_order_idempotency_key_per_user",
            )
        ]
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"order:{self.id} user:{self.user_id} {self.status}"

    def recalculate_totals(self) -> None:
        from checkout.services import get_shipping_net, get_shipping_tax_class, get_vat_rate_for, money_from_net

        currency = self.currency or "EUR"

        items_net = Decimal("0.00")
        items_vat = Decimal("0.00")
        items_gross = Decimal("0.00")

        for ln in self.lines.all():
            # Prefer stored totals; compute if something is missing.
            if ln.total_net is None or ln.total_vat is None or ln.total_gross is None:
                m = money_from_net(
                    currency=currency,
                    unit_net=ln.unit_net,
                    vat_rate=ln.vat_rate,
                    qty=ln.qty,
                )
                items_net += m.net
                items_vat += m.vat
                items_gross += m.gross
            else:
                items_net += ln.total_net
                items_vat += ln.total_vat
                items_gross += ln.total_gross

        self.items_net = items_net
        self.items_vat = items_vat
        self.items_gross = items_gross

        # Shipping
        if self.shipping_net_manual is not None:
            shipping_net = Decimal(self.shipping_net_manual)
        else:
            try:
                shipping_net = get_shipping_net(
                    shipping_method=self.shipping_method,
                    country_code=self.country_code,
                )
            except ValueError:
                shipping_net = Decimal("0.00")

        shipping_tax_class = get_shipping_tax_class()
        if shipping_tax_class and shipping_net:
            vat_rate = get_vat_rate_for(
                country_code=self.country_code, tax_class=shipping_tax_class
            )
        else:
            vat_rate = Decimal("0")

        shipping_money = money_from_net(
            currency=currency,
            unit_net=shipping_net,
            vat_rate=vat_rate,
            qty=1,
        )
        self.shipping_net = shipping_money.net
        self.shipping_vat = shipping_money.vat
        self.shipping_gross = shipping_money.gross

        # Fees (always +)
        fees_net = Decimal("0.00")
        fees_vat = Decimal("0.00")
        fees_gross = Decimal("0.00")
        for f in self.fees.all():
            fees_net += Decimal(f.net)
            fees_vat += Decimal(f.vat)
            fees_gross += Decimal(f.gross)

        # Totals
        self.total_net = self.items_net + self.shipping_net + fees_net
        self.total_vat = self.items_vat + self.shipping_vat + fees_vat
        self.total_gross = self.items_gross + self.shipping_gross + fees_gross


class OrderLine(models.Model):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey(
        "catalog.Variant",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="order_lines",
    )

    sku = models.CharField(max_length=64)
    name = models.CharField(max_length=255)

    unit_net = models.DecimalField(max_digits=12, decimal_places=2)
    vat_rate = models.DecimalField(max_digits=6, decimal_places=5)
    unit_vat = models.DecimalField(max_digits=12, decimal_places=2)
    unit_gross = models.DecimalField(max_digits=12, decimal_places=2)

    qty = models.PositiveIntegerField(default=1)

    total_net = models.DecimalField(max_digits=12, decimal_places=2)
    total_vat = models.DecimalField(max_digits=12, decimal_places=2)
    total_gross = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def save(self, *args, **kwargs):
        from pricing.services import compute_vat, get_vat_rate

        if self.variant:
            if not self.sku:
                self.sku = self.variant.sku
            if not self.name:
                self.name = getattr(self.variant.product, "name", "")

            if self.vat_rate is None and getattr(self.variant.product, "tax_class", None):
                self.vat_rate = get_vat_rate(
                    country_code=self.order.country_code,
                    tax_class=self.variant.product.tax_class,
                )

        if self.vat_rate is None:
            self.vat_rate = Decimal("0")

        breakdown = compute_vat(
            unit_net=Decimal(self.unit_net),
            vat_rate=Decimal(self.vat_rate),
            qty=int(self.qty),
        )
        self.unit_vat = breakdown.unit_vat
        self.unit_gross = breakdown.unit_gross
        self.total_net = breakdown.total_net
        self.total_vat = breakdown.total_vat
        self.total_gross = breakdown.total_gross

        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"order:{self.order_id} {self.sku} x{self.qty}"


class PaymentIntent(models.Model):
    class Provider(models.TextChoices):
        KLIX = "klix", "Klix (Citadele)"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        REDIRECTED = "redirected", "Redirected"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    order = models.OneToOneField(
        Order, on_delete=models.CASCADE, related_name="payment_intent")
    provider = models.CharField(
        max_length=20, choices=Provider.choices, default=Provider.KLIX)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING)

    currency = models.CharField(max_length=3, default="EUR")
    amount_gross = models.DecimalField(max_digits=12, decimal_places=2)

    external_id = models.CharField(max_length=120, blank=True, default="")
    redirect_url = models.URLField(blank=True, default="")

    raw_request = models.JSONField(default=dict, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["provider", "status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"payment:{self.provider}:{self.status} order:{self.order_id}"


class FeeRule(models.Model):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    country_code = models.CharField(max_length=2, blank=True, default="")

    min_items_gross = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    max_items_gross = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    payment_method_code = models.CharField(max_length=50, blank=True, default="")

    amount_net = models.DecimalField(max_digits=12, decimal_places=2)
    tax_class = models.ForeignKey(
        "catalog.TaxClass",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fee_rules",
    )

    class Meta:
        ordering = ["sort_order", "code"]
        indexes = [
            models.Index(fields=["is_active", "country_code", "sort_order"]),
            models.Index(fields=["payment_method_code"]),
        ]

    def __str__(self) -> str:
        return self.code


class OrderFee(models.Model):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="fees"
    )
    rule = models.ForeignKey(
        FeeRule,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="applied_fees",
    )

    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=200)

    net = models.DecimalField(max_digits=12, decimal_places=2)
    vat_rate = models.DecimalField(max_digits=6, decimal_places=5, default=Decimal("0"))
    vat = models.DecimalField(max_digits=12, decimal_places=2)
    gross = models.DecimalField(max_digits=12, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["order", "code"]),
        ]

    def __str__(self) -> str:
        return f"order:{self.order_id} fee:{self.code}"


class OrderConsent(models.Model):
    class Kind(models.TextChoices):
        TERMS = "terms", "Terms of sale"
        PRIVACY = "privacy", "Privacy notice"

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="consents")

    kind = models.CharField(max_length=32, choices=Kind.choices)
    document_version = models.CharField(max_length=80)

    accepted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "kind"], name="uniq_order_consent_kind"
            )
        ]
        indexes = [
            models.Index(fields=["kind", "-accepted_at"]),
        ]
        ordering = ["id"]

    def __str__(self) -> str:
        return f"order:{self.order_id} consent:{self.kind}@{self.document_version}"
