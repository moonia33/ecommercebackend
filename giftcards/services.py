"""Gift card debit/credit services — all mutations are row-locked and
ledgered. Callers must run inside transaction.atomic()."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from .models import GiftCard, GiftCardTransaction


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
    """Debit `amount` from the card for `order`. Raises HttpError on
    insufficient funds. Must be called after order totals are final."""
    card = get_spendable_card(code, for_update=True)
    if card.currency != order.currency:
        raise HttpError(400, "Gift card currency mismatch")
    claim_card(card, user=user)
    amount = Decimal(amount).quantize(Decimal("0.01"))
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
