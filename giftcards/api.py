"""Customer-facing gift card endpoints — list my cards, check a code,
claim a card to my account."""

from __future__ import annotations

from ninja import Router, Schema
from ninja.errors import HttpError

from accounts.auth import JWTAuth
from api.shop_context import get_request_shop, shop_context_enabled, shop_context_strict

from .models import GiftCard
from .services import claim_card, get_spendable_card, normalize_code

router = Router(tags=["giftcards"])

_auth = JWTAuth()


class GiftCardOut(Schema):
    code: str
    currency: str
    initial_amount: str
    balance: str
    status: str
    expires_at: str | None = None
    is_spendable: bool


class CheckIn(Schema):
    code: str


def _require_user(request):
    user = request.auth
    if not user:
        raise HttpError(401, "Unauthorized")
    return user


def _ensure_shop(request) -> None:
    if shop_context_enabled() and shop_context_strict() and get_request_shop(request) is None:
        raise HttpError(404, "Shop not resolved for host")


def _out(card: GiftCard) -> dict:
    return {
        "code": card.code,
        "currency": card.currency,
        "initial_amount": str(card.initial_amount),
        "balance": str(card.balance),
        "status": card.status,
        "expires_at": card.expires_at.isoformat() if card.expires_at else None,
        "is_spendable": card.is_spendable,
    }


@router.get("/mine", response=list[GiftCardOut], auth=_auth)
def mine(request):
    user = _require_user(request)
    _ensure_shop(request)
    from django.db import models
    cards = GiftCard.objects.filter(
        models.Q(owner=user) | models.Q(purchaser=user)).order_by("-created_at")
    return [_out(c) for c in cards]


@router.post("/check", response=GiftCardOut, auth=_auth)
def check(request, payload: CheckIn):
    user = _require_user(request)
    _ensure_shop(request)
    card = get_spendable_card(normalize_code(payload.code))
    if card.owner_id is not None and card.owner_id != user.id:
        raise HttpError(400, "Gift card already belongs to another account")
    return _out(card)


@router.post("/redeem", response=GiftCardOut, auth=_auth)
def redeem(request, payload: CheckIn):
    user = _require_user(request)
    _ensure_shop(request)
    card = get_spendable_card(normalize_code(payload.code))
    claim_card(card, user=user)
    return _out(card)
