from __future__ import annotations

import secrets

from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from django.db import IntegrityError
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from notifications.services import send_templated_email

from .auth import JWTAuth
from .jwt_utils import issue_access_token, issue_refresh_token, decode_token
from .models import ConsentType, EmailOTP, UserAddress, UserConsent
from .schemas import (
    AccessOut,
    AddressCreateIn,
    AddressOut,
    AddressUpdateIn,
    LoginIn,
    ConsentUpdateIn,
    MeUpdateIn,
    MeOut,
    OTPRequestIn,
    OTPVerifyIn,
    RefreshIn,
    RegisterIn,
    TokenOut,
)

router = Router(tags=["auth"])
User = get_user_model()
auth = JWTAuth()


def _generate_numeric_code(length: int) -> str:
    length = max(4, min(int(length), 10))
    max_value = 10**length
    return str(secrets.randbelow(max_value)).zfill(length)


@router.post("/otp/request")
def otp_request(request, payload: OTPRequestIn):
    email = payload.email.strip().lower()
    if not email:
        raise HttpError(400, "Email is required")

    ttl = int(getattr(settings, "EMAIL_OTP_TTL_MINUTES", 10))
    length = int(getattr(settings, "EMAIL_OTP_CODE_LENGTH", 6))
    cooldown = int(getattr(settings, "EMAIL_OTP_RESEND_COOLDOWN_SECONDS", 30))

    existing = (
        EmailOTP.objects.filter(
            email=email, used_at__isnull=True, expires_at__gt=timezone.now())
        .order_by("-created_at")
        .first()
    )
    if existing and existing.last_sent_at:
        seconds_since = (timezone.now() -
                         existing.last_sent_at).total_seconds()
        if seconds_since < cooldown:
            # Neleidžiam spam'inti, bet neatskleidžiam detalių
            return {"status": "ok"}

    code = _generate_numeric_code(length)

    result = send_templated_email(
        template_key="auth_otp_code",
        to_email=email,
        context={"code": code, "ttl_minutes": ttl},
    )
    if not result.ok:
        if getattr(settings, "DEBUG", False):
            raise HttpError(503, f"Email sending failed: {result.error}")
        raise HttpError(503, "Email sending failed")

    EmailOTP.objects.create(
        email=email,
        code_hash=make_password(code),
        expires_at=EmailOTP.new_expires_at(ttl),
        last_sent_at=timezone.now(),
    )

    return {"status": "ok"}


@router.post("/otp/verify", response=TokenOut)
def otp_verify(request, payload: OTPVerifyIn):
    email = payload.email.strip().lower()
    code = payload.code.strip()
    if not email or not code:
        raise HttpError(400, "Email and code are required")

    max_attempts = int(getattr(settings, "EMAIL_OTP_MAX_ATTEMPTS", 5))

    otp = (
        EmailOTP.objects.filter(email=email, used_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if not otp or otp.is_expired:
        raise HttpError(401, "Invalid code")

    if otp.attempts >= max_attempts:
        raise HttpError(429, "Too many attempts")

    ok = check_password(code, otp.code_hash)
    otp.attempts += 1
    otp.save(update_fields=["attempts"])
    if not ok:
        raise HttpError(401, "Invalid code")

    otp.mark_used()

    user, created = User.objects.get_or_create(
        email=email, defaults={"is_active": True})
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])

    return {
        "access": issue_access_token(user_id=user.id),
        "refresh": issue_refresh_token(user_id=user.id),
    }


@router.post("/register", response=TokenOut)
def register(request, payload: RegisterIn):
    try:
        user = User.objects.create_user(
            email=payload.email,
            password=payload.password,
            first_name=payload.first_name or "",
            last_name=payload.last_name or "",
        )
    except IntegrityError:
        raise HttpError(400, "User with this email already exists")

    return {
        "access": issue_access_token(user_id=user.id),
        "refresh": issue_refresh_token(user_id=user.id),
    }


@router.post("/login", response=TokenOut)
def login(request, payload: LoginIn):
    user = authenticate(request, username=payload.email,
                        password=payload.password)
    if user is None:
        raise HttpError(401, "Invalid credentials")

    return {
        "access": issue_access_token(user_id=user.id),
        "refresh": issue_refresh_token(user_id=user.id),
    }


@router.post("/refresh", response=AccessOut)
def refresh(request, payload: RefreshIn):
    try:
        data = decode_token(payload.refresh)
    except Exception:
        raise HttpError(401, "Invalid refresh token")

    if data.get("type") != "refresh":
        raise HttpError(401, "Invalid refresh token")

    user_id = data.get("sub")
    if not user_id:
        raise HttpError(401, "Invalid refresh token")

    return {"access": issue_access_token(user_id=int(user_id))}


@router.get("/me", response=MeOut, auth=auth)
def me(request):
    user = request.auth

    phones_out = [
        {
            "phone": p.phone,
            "label": p.label,
            "is_primary": bool(p.is_primary),
            "is_verified": bool(p.is_verified),
        }
        for p in user.phones.all().order_by("-is_primary", "phone")
    ]

    addresses_out = [
        {
            "id": a.id,
            "label": a.label,
            "full_name": a.full_name,
            "company": a.company,
            "company_reg_no": a.company_reg_no,
            "company_vat_no": a.company_vat_no,
            "line1": a.line1,
            "city": a.city,
            "postal_code": a.postal_code,
            "country_code": a.country_code,
            "phone": a.phone,
            "is_default_shipping": bool(a.is_default_shipping),
            "is_default_billing": bool(a.is_default_billing),
        }
        for a in user.addresses.all().order_by(
            "-is_default_shipping", "-is_default_billing", "-updated_at"
        )
    ]

    groups_qs = user.get_active_customer_groups().order_by("-priority", "code")
    groups = [
        {
            "code": g.code,
            "name": g.name,
            "priority": g.priority,
            "pricing_type": g.pricing_type,
            "allow_additional_discounts": g.allow_additional_discounts,
            "allow_coupons": g.allow_coupons,
        }
        for g in groups_qs
    ]

    primary = user.get_primary_customer_group()
    primary_out = (
        {
            "code": primary.code,
            "name": primary.name,
            "priority": primary.priority,
            "pricing_type": primary.pricing_type,
            "allow_additional_discounts": primary.allow_additional_discounts,
            "allow_coupons": primary.allow_coupons,
        }
        if primary
        else None
    )

    consent_types = ConsentType.objects.filter(is_active=True).order_by(
        "sort_order", "key"
    )
    existing = {
        uc.consent_type_id: uc
        for uc in UserConsent.objects.filter(user=user, consent_type__in=consent_types)
    }

    consents_out = []
    for ct in consent_types:
        uc = existing.get(ct.id)
        consents_out.append(
            {
                "key": ct.key,
                "name": ct.name,
                "version": ct.version or None,
                "required": bool(ct.is_required),
                "accepted": bool(uc.accepted) if uc else False,
                "accepted_at": uc.accepted_at.isoformat() if uc and uc.accepted_at else None,
                "revoked_at": uc.revoked_at.isoformat() if uc and uc.revoked_at else None,
            }
        )

    return {
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "customer_groups": groups,
        "primary_customer_group": primary_out,
        "consents": consents_out,
        "phones": phones_out,
        "addresses": addresses_out,
    }


@router.patch("/me", response=MeOut, auth=auth)
def update_me(request, payload: MeUpdateIn):
    user = request.auth

    update_fields: list[str] = []

    if payload.first_name is not None:
        user.first_name = (payload.first_name or "").strip()
        update_fields.append("first_name")
    if payload.last_name is not None:
        user.last_name = (payload.last_name or "").strip()
        update_fields.append("last_name")

    if update_fields:
        user.save(update_fields=update_fields)

    return me(request)


def _serialize_address(a: UserAddress) -> dict:
    return {
        "id": a.id,
        "label": a.label,
        "full_name": a.full_name,
        "company": a.company,
        "company_reg_no": a.company_reg_no,
        "company_vat_no": a.company_vat_no,
        "line1": a.line1,
        "city": a.city,
        "postal_code": a.postal_code,
        "country_code": a.country_code,
        "phone": a.phone,
        "is_default_shipping": bool(a.is_default_shipping),
        "is_default_billing": bool(a.is_default_billing),
    }


@router.get("/addresses", response=list[AddressOut], auth=auth)
def list_addresses(request):
    user = request.auth
    qs = UserAddress.objects.filter(user=user).order_by(
        "-is_default_shipping", "-is_default_billing", "-updated_at"
    )
    return [_serialize_address(a) for a in qs]


@router.post("/addresses", response=AddressOut, auth=auth)
def create_address(request, payload: AddressCreateIn):
    user = request.auth

    country_code = (payload.country_code or "").strip().upper() or "LT"
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    with transaction.atomic():
        if payload.is_default_shipping:
            UserAddress.objects.filter(user=user, is_default_shipping=True).update(
                is_default_shipping=False
            )
        if payload.is_default_billing:
            UserAddress.objects.filter(user=user, is_default_billing=True).update(
                is_default_billing=False
            )

        addr = UserAddress.objects.create(
            user=user,
            label=(payload.label or "").strip(),
            full_name=(payload.full_name or "").strip(),
            company=(payload.company or "").strip(),
            company_reg_no=(payload.company_reg_no or "").strip(),
            company_vat_no=(payload.company_vat_no or "").strip(),
            line1=(payload.line1 or "").strip(),
            city=(payload.city or "").strip(),
            postal_code=(payload.postal_code or "").strip(),
            country_code=country_code,
            phone=(payload.phone or "").strip(),
            is_default_shipping=bool(payload.is_default_shipping),
            is_default_billing=bool(payload.is_default_billing),
        )

    return _serialize_address(addr)


@router.patch("/addresses/{address_id}", response=AddressOut, auth=auth)
def update_address(request, address_id: int, payload: AddressUpdateIn):
    user = request.auth
    addr = UserAddress.objects.filter(user=user, id=address_id).first()
    if not addr:
        raise HttpError(404, "Address not found")

    with transaction.atomic():
        if payload.is_default_shipping is True:
            UserAddress.objects.filter(user=user, is_default_shipping=True).exclude(
                id=addr.id
            ).update(is_default_shipping=False)
            addr.is_default_shipping = True
        elif payload.is_default_shipping is False:
            addr.is_default_shipping = False

        if payload.is_default_billing is True:
            UserAddress.objects.filter(user=user, is_default_billing=True).exclude(
                id=addr.id
            ).update(is_default_billing=False)
            addr.is_default_billing = True
        elif payload.is_default_billing is False:
            addr.is_default_billing = False

        if payload.label is not None:
            addr.label = (payload.label or "").strip()
        if payload.full_name is not None:
            addr.full_name = (payload.full_name or "").strip()
        if payload.company is not None:
            addr.company = (payload.company or "").strip()
        if payload.company_reg_no is not None:
            addr.company_reg_no = (payload.company_reg_no or "").strip()
        if payload.company_vat_no is not None:
            addr.company_vat_no = (payload.company_vat_no or "").strip()
        if payload.line1 is not None:
            addr.line1 = (payload.line1 or "").strip()
        if payload.city is not None:
            addr.city = (payload.city or "").strip()
        if payload.postal_code is not None:
            addr.postal_code = (payload.postal_code or "").strip()
        if payload.country_code is not None:
            cc = (payload.country_code or "").strip().upper()
            if len(cc) != 2:
                raise HttpError(400, "Invalid country_code")
            addr.country_code = cc
        if payload.phone is not None:
            addr.phone = (payload.phone or "").strip()

        addr.save()

    return _serialize_address(addr)


@router.delete("/addresses/{address_id}", auth=auth)
def delete_address(request, address_id: int):
    user = request.auth
    deleted = UserAddress.objects.filter(user=user, id=address_id).delete()[0]
    if not deleted:
        raise HttpError(404, "Address not found")
    return {"status": "ok"}


@router.put("/consents", response=MeOut, auth=auth)
def update_consents(request, payload: ConsentUpdateIn):
    user = request.auth
    updates = payload.items or []

    keys = [i.key.strip() for i in updates if i.key and i.key.strip()]
    consent_types = {
        ct.key: ct
        for ct in ConsentType.objects.filter(is_active=True, key__in=keys)
    }
    unknown = sorted({k for k in keys if k not in consent_types})
    if unknown:
        raise HttpError(400, f"Unknown consent keys: {', '.join(unknown)}")

    for item in updates:
        key = item.key.strip()
        ct = consent_types.get(key)
        if not ct:
            continue
        uc, _ = UserConsent.objects.get_or_create(user=user, consent_type=ct)
        uc.set_status(bool(item.accepted), source="api")

    return me(request)
