from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db import models as dj_models
from django.db.models import F


def reserve_coupon_for_order(*, order_id: int) -> bool:
    from checkout.models import Order
    from promotions.models import Coupon, CouponRedemption

    with transaction.atomic():
        order = Order.objects.select_for_update().filter(id=int(order_id)).first()
        if not order:
            return False

        discount = order.discounts.filter(kind="coupon").order_by("id").first()
        if not discount or not (discount.code or "").strip():
            return False

        # Idempotency: only one redemption/reservation per order.
        if CouponRedemption.objects.filter(order=order).exists():
            return False

        coupon = (
            Coupon.objects.select_for_update()
            .filter(code=(discount.code or "").strip().lower())
            .first()
        )
        if not coupon:
            return False

        # Enforce usage limits at reservation time.
        if coupon.usage_limit_total is not None:
            if int(coupon.times_redeemed) >= int(coupon.usage_limit_total):
                return False

        if coupon.usage_limit_per_user is not None:
            used = CouponRedemption.objects.filter(coupon=coupon, user=order.user).count()
            if int(used) >= int(coupon.usage_limit_per_user):
                return False

        redemption, created = CouponRedemption.objects.get_or_create(
            coupon=coupon,
            order=order,
            defaults={"user": order.user},
        )
        if not created:
            return False

        qs = Coupon.objects.filter(id=coupon.id)
        if coupon.usage_limit_total is not None:
            qs = qs.filter(times_redeemed__lt=int(coupon.usage_limit_total))

        updated = qs.update(times_redeemed=F("times_redeemed") + 1)
        if updated != 1:
            redemption.delete()
            return False

        return True


def release_coupon_for_order(*, order_id: int) -> bool:
    from checkout.models import Order
    from promotions.models import Coupon, CouponRedemption

    with transaction.atomic():
        order = Order.objects.select_for_update().filter(id=int(order_id)).first()
        if not order:
            return False

        redemption = CouponRedemption.objects.select_for_update().filter(order=order).first()
        if not redemption:
            return False

        coupon = Coupon.objects.select_for_update().filter(id=redemption.coupon_id).first()
        if not coupon:
            redemption.delete()
            return True

        redemption.delete()
        Coupon.objects.filter(id=coupon.id, times_redeemed__gt=0).update(times_redeemed=F("times_redeemed") - 1)
        return True


def redeem_coupon_for_paid_order(*, order_id: int) -> bool:
    from checkout.models import Order
    from promotions.models import Coupon, CouponRedemption

    with transaction.atomic():
        order = Order.objects.select_for_update().filter(id=int(order_id)).first()
        if not order or order.status != Order.Status.PAID:
            return False

        discount = order.discounts.filter(kind="coupon").order_by("id").first()
        if not discount or not (discount.code or "").strip():
            return False

        coupon = (
            Coupon.objects.select_for_update()
            .filter(code=(discount.code or "").strip().lower())
            .first()
        )
        if not coupon:
            return False

        # Idempotency: if the order was already redeemed, do nothing.
        if CouponRedemption.objects.filter(order=order).exists():
            return False

        # Global usage limit
        if coupon.usage_limit_total is not None:
            if int(coupon.times_redeemed) >= int(coupon.usage_limit_total):
                return False

        # Per-user usage limit
        if coupon.usage_limit_per_user is not None:
            used = CouponRedemption.objects.filter(coupon=coupon, user=order.user).count()
            if int(used) >= int(coupon.usage_limit_per_user):
                return False

        # Idempotency: only one redemption per order+coupon.
        redemption, created = CouponRedemption.objects.get_or_create(
            coupon=coupon,
            order=order,
            defaults={"user": order.user},
        )
        if not created:
            return False

        # Atomic increment (coupon row is locked, but keep it safe).
        qs = Coupon.objects.filter(id=coupon.id)
        if coupon.usage_limit_total is not None:
            qs = qs.filter(times_redeemed__lt=int(coupon.usage_limit_total))

        updated = qs.update(times_redeemed=F("times_redeemed") + 1)
        if updated != 1:
            # Roll back this redemption if we couldn't increment (limit reached race).
            redemption.delete()
            return False

        return True


def find_best_promo_rule(
    *,
    channel: str,
    category_id: int | None,
    brand_id: int | None,
    product_id: int | None,
    variant_id: int | None,
    customer_group_id: int | None,
):
    from django.utils import timezone

    from promotions.models import PromoRule

    now = timezone.now()
    ch = (channel or "").strip().lower() or "normal"

    qs = (
        PromoRule.objects.filter(is_active=True)
        .prefetch_related(
            "customer_groups",
            "channels",
            "condition_groups",
            "condition_groups__conditions",
        )
        .order_by("-priority", "id")
    )
    qs = qs.filter(dj_models.Q(start_at__isnull=True) | dj_models.Q(start_at__lte=now))
    qs = qs.filter(dj_models.Q(end_at__isnull=True) | dj_models.Q(end_at__gte=now))

    candidates = [r for r in qs if r.allows_channel(channel=ch)]

    if customer_group_id is None:
        candidates = [r for r in candidates if len(list(r.customer_groups.all())) == 0]
    else:
        cg_id = int(customer_group_id)
        candidates = [
            r
            for r in candidates
            if (
                len(list(r.customer_groups.all())) == 0
                or any(int(g.id) == cg_id for g in r.customer_groups.all())
            )
        ]

    def _legacy_scope_matches(r: PromoRule) -> bool:
        if r.scope == PromoRule.Scope.ALL:
            return True
        if r.scope == PromoRule.Scope.CATEGORY:
            return bool(r.category_id) and r.category_id == int(category_id or 0)
        if r.scope == PromoRule.Scope.BRAND:
            return bool(r.brand_id) and r.brand_id == int(brand_id or 0)
        if r.scope == PromoRule.Scope.PRODUCT:
            return bool(r.product_id) and r.product_id == int(product_id or 0)
        if r.scope == PromoRule.Scope.VARIANT:
            return bool(r.variant_id) and r.variant_id == int(variant_id or 0)
        return False

    def _condition_groups_match(r: PromoRule) -> bool:
        groups = list(getattr(r, "condition_groups", []).all())
        if not groups:
            return False

        ctx = {
            "category_id": int(category_id) if category_id is not None else None,
            "brand_id": int(brand_id) if brand_id is not None else None,
            "product_id": int(product_id) if product_id is not None else None,
            "variant_id": int(variant_id) if variant_id is not None else None,
            "product_group_id": None,
            "feature_value_ids": set(),
        }

        # product_group_id and feature_value_ids resolution are optional and lazy.
        # If caller doesn't provide them, we can infer only if product_id is present.
        if product_id is not None:
            try:
                from catalog.models import ProductFeatureValue, Product

                p = Product.objects.filter(id=int(product_id)).only("id", "group_id").first()
                ctx["product_group_id"] = int(p.group_id) if p and p.group_id else None

                fvs = ProductFeatureValue.objects.filter(product_id=int(product_id)).values_list(
                    "feature_value_id", flat=True
                )
                ctx["feature_value_ids"] = {int(x) for x in fvs if x is not None}
            except Exception:
                pass

        def condition_matches(c) -> bool:
            k = str(c.kind)
            if k == "category":
                return bool(c.category_id) and ctx["category_id"] == int(c.category_id)
            if k == "brand":
                return bool(c.brand_id) and ctx["brand_id"] == int(c.brand_id)
            if k == "product":
                return bool(c.product_id) and ctx["product_id"] == int(c.product_id)
            if k == "variant":
                return bool(c.variant_id) and ctx["variant_id"] == int(c.variant_id)
            if k == "product_group":
                return bool(c.product_group_id) and ctx["product_group_id"] == int(c.product_group_id)
            if k == "feature_value":
                return bool(c.feature_value_id) and int(c.feature_value_id) in ctx["feature_value_ids"]
            return False

        # OR between groups, AND within group
        for g in groups:
            conds = list(g.conditions.all())
            if not conds:
                return True
            if all(condition_matches(c) for c in conds):
                return True
        return False

    for r in candidates:
        if _condition_groups_match(r) or _legacy_scope_matches(r):
            return r
    return None


def apply_promo_to_unit_net(
    *,
    base_unit_net: Decimal,
    channel: str,
    category_id: int | None,
    brand_id: int | None,
    product_id: int | None,
    variant_id: int | None,
    customer_group_id: int | None,
    allow_additional_promotions: bool,
    is_discounted_offer: bool,
):
    base_unit_net = Decimal(base_unit_net)
    if base_unit_net <= 0:
        return base_unit_net, None

    if is_discounted_offer and not bool(allow_additional_promotions):
        return base_unit_net, None

    rule = find_best_promo_rule(
        channel=channel,
        category_id=category_id,
        brand_id=brand_id,
        product_id=product_id,
        variant_id=variant_id,
        customer_group_id=customer_group_id,
    )
    if not rule:
        return base_unit_net, None

    discount = rule.get_discount_net_for(eligible_unit_net=base_unit_net)
    sale = (base_unit_net - discount).quantize(Decimal("0.01"))
    if sale < 0:
        sale = Decimal("0.00")
    if sale >= base_unit_net:
        return base_unit_net, None
    return sale, rule
