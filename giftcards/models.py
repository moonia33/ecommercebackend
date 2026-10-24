"""Gift cards — stored-value payment instruments, NOT discount coupons.

A gift card carries a real monetary balance that is debited at checkout
like any other payment provider (PaymentIntent provider=gift_card).
Every balance change goes through GiftCardTransaction (ledger rows are
append-only; card.balance is a cached projection kept in sync inside a
row lock).

Cards are issued two ways: staff via Django admin, or automatically when
a paid order contains a `Product.is_gift_card` line (catalog sale —
`issue_cards_for_paid_order`). Codes are bearer credentials — treat like
cash.
"""

from __future__ import annotations

import secrets
import string

from django.conf import settings
from django.db import models


def generate_card_code() -> str:
    # Unambiguous alphabet (no 0/O, 1/I), grouped for readability.
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))


class GiftCard(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DEPLETED = "depleted", "Depleted"
        VOIDED = "voided", "Voided"  # cancelled by staff

    code = models.CharField(
        max_length=32, unique=True, default=generate_card_code,
        help_text="Bearer credential — anyone holding it can spend it.")
    currency = models.CharField(max_length=3, default="EUR")
    initial_amount = models.DecimalField(max_digits=10, decimal_places=2)
    balance = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.ACTIVE)

    # Who bought it (nullable — cards may be issued offline/in-person).
    purchaser = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="purchased_gift_cards")
    # Account the card was redeemed/claimed to. Null = floating card.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="gift_cards")
    recipient_email = models.EmailField(blank=True, default="")
    issued_order = models.ForeignKey(
        "checkout.Order", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="issued_gift_cards")

    note = models.CharField(max_length=255, blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"giftcard:{self.code[-4:]} {self.balance}{self.currency} [{self.status}]"

    @property
    def is_spendable(self) -> bool:
        from django.utils import timezone
        return (
            self.status == self.Status.ACTIVE
            and self.balance > 0
            and (self.expires_at is None or self.expires_at > timezone.now())
        )


class GiftCardTransaction(models.Model):
    """Append-only ledger. Never UPDATE or DELETE rows."""

    class Kind(models.TextChoices):
        ISSUE = "issue", "Issue"
        DEBIT = "debit", "Debit (checkout payment)"
        CREDIT = "credit", "Credit (refund/void order)"
        ADJUST = "adjust", "Staff adjustment"

    card = models.ForeignKey(
        GiftCard, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Signed: +credit/issue, -debit.")
    balance_after = models.DecimalField(max_digits=10, decimal_places=2)
    order = models.ForeignKey(
        "checkout.Order", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="gift_card_transactions")
    # CREDIT reversal'as, atšaukiantis konkretų DEBIT'ą (payment fail/void).
    # Leidžia restore'ą daryti idempotentiškai — debit'as atšaukiamas max 1×.
    reverses = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reversals")
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.kind} {self.amount} -> {self.balance_after}"
