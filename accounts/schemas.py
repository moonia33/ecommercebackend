from __future__ import annotations

from ninja import Schema


class RegisterIn(Schema):
    email: str
    password: str
    first_name: str | None = None
    last_name: str | None = None


class LoginIn(Schema):
    email: str
    password: str


class RefreshIn(Schema):
    refresh: str


class OTPRequestIn(Schema):
    email: str


class OTPVerifyIn(Schema):
    email: str
    code: str


class TokenOut(Schema):
    access: str
    refresh: str


class AccessOut(Schema):
    access: str


class CustomerGroupOut(Schema):
    code: str
    name: str
    priority: int
    pricing_type: str
    allow_additional_discounts: bool
    allow_coupons: bool


class ConsentOut(Schema):
    key: str
    name: str
    version: str | None = None
    required: bool
    accepted: bool
    accepted_at: str | None = None
    revoked_at: str | None = None


class PhoneOut(Schema):
    phone: str
    label: str
    is_primary: bool
    is_verified: bool


class AddressOut(Schema):
    id: int
    label: str
    full_name: str
    company: str
    company_reg_no: str
    company_vat_no: str
    line1: str
    city: str
    postal_code: str
    country_code: str
    phone: str
    is_default_shipping: bool
    is_default_billing: bool


class MeOut(Schema):
    email: str
    first_name: str
    last_name: str
    customer_groups: list[CustomerGroupOut]
    primary_customer_group: CustomerGroupOut | None = None
    consents: list[ConsentOut]
    phones: list[PhoneOut]
    addresses: list[AddressOut]


class MeUpdateIn(Schema):
    first_name: str | None = None
    last_name: str | None = None


class AddressCreateIn(Schema):
    label: str = ""
    full_name: str = ""
    company: str = ""
    company_reg_no: str = ""
    company_vat_no: str = ""
    line1: str
    city: str
    postal_code: str
    country_code: str = "LT"
    phone: str = ""
    is_default_shipping: bool = False
    is_default_billing: bool = False


class AddressUpdateIn(Schema):
    label: str | None = None
    full_name: str | None = None
    company: str | None = None
    company_reg_no: str | None = None
    company_vat_no: str | None = None
    line1: str | None = None
    city: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    phone: str | None = None
    is_default_shipping: bool | None = None
    is_default_billing: bool | None = None


class ConsentUpdateItemIn(Schema):
    key: str
    accepted: bool


class ConsentUpdateIn(Schema):
    items: list[ConsentUpdateItemIn]
