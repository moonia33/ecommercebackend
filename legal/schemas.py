from __future__ import annotations

from datetime import datetime

from ninja import Schema


class LegalVersionRefOut(Schema):
    version: str
    effective_from: datetime
    is_current: bool = False


class LegalInfoOut(Schema):
    kind: str
    version: str
    effective_from: datetime
    is_current: bool
    change_summary: str = ""
    upcoming: LegalVersionRefOut | None = None
    versions: list[LegalVersionRefOut] = []


class LegalDocumentListOut(Schema):
    kind: str
    slug: str
    url: str
    title: str
    version: str
    effective_from: datetime
    required_at_checkout: bool
    upcoming: LegalVersionRefOut | None = None


class LegalPendingOut(Schema):
    kind: str
    slug: str
    url: str
    title: str
    version: str
    effective_from: datetime
    change_summary: str = ""


class LegalAcceptItemIn(Schema):
    kind: str
    version: str


class LegalAcceptIn(Schema):
    items: list[LegalAcceptItemIn]


class LegalAcceptOut(Schema):
    accepted: int
    pending: list[LegalPendingOut]
