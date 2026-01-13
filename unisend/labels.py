from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from django.core.files.base import ContentFile
from django.utils import timezone

from checkout.models import Order

from .client import UnisendApiError, UnisendClient
from .models import UnisendApiConfig, UnisendTerminal


class UnisendLabelConfigError(RuntimeError):
    pass


def _require(value: str, *, key: str) -> str:
    v = (value or "").strip()
    if not v:
        raise UnisendLabelConfigError(f"Trūksta konfigūracijos: {key}")
    return v


def _estimate_order_weight_g(order: Order) -> int:
    total_g = 0
    for ln in order.lines.select_related("variant").all():
        if ln.variant_id and getattr(ln.variant, "weight_g", 0):
            total_g += int(ln.variant.weight_g) * int(ln.qty)
    if total_g <= 0:
        return 1000
    return max(100, min(total_g, 31500))


def _cfg() -> UnisendApiConfig:
    return UnisendApiConfig.get_solo()


def _sender(cfg: UnisendApiConfig) -> dict[str, Any]:
    address: dict[str, Any] = {
        "countryCode": _require(cfg.sender_country, key="UNISEND_SENDER_COUNTRY"),
        "locality": _require(cfg.sender_locality, key="UNISEND_SENDER_LOCALITY"),
        "postalCode": _require(cfg.sender_postal_code, key="UNISEND_SENDER_POSTAL_CODE"),
        "street": _require(cfg.sender_street, key="UNISEND_SENDER_STREET"),
    }
    building = str(cfg.sender_building or "").strip()
    if building:
        address["building"] = building
    flat = str(cfg.sender_flat or "").strip()
    if flat:
        address["flat"] = flat

    contacts: dict[str, Any] = {
        "phone": _require(cfg.sender_phone, key="UNISEND_SENDER_PHONE"),
    }
    email = str(cfg.sender_email or "").strip()
    if email:
        contacts["email"] = email

    return {
        "name": _require(cfg.sender_name, key="UNISEND_SENDER_NAME"),
        "address": address,
        "contacts": contacts,
    }


def _receiver_terminal(order: Order, *, terminal: UnisendTerminal) -> dict[str, Any]:
    full_name = (order.shipping_full_name or "").strip() or "Customer"
    phone = (order.shipping_phone or "").strip()
    if not phone:
        raise UnisendLabelConfigError("lpexpress: trūksta telefono (shipping_phone)")

    return {
        "name": full_name,
        "companyName": (order.shipping_company or "").strip() or None,
        "address": {
            "countryCode": (terminal.country_code or "").strip().upper() or "LT",
            "terminalId": (terminal.terminal_id or "").strip(),
        },
        "contacts": {
            "phone": phone,
            "email": (getattr(order.user, "email", "") or "").strip() or None,
        },
    }


def _receiver_courier(order: Order) -> dict[str, Any]:
    full_name = (order.shipping_full_name or "").strip() or "Customer"
    phone = (order.shipping_phone or "").strip()
    if not phone:
        raise UnisendLabelConfigError("lpexpress: trūksta telefono (shipping_phone)")

    country_code = (order.shipping_country_code or order.country_code or "LT").strip().upper() or "LT"
    locality = (order.shipping_city or "").strip()
    postal_code = (order.shipping_postal_code or "").strip()
    street = (order.shipping_line1 or "").strip()

    if not locality or not postal_code or not street:
        raise UnisendLabelConfigError("lpexpress_courier: trūksta gavėjo adreso (city/postal_code/line1)")

    return {
        "name": full_name,
        "companyName": (order.shipping_company or "").strip() or None,
        "address": {
            "countryCode": country_code,
            "locality": locality,
            "postalCode": postal_code,
            "street": street,
        },
        "contacts": {
            "phone": phone,
            "email": (getattr(order.user, "email", "") or "").strip() or None,
        },
    }


def ensure_unisend_parcel(order: Order, *, client: UnisendClient | None = None) -> int:
    existing = (order.carrier_shipment_id or "").strip()
    if (order.carrier_code or "").strip() in {"lpexpress", "unisend"} and existing.isdigit():
        return int(existing)

    shipping_method = (order.shipping_method or "").strip()
    if shipping_method not in {"unisend_pickup", "unisend_courier", "lpexpress", "lpexpress_courier"}:
        raise UnisendLabelConfigError("Šis užsakymas nėra Unisend.")

    receiver: dict[str, Any]
    plan_code: str
    parcel_type: str
    parcel_size: str

    if shipping_method in {"unisend_courier", "lpexpress_courier"}:
        # Courier: from sender (home/office) to receiver address.
        plan_code = "HANDS"
        parcel_type = "H2H"
        parcel_size = "S"
        receiver = _receiver_courier(order)
    else:
        # Terminal: from terminal to terminal.
        terminal_id = (order.pickup_point_id or "").strip()
        if not terminal_id:
            raise UnisendLabelConfigError("lpexpress: trūksta pickup_point_id")

        terminal = UnisendTerminal.objects.filter(terminal_id=terminal_id, is_active=True).first()
        if not terminal:
            raise UnisendLabelConfigError("lpexpress: neteisingas pickup_point_id")

        plan_code = "TERMINAL"
        parcel_type = "T2T"
        parcel_size = "XS"
        receiver = _receiver_terminal(order, terminal=terminal)

    client = client or UnisendClient()
    cfg = _cfg()

    weight_g = _estimate_order_weight_g(order)

    payload: dict[str, Any] = {
        "plan": {"code": plan_code},
        "parcel": {
            "type": parcel_type,
            "size": parcel_size,
            "weight": str(weight_g),
        },
        "services": [],
        "receiver": receiver,
        "sender": _sender(cfg),
    }

    created = client.create_parcel(payload=payload)
    parcel_id = created.get("id") or created.get("parcelId")
    if parcel_id is None:
        raise UnisendLabelConfigError("Unisend: create_parcel negrąžino parcel id")

    try:
        parcel_id_int = int(parcel_id)
    except Exception as e:
        raise UnisendLabelConfigError("Unisend: parcel id neteisingas") from e

    order.carrier_code = "unisend"
    order.carrier_shipment_id = str(parcel_id_int)
    order.save(update_fields=["carrier_code", "carrier_shipment_id", "updated_at"])

    return parcel_id_int


def generate_label_pdf_for_order(
    order: Order,
    *,
    client: UnisendClient | None = None,
    store_on_order: bool = True,
) -> bytes:
    client = client or UnisendClient()
    parcel_id = ensure_unisend_parcel(order, client=client)

    # Ensure shipping is initiated so barcode/tracking exists.
    client.initiate_shipping(parcel_ids=[parcel_id], process_async=False)

    tracking = ""
    try:
        barcodes = client.list_barcodes(parcel_ids=[parcel_id])
        if isinstance(barcodes, dict):
            items = barcodes.get("items")
            if isinstance(items, list) and items:
                it0 = items[0] if isinstance(items[0], dict) else {}
                tracking = str(it0.get("barcode") or it0.get("value") or "").strip()
        elif isinstance(barcodes, list) and barcodes:
            it0 = barcodes[0] if isinstance(barcodes[0], dict) else {}
            tracking = str(it0.get("barcode") or it0.get("value") or "").strip()
    except Exception:
        tracking = ""

    if tracking:
        order.tracking_number = tracking

    pdf = client.get_sticker_pdf(
        parcel_ids=[parcel_id],
        layout="LAYOUT_10x15",
        label_orientation="PORTRAIT",
        include_cn23=False,
        include_manifest=False,
    )

    if store_on_order:
        filename = f"unisend_label_10x15_order_{order.id}_{parcel_id}.pdf"
        order.shipping_label_pdf.save(filename, ContentFile(pdf), save=False)
        order.shipping_label_generated_at = timezone.now()
        order.delivery_status = Order.DeliveryStatus.LABEL_CREATED
        order.save(update_fields=[
            "shipping_label_pdf",
            "shipping_label_generated_at",
            "delivery_status",
            "tracking_number",
            "updated_at",
        ])

    return pdf


def generate_labels_pdf_for_orders(
    orders: Iterable[Order],
    *,
    client: UnisendClient | None = None,
) -> tuple[bytes, list[tuple[int, str]]]:
    client = client or UnisendClient()

    parcel_ids: list[int] = []
    by_order_id: dict[int, int] = {}
    updated: list[tuple[int, str]] = []

    for o in orders:
        pid = ensure_unisend_parcel(o, client=client)
        parcel_ids.append(pid)
        by_order_id[o.id] = pid

    if not parcel_ids:
        raise RuntimeError("Nėra Unisend parcelIds lipdukų generavimui")

    client.initiate_shipping(parcel_ids=parcel_ids, process_async=False)

    # Try to backfill tracking numbers.
    try:
        barcodes = client.list_barcodes(parcel_ids=parcel_ids)
        by_parcel: dict[int, str] = {}
        items: list[Any] = []
        if isinstance(barcodes, dict) and isinstance(barcodes.get("items"), list):
            items = barcodes.get("items")
        elif isinstance(barcodes, list):
            items = barcodes

        for it in items:
            if not isinstance(it, dict):
                continue
            pid = it.get("parcelId") or it.get("id")
            try:
                pid_int = int(pid)
            except Exception:
                continue
            code = str(it.get("barcode") or it.get("value") or "").strip()
            if code:
                by_parcel[pid_int] = code

        for o in orders:
            pid = by_order_id.get(o.id)
            if not pid:
                continue
            t = by_parcel.get(pid, "")
            if t and not (o.tracking_number or "").strip():
                o.tracking_number = t
                o.save(update_fields=["tracking_number", "updated_at"])
            updated.append((o.id, t or str(pid)))
    except Exception:
        for o in orders:
            pid = by_order_id.get(o.id)
            if pid:
                updated.append((o.id, str(pid)))

    pdf = client.get_sticker_pdf(
        parcel_ids=parcel_ids,
        layout="LAYOUT_10x15",
        label_orientation="PORTRAIT",
        include_cn23=False,
        include_manifest=False,
    )

    return pdf, updated
