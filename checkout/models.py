from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.files.storage import storages
from django.db import models


def _private_label_storage():
    """Labels/invoices never go to the public CDN bucket."""
    try:
        return storages["private"]
    except Exception:
        return storages["default"]


class Cart(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="carts",
    )
    shop = models.ForeignKey(
        "api.Shop",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="carts",
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
                fields=["user", "shop"],
                condition=models.Q(user__isnull=False),
                name="uniq_cart_user_shop",
            ),
            models.UniqueConstraint(
                fields=["session_key", "shop"],
                condition=~models.Q(session_key=""),
                name="uniq_cart_session_shop_key",
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
    offer = models.ForeignKey(
        "catalog.InventoryItem",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cart_items",
    )
    qty = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "variant"],
                condition=models.Q(offer__isnull=True),
                name="uniq_cart_variant_when_offer_null",
            ),
            models.UniqueConstraint(
                fields=["cart", "offer"],
                condition=models.Q(offer__isnull=False),
                name="uniq_cart_offer_when_offer_present",
            ),
            models.CheckConstraint(check=models.Q(
                qty__gte=1), name="chk_cart_qty_gte_1"),
        ]
        ordering = ["-updated_at", "-id"]

    def __str__(self) -> str:
        return f"cart:{self.cart_id} variant:{self.variant_id} x{self.qty}"


class Favorite(models.Model):
    """Mėgstamos prekės (wishlist): user arba svečio Django session,
    tas pats merge-on-login pattern'as kaip Cart."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="favorites",
    )
    session_key = models.CharField(max_length=40, blank=True, default="")
    shop = models.ForeignKey(
        "api.Shop",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="favorites",
    )
    variant = models.ForeignKey(
        "catalog.Variant", on_delete=models.CASCADE, related_name="favorites"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                check=(~models.Q(user=None) | ~models.Q(session_key="")),
                name="chk_favorite_user_or_session",
            ),
            models.UniqueConstraint(
                fields=["user", "shop", "variant"],
                condition=models.Q(user__isnull=False),
                name="uniq_favorite_user_shop_variant",
            ),
            models.UniqueConstraint(
                fields=["session_key", "shop", "variant"],
                condition=(models.Q(user__isnull=True) & ~models.Q(session_key="")),
                name="uniq_favorite_session_shop_variant",
            ),
        ]

    def __str__(self) -> str:
        who = f"user:{self.user_id}" if self.user_id else f"session:{self.session_key}"
        return f"fav:{who} variant:{self.variant_id}"


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", "Pending payment"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    class FulfillmentMode(models.TextChoices):
        STOCK = "stock", "Stock"
        DROPSHIP = "dropship", "Dropship"  # legacy — remapped to REMOTE
        REMOTE = "remote", "Remote warehouse"
        MIXED = "mixed", "Mixed"

    class SupplierReservationStatus(models.TextChoices):
        NOT_NEEDED = "not_needed", "Not needed"
        PENDING = "pending", "Pending"
        RESERVED = "reserved", "Reserved"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class DeliveryStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        AWAITING_STOCK = "awaiting_stock", "Awaiting stock"
        PACKING = "packing", "Packing"
        PACKED = "packed", "Packed, awaiting pickup"
        PICKED_UP = "picked_up", "Picked up by carrier"
        IN_TRANSIT = "in_transit", "In transit"
        AT_PICKUP_POINT = "at_pickup_point", "At pickup point"
        DELIVERED = "delivered", "Delivered"
        RETURNING = "returning", "Returning to sender"
        RETURNED = "returned", "Returned to sender"
        CANCELLED = "cancelled", "Cancelled"
        ERROR = "error", "Error"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    shop = models.ForeignKey(
        "api.Shop",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="orders",
    )

    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.PENDING_PAYMENT)

    fulfillment_mode = models.CharField(
        max_length=20,
        choices=FulfillmentMode.choices,
        default=FulfillmentMode.STOCK,
    )
    supplier_reservation_status = models.CharField(
        max_length=20,
        choices=SupplierReservationStatus.choices,
        default=SupplierReservationStatus.NOT_NEEDED,
    )
    supplier_reserved_at = models.DateTimeField(null=True, blank=True)
    supplier_reference = models.CharField(max_length=120, blank=True, default="")
    supplier_notes = models.TextField(blank=True, default="")

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

    shipping_method = models.CharField(max_length=50, default="unisend_pickup")

    # Delivery / carrier
    delivery_status = models.CharField(
        max_length=32, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )
    # Set by signal when delivery_status first reaches DELIVERED
    # (courier / locker pickup / store pickup) — return-window anchor
    # and sales-invoice trigger.
    delivered_at = models.DateTimeField(null=True, blank=True)

    # Delivery ETA snapshot (order-level, aggregated)
    delivery_min_date = models.DateField(null=True, blank=True)
    delivery_max_date = models.DateField(null=True, blank=True)
    delivery_eta_kind = models.CharField(max_length=20, blank=True, default="")
    delivery_eta_rule_code = models.CharField(max_length=80, blank=True, default="")
    delivery_eta_source = models.CharField(max_length=120, blank=True, default="")
    carrier_code = models.CharField(max_length=32, blank=True, default="")
    carrier_shipment_id = models.CharField(
        max_length=80, blank=True, default="")
    tracking_number = models.CharField(max_length=64, blank=True, default="")

    # Shipping label (PDF) — private bucket: labels contain personal data.
    shipping_label_pdf = models.FileField(
        upload_to="shipping_labels/%Y/%m/",
        storage=_private_label_storage,
        null=True, blank=True
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

    # Dovanų kortelės gavėjas: jei pirkėjas nori, kad kodas iškeliautų
    # tiesiai gavėjui (dovana), kitaip — pirkėjo el. paštas.
    gift_recipient_email = models.EmailField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["shop", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["carrier_code", "tracking_number"]),
            models.Index(fields=["fulfillment_mode", "supplier_reservation_status", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "shop", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_order_idempotency_key_per_user_shop",
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

        # Discounts (always -)
        discounts_net = Decimal("0.00")
        discounts_vat = Decimal("0.00")
        discounts_gross = Decimal("0.00")
        for d in self.discounts.all():
            discounts_net += Decimal(d.net)
            discounts_vat += Decimal(d.vat)
            discounts_gross += Decimal(d.gross)

        # Totals
        self.total_net = self.items_net + self.shipping_net + fees_net - discounts_net
        self.total_vat = self.items_vat + self.shipping_vat + fees_vat - discounts_vat
        self.total_gross = self.items_gross + self.shipping_gross + fees_gross - discounts_gross


class OrderEvent(models.Model):
    class Kind(models.TextChoices):
        FULFILLMENT = "fulfillment", "Fulfillment"
        DELIVERY = "delivery", "Delivery"
        PAYMENT = "payment", "Payment"
        NOTE = "note", "Note"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.NOTE)
    name = models.CharField(max_length=80, blank=True, default="")

    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="order_events",
    )
    actor_label = models.CharField(max_length=200, blank=True, default="")

    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["order", "-created_at"]),
            models.Index(fields=["kind", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"order:{self.order_id} event:{self.kind}:{self.name}"


class OpsJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    class JobType(models.TextChoices):
        SHIPMENT_LABELS_BULK = "shipment_labels_bulk", "Shipment labels bulk"
        ORDERS_BULK_ACTION = "orders_bulk_action", "Orders bulk action"

    job_type = models.CharField(max_length=40, choices=JobType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)

    shop = models.ForeignKey(
        "api.Shop",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ops_jobs",
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ops_jobs",
    )
    request_payload = models.JSONField(default=dict, blank=True)
    result_payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["job_type", "-created_at"]),
            models.Index(fields=["shop", "status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"opsjob:{self.id}:{self.job_type}:{self.status}"


class OrderDiscount(models.Model):
    class Kind(models.TextChoices):
        COUPON = "coupon", "Coupon"
        PROMO = "promo", "Promo"
        MANUAL = "manual", "Manual"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="discounts")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.COUPON)

    code = models.CharField(max_length=80, blank=True, default="")
    name = models.CharField(max_length=200, blank=True, default="")

    net = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    vat = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    gross = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["order", "kind"]),
            models.Index(fields=["kind", "code"]),
        ]

    def __str__(self) -> str:
        return f"order:{self.order_id} discount:{self.kind}:{self.code}"


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

    offer = models.ForeignKey(
        "catalog.InventoryItem",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
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
            if self.unit_net in (None, ""):
                self.unit_net = getattr(self.variant, "price_eur", Decimal("0"))

            order_country_code = ""
            if self.order_id:
                try:
                    order_country_code = (self.order.country_code or "").strip().upper()
                except Order.DoesNotExist:
                    order_country_code = ""

            if (
                self.vat_rate is None
                and order_country_code
                and getattr(self.variant.product, "tax_class", None)
            ):
                try:
                    self.vat_rate = get_vat_rate(
                        country_code=order_country_code,
                        tax_class=self.variant.product.tax_class,
                    )
                except Exception:
                    self.vat_rate = Decimal("0")

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


class InventoryAllocation(models.Model):
    class Status(models.TextChoices):
        RESERVED = "reserved", "Reserved"
        CAPTURED = "captured", "Captured"
        RELEASED = "released", "Released"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="inventory_allocations")
    order_line = models.ForeignKey(OrderLine, on_delete=models.CASCADE, related_name="inventory_allocations")
    inventory_item = models.ForeignKey(
        "catalog.InventoryItem",
        on_delete=models.PROTECT,
        related_name="allocations",
    )

    qty = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RESERVED)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["order", "status", "-created_at"]),
            models.Index(fields=["inventory_item", "status"]),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(qty__gte=0), name="chk_alloc_qty_gte_0"),
        ]

    def __str__(self) -> str:
        return f"order:{self.order_id} line:{self.order_line_id} inv:{self.inventory_item_id} {self.qty} {self.status}"


class PaymentIntent(models.Model):
    class Provider(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        MONTONIO = "montonio", "Montonio"
        COD = "cod", "Pay on delivery / pickup"
        INBANK = "inbank", "Inbank hire purchase"
        GIFT_CARD = "gift_card", "Gift card"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        REDIRECTED = "redirected", "Redirected"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    order = models.OneToOneField(
        Order, on_delete=models.CASCADE, related_name="payment_intent")
    provider = models.CharField(
        max_length=20, choices=Provider.choices, default=Provider.BANK_TRANSFER)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING)

    currency = models.CharField(max_length=3, default="EUR")
    # Sumą, kurią surenka šis provider'is. Split tender atveju
    # (dovanų kortelė + montonio/bank) čia = LIKUTIS, ne order.total_gross.
    amount_gross = models.DecimalField(max_digits=12, decimal_places=2)
    # Dalyvis sumos, padengtas dovanų kortele (GiftCardTransaction DEBIT'ai
    # — source of truth; čia snapshot'as ataskaitoms/ops UI).
    gift_card_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))

    external_id = models.CharField(max_length=120, blank=True, default="")
    redirect_url = models.URLField(max_length=2048, blank=True, default="")

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


class Shipment(models.Model):
    """A physical parcel belonging to an order (multi-parcel orders supported).

    Source of truth for carrier tracking; Order.tracking_number /
    carrier_code / delivery_status mirror the primary outbound shipment.
    """

    class Kind(models.TextChoices):
        OUTBOUND = "outbound", "Outbound"
        RETURN = "return", "Return to sender"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PACKED = "packed", "Packed, awaiting pickup"
        PICKED_UP = "picked_up", "Picked up by carrier"
        IN_TRANSIT = "in_transit", "In transit"
        AT_PICKUP_POINT = "at_pickup_point", "At pickup point"
        DELIVERED = "delivered", "Delivered"
        RETURNING = "returning", "Returning to sender"
        RETURNED = "returned", "Returned to sender"
        CANCELLED = "cancelled", "Cancelled"
        ERROR = "error", "Error"

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="shipments")
    kind = models.CharField(
        max_length=16, choices=Kind.choices, default=Kind.OUTBOUND)
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.PENDING)

    carrier_code = models.CharField(max_length=32, blank=True, default="")
    shipping_method = models.CharField(max_length=50, blank=True, default="")
    tracking_number = models.CharField(max_length=64, blank=True, default="")
    carrier_shipment_id = models.CharField(max_length=80, blank=True, default="")

    pickup_locker = models.ForeignKey(
        "dpd.DpdLocker", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="shipments")
    pickup_point_id = models.CharField(max_length=80, blank=True, default="")
    pickup_point_name = models.CharField(max_length=255, blank=True, default="")

    shipping_label_pdf = models.FileField(
        upload_to="shipping_labels/%Y/%m/",
        storage=_private_label_storage, null=True, blank=True)
    label_generated_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_shipments")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tracking_number"]),
            models.Index(fields=["order", "kind"]),
            models.Index(fields=["carrier_code", "status"]),
        ]

    def __str__(self) -> str:
        return f"shipment:{self.id} order:{self.order_id} {self.kind} {self.tracking_number or '-'}"

    def apply_to_order_projection(self) -> None:
        """Mirror outbound shipment data onto the order (primary = latest outbound)."""
        order = self.order
        if self.kind != Shipment.Kind.OUTBOUND:
            return
        order.carrier_code = self.carrier_code
        order.carrier_shipment_id = self.carrier_shipment_id
        order.tracking_number = self.tracking_number
        order.delivery_status = aggregate_delivery_status(order)
        order.save(update_fields=[
            "carrier_code", "carrier_shipment_id",
            "tracking_number", "delivery_status", "updated_at",
        ])


# Status progress order used to aggregate an order's delivery_status
# across multiple outbound shipments.
_SHIPMENT_PROGRESS = {
    "pending": 0, "awaiting_stock": 1, "packing": 2, "packed": 3,
    "picked_up": 4, "in_transit": 5, "at_pickup_point": 6,
    "delivered": 7, "returned": 7,
}


def aggregate_delivery_status(order: Order) -> str:
    """Order-level status = worst-case progress of outbound shipments.

    All terminal (delivered/returned) -> delivered if any delivered else returned.
    Any in flight -> the least-advanced in-flight status (split shipments
    surface as the slowest parcel).
    """
    statuses = list(
        order.shipments.filter(kind=Shipment.Kind.OUTBOUND)
        .values_list("status", flat=True)
    )
    if not statuses:
        return order.delivery_status or Order.DeliveryStatus.PENDING
    if all(s in {"delivered", "returned", "cancelled"} for s in statuses):
        if any(s == "delivered" for s in statuses):
            return "delivered"
        if any(s == "returned" for s in statuses):
            return "returned"
        return "cancelled"
    in_flight = [s for s in statuses if s not in {"delivered", "returned", "cancelled"}]
    worst = min(in_flight, key=lambda s: _SHIPMENT_PROGRESS.get(s, 0))
    return worst


class Surcharge(models.Model):
    """Post-order balance adjustment: charge (customer owes) or refund
    (customer is owed). Auto rows are maintained by
    reconcile_order_balance(); method stays empty until the admin
    picks a collection channel."""

    class Direction(models.TextChoices):
        CHARGE = "charge", "Charge"
        REFUND = "refund", "Refund"

    class Source(models.TextChoices):
        AUTO = "auto", "Auto"
        MANUAL = "manual", "Manual"

    class Method(models.TextChoices):
        MONTONIO = "montonio", "Montonio link"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        RESOLVED = "resolved", "Resolved"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="surcharges")
    direction = models.CharField(
        max_length=10, choices=Direction.choices, default=Direction.CHARGE)
    source = models.CharField(
        max_length=10, choices=Source.choices, default=Source.MANUAL)
    reason = models.CharField(max_length=255, blank=True, default="")
    amount_gross = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="EUR")

    method = models.CharField(
        max_length=20, choices=Method.choices, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING)

    payment_url = models.URLField(max_length=2048, blank=True, default="")
    external_id = models.CharField(max_length=120, blank=True, default="")
    raw_response = models.JSONField(default=dict, blank=True)

    emailed_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_surcharges")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["order", "status"])]

    @property
    def reference(self) -> str:
        return f"{self.order_id}-sur-{self.id}"

    def __str__(self) -> str:
        return f"surcharge:{self.id} order:{self.order_id} {self.amount_gross}{self.currency}"


class ReturnRequest(models.Model):
    """RMA: a customer's (or manager-created) return of order items.

    Lifecycle: pending -> approved -> received -> completed (credit issued).
    Rejected/cancelled are terminal. approved -> cancelled allowed while
    nothing was received.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved, awaiting goods"
        REJECTED = "rejected", "Rejected"
        RECEIVED = "received", "Goods received"
        COMPLETED = "completed", "Completed (credited)"
        CANCELLED = "cancelled", "Cancelled"

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="returns")
    number = models.CharField(max_length=40, unique=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING)

    note = models.TextField(blank=True, default="")

    shipment = models.ForeignKey(
        Shipment, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="return_requests",
        help_text="Return shipment (kind=return) carrying these goods.",
    )
    credit_invoice = models.ForeignKey(
        "billing.Invoice", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="return_requests",
        help_text="Latest credit invoice issued for this return.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_returns")

    decided_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["order", "status"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.number} order:{self.order_id} {self.status}"


class ReturnLine(models.Model):
    return_request = models.ForeignKey(
        ReturnRequest, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(
        OrderLine, on_delete=models.PROTECT, related_name="return_lines")

    qty = models.PositiveIntegerField()
    received_qty = models.PositiveIntegerField(default=0)
    credited_qty = models.PositiveIntegerField(default=0)
    reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.return_request_id} line:{self.order_line_id} x{self.qty}"
