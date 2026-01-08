from __future__ import annotations

from django.db.models import Q
from ninja import Router
from ninja.errors import HttpError

from .models import UnisendTerminal
from .schemas import TerminalOut


router = Router(tags=["Unisend"])  # mounted under /unisend


@router.get("/terminals", response=list[TerminalOut])
def list_terminals(
    request,
    country_code: str = "LT",
    locality: str | None = None,
    search: str | None = None,
    postal_code: str | None = None,
    limit: int | None = 50,
):
    cc = (country_code or "").strip().upper()
    if len(cc) != 2:
        raise HttpError(400, "Invalid country_code")

    lim = 50 if limit is None else int(limit)
    lim = max(1, min(lim, 1000))

    qs = UnisendTerminal.objects.filter(is_active=True, country_code=cc)

    if locality:
        qs = qs.filter(locality__iexact=str(locality).strip())
    if postal_code:
        qs = qs.filter(postal_code__iexact=str(postal_code).strip())
    if search:
        s = str(search).strip()
        if s:
            qs = qs.filter(
                Q(terminal_id__icontains=s)
                | Q(name__icontains=s)
                | Q(locality__icontains=s)
                | Q(street__icontains=s)
                | Q(postal_code__icontains=s)
            )

    qs = qs.order_by("locality", "name", "terminal_id")[:lim]

    out: list[TerminalOut] = []
    for o in qs:
        raw_addr = ""
        try:
            if isinstance(o.raw, dict):
                raw_addr = str(o.raw.get("address") or "").strip()
        except Exception:
            raw_addr = ""

        out.append(
            TerminalOut(
                id=str(o.terminal_id or ""),
                name=str(o.name or ""),
                countryCode=str(o.country_code or ""),
                locality=str(o.locality or ""),
                street=str((o.street or "").strip() or raw_addr),
                postalCode=str(o.postal_code or ""),
                latitude=float(o.latitude) if o.latitude is not None else None,
                longitude=float(o.longitude) if o.longitude is not None else None,
            )
        )

    return [x for x in out if x.id]
