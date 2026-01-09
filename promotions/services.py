from __future__ import annotations

from django.db import transaction
from django.db.models import F


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
