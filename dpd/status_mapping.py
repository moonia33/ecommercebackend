from __future__ import annotations

from checkout.models import Order

# DPD serviceCodes that mark a return-to-sender flow.
# statusCode=13 means "delivered" in BOTH directions — only serviceCode
# disambiguates consignee delivery vs return to sender.
RETURN_SERVICE_CODES = {"298", "299", "300", "301", "332"}


def map_dpd_status(
    *,
    status_code: str,
    service_codes: list[str] | None = None,
    prev_status_code: str = "",
) -> str | None:
    """Map a DPD statusCode/serviceCode pair to unified Order.DeliveryStatus.

    Returns None when the event shouldn't change the current status.
    """
    sc = (status_code or "").strip().upper()
    prev = (prev_status_code or "").strip().upper()
    services = {str(s).strip() for s in (service_codes or []) if str(s).strip()}
    is_return = bool(services & RETURN_SERVICE_CODES)

    # Return-to-sender flow wins: 13+return service = delivered back to us.
    if is_return:
        if sc == "13":
            return Order.DeliveryStatus.RETURNED
        return Order.DeliveryStatus.RETURNING

    if sc == "13":
        return Order.DeliveryStatus.DELIVERED
    if sc == "DODEY":
        return Order.DeliveryStatus.DELIVERED  # picked up from locker
    if sc == "DEYY":
        # DEYY is a generic "delivered/failed" info event; prev decides.
        if prev == "04":
            return Order.DeliveryStatus.IN_TRANSIT
        return Order.DeliveryStatus.DELIVERED
    if sc in {"23", "DODEI"}:
        return Order.DeliveryStatus.AT_PICKUP_POINT
    if sc in {"05", "15", "DOPKY"}:
        # Parcel accepted from sender — entered DPD network.
        return Order.DeliveryStatus.PICKED_UP
    if sc in {"03", "01", "02", "06", "09", "10", "20"}:
        return Order.DeliveryStatus.IN_TRANSIT
    if sc in {"04", "14"}:
        # Failed delivery attempt — parcel back at terminal for redelivery.
        return Order.DeliveryStatus.IN_TRANSIT
    if sc == "08":
        return Order.DeliveryStatus.ERROR
    return None


def extract_latest_event(raw: list[dict] | dict) -> dict:
    """Pick the newest status event from a /status/tracking response."""
    if isinstance(raw, dict):
        item = raw
    else:
        item = raw[0] if raw and isinstance(raw[0], dict) else {}
    details = item.get("details") or item.get("statuses") or []
    if isinstance(details, list) and details:
        latest = details[0] if isinstance(details[0], dict) else {}
    else:
        latest = item
    service_codes: list[str] = []
    for key in ("serviceCode", "serviceCodes", "services"):
        val = latest.get(key) or item.get(key)
        if isinstance(val, list):
            service_codes = [str(x) for x in val]
        elif val:
            service_codes = [str(val)]
        if service_codes:
            break
    return {
        "status_code": str(
            latest.get("statusCode") or latest.get("status_code") or item.get("statusCode") or ""
        ),
        "prev_status_code": str(
            latest.get("prevStatusCode") or latest.get("prev_status_code") or ""
        ),
        "service_codes": service_codes,
        "status_text": str(
            latest.get("status") or latest.get("statusText") or latest.get("statusName") or ""
        ),
    }
