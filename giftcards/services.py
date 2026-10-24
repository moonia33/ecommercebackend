"""Gift card debit/credit/issue services — all mutations are row-locked
and ledgered. Callers must run inside transaction.atomic()."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from .models import GiftCard, GiftCardTransaction

logger = logging.getLogger(__name__)


def normalize_code(raw: str) -> str:
    return (raw or "").strip().upper().replace(" ", "").replace("-", "")


def get_spendable_card(code: str, *, for_update: bool = False) -> GiftCard:
    qs = GiftCard.objects.filter(code=normalize_code(code))
    if for_update:
        qs = qs.select_for_update()
    card = qs.first()
    if card is None:
        raise HttpError(400, "Gift card not found")
    if card.status == GiftCard.Status.VOIDED:
        raise HttpError(400, "Gift card is voided")
    if card.expires_at is not None and card.expires_at <= timezone.now():
        raise HttpError(400, "Gift card has expired")
    if card.status != GiftCard.Status.ACTIVE or card.balance <= 0:
        raise HttpError(400, "Gift card has no balance")
    return card


def claim_card(card: GiftCard, *, user) -> None:
    """Attach a floating card to the redeeming user's account."""
    if card.owner_id is not None and card.owner_id != user.id:
        raise HttpError(400, "Gift card already belongs to another account")
    if card.owner_id is None:
        card.owner = user
        card.save(update_fields=["owner", "updated_at"])


@transaction.atomic
def debit_card(*, code: str, order, amount: Decimal, user) -> GiftCard:
    """Debit `amount` from the card for `order`. Caller decides how much to
    take (partial cover is fine — pass min(balance, order.total_gross));
    raises only when `amount` exceeds the balance. Must be called after
    order totals are final."""
    card = get_spendable_card(code, for_update=True)
    if card.currency != order.currency:
        raise HttpError(400, "Gift card currency mismatch")
    claim_card(card, user=user)
    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount <= 0:
        raise HttpError(400, "Invalid gift card amount")
    if card.balance < amount:
        raise HttpError(400, "Insufficient gift card balance")
    card.balance = (card.balance - amount).quantize(Decimal("0.01"))
    if card.balance <= 0:
        card.balance = Decimal("0.00")
        card.status = GiftCard.Status.DEPLETED
        card.save(update_fields=["balance", "status", "updated_at"])
    else:
        card.save(update_fields=["balance", "updated_at"])
    GiftCardTransaction.objects.create(
        card=card, kind=GiftCardTransaction.Kind.DEBIT,
        amount=-amount, balance_after=card.balance,
        order=order, note=f"Order #{order.id}",
    )
    return card


@transaction.atomic
def credit_card(*, card: GiftCard, order=None, amount: Decimal,
                note: str = "") -> None:
    """Refund back onto the card (order cancellation/return)."""
    card = GiftCard.objects.select_for_update().get(pk=card.pk)
    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount <= 0:
        return
    card.balance = (card.balance + amount).quantize(Decimal("0.01"))
    update = ["balance", "updated_at"]
    if card.status == GiftCard.Status.DEPLETED:
        card.status = GiftCard.Status.ACTIVE
        update.append("status")
    card.save(update_fields=update)
    GiftCardTransaction.objects.create(
        card=card, kind=GiftCardTransaction.Kind.CREDIT,
        amount=amount, balance_after=card.balance,
        order=order, note=note,
    )


@transaction.atomic
def restore_card_debits_for_order(*, order) -> Decimal:
    """Reverse DEBIT charges for `order` — called when the covering
    payment fails, is voided/abandoned or the order is cancelled unpaid.

    Idempotent: each DEBIT is reversed at most once (the CREDIT row keeps
    `reverses` pointing at the debit), so this is safe to call from every
    terminal-failure path.
    """
    debits = list(
        GiftCardTransaction.objects.select_for_update()
        .filter(order=order, kind=GiftCardTransaction.Kind.DEBIT)
        .select_related("card")
    )
    restored = Decimal("0.00")
    for debit in debits:
        already = GiftCardTransaction.objects.filter(
            reverses=debit).exists()
        if already:
            continue
        amount = abs(Decimal(debit.amount)).quantize(Decimal("0.01"))
        if amount <= 0:
            continue
        card = GiftCard.objects.select_for_update().get(pk=debit.card_id)
        card.balance = (card.balance + amount).quantize(Decimal("0.01"))
        update = ["balance", "updated_at"]
        if card.status == GiftCard.Status.DEPLETED:
            card.status = GiftCard.Status.ACTIVE
            update.append("status")
        card.save(update_fields=update)
        GiftCardTransaction.objects.create(
            card=card, kind=GiftCardTransaction.Kind.CREDIT,
            amount=amount, balance_after=card.balance,
            order=order, reverses=debit,
            note=f"Order #{order.id} payment not completed",
        )
        restored += amount
    return restored


def issue_cards_for_paid_order(*, order) -> list[GiftCard]:
    """Issue one GiftCard per unit of every gift-card line on a paid order.

    Card face value = the gross unit price the customer actually paid
    (so a discounted/sale price still produces a correct balance).
    Idempotent — skipped when the order already issued cards.
    """
    if order.issued_gift_cards.exists():
        return []

    lines = list(
        order.lines.select_related("variant__product")
        .filter(variant__product__is_gift_card=True)
    )
    if not lines:
        return []

    expires_at = timezone.now() + timezone.timedelta(
        days=int(getattr(settings, "GIFT_CARD_EXPIRY_DAYS", 365) or 365))
    currency = order.currency or "EUR"
    # Gavėjas: explicit choice (dovana siunčiama tiesiai) arba pirkėjas.
    recipient_email = (
        (order.gift_recipient_email or "").strip()
        or (order.user.email or "")
    )
    cards: list[GiftCard] = []
    for ln in lines:
        face_value = Decimal(ln.unit_gross).quantize(Decimal("0.01"))
        for _ in range(int(ln.qty)):
            card = GiftCard.objects.create(
                currency=currency,
                initial_amount=face_value,
                balance=face_value,
                status=GiftCard.Status.ACTIVE,
                purchaser=order.user,
                recipient_email=recipient_email,
                issued_order=order,
                expires_at=expires_at,
            )
            GiftCardTransaction.objects.create(
                card=card, kind=GiftCardTransaction.Kind.ISSUE,
                amount=face_value, balance_after=card.balance,
                order=order, note=f"Order #{order.id} line {ln.id}",
            )
            cards.append(card)

    _email_issued_cards(order=order, cards=cards)
    return cards


def _email_issued_cards(*, order, cards: list[GiftCard]) -> None:
    """One email per card so each can be forwarded/printed individually.

    Template key `gift_card_issued` (DB EmailTemplate); when it is
    missing/inactive send_templated_email logs a failed OutboundEmail
    and we simply move on — never block payment completion on email.
    """
    # Kodas keliauja gavėjui (jei nurodytas) arba pirkėjui. Užsakymo
    # patvirtinimas pirkėjui vis tiek eina per order email flow.
    to_email = (
        (getattr(order, "gift_recipient_email", "") or "").strip()
        or (order.user.email or "").strip()
    )
    if not to_email or not cards:
        return
    try:
        from notifications.services import send_templated_email

        shop_url = (getattr(settings, "STOREFRONT_URL", "") or "").rstrip("/")
        for card in cards:
            send_templated_email(
                template_key="gift_card_issued",
                to_email=to_email,
                context={
                    "card_code": card.code,
                    "amount": f"{card.initial_amount} {card.currency}",
                    "expires_at": (
                        card.expires_at.date().isoformat()
                        if card.expires_at else ""
                    ),
                    "order_id": order.id,
                    "shop_url": shop_url,
                },
            )
    except Exception:
        logger.exception("gift card email failed for order %s", order.id)
