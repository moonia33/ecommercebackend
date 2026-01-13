from __future__ import annotations

from django.db.models import Q
from ninja import Router
from ninja.errors import HttpError

from .client import DpdApiError, DpdClient
from .models import DpdLocker
from .schemas import LockerOut, StatusOut

router = Router(tags=["DPD"])  # mounted under /dpd


@router.get("/lockers", response=list[LockerOut])
def list_lockers(
    request,
    country_code: str = "LT",
    city: str | None = None,
    search: str | None = None,
    locker_type: str | None = None,
    postal_code: str | None = None,
    limit: int | None = 1000,
):
    cc = (country_code or "").strip().upper()
    if len(cc) != 2:
        raise HttpError(400, "Invalid country_code")

    lim = 50 if limit is None else int(limit)
    lim = max(1, min(lim, 1000))

    qs = DpdLocker.objects.filter(is_active=True, country_code=cc)

    if city:
        qs = qs.filter(city__iexact=str(city).strip())
    if postal_code:
        qs = qs.filter(postal_code__iexact=str(postal_code).strip())
    if locker_type:
        # lockerType is stored in raw payload (top-level, per DPD API)
        qs = qs.filter(raw__lockerType=str(locker_type).strip())
    if search:
        s = str(search).strip()
        if s:
            qs = qs.filter(
                Q(locker_id__icontains=s)
                | Q(name__icontains=s)
                | Q(city__icontains=s)
                | Q(street__icontains=s)
                | Q(postal_code__icontains=s)
            )

    qs = qs.order_by("city", "name", "locker_id")[:lim]

    out: list[LockerOut] = []
    for o in qs:
        raw = o.raw if isinstance(o.raw, dict) else {}
        out.append(
            LockerOut(
                id=str(o.locker_id or ""),
                name=str(o.name or ""),
                lockerType=str(raw.get("lockerType") or ""),
                countryCode=str(o.country_code or ""),
                city=str(o.city or ""),
                street=str(o.street or ""),
                postalCode=str(o.postal_code or ""),
                latitude=float(o.latitude) if o.latitude is not None else None,
                longitude=float(
                    o.longitude) if o.longitude is not None else None,
            )
        )

    return [x for x in out if x.id]


@router.get("/status", response=StatusOut)
def get_status(request, tracking_number: str):
    pknr = (tracking_number or "").strip()
    if not pknr:
        raise HttpError(400, "tracking_number is required")

    try:
        raw = DpdClient().get_status(pknr=pknr, detail="0", show_all="0")
    except DpdApiError as e:
        raise HttpError(502, str(e))

    return StatusOut(raw=raw)
