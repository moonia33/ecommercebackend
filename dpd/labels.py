from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from checkout.models import Order

from .client import DpdApiError, DpdClient


class DpdLabelConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class DpdShipmentConfig:
    sender_name: str
    sender_phone: str
    sender_street: str
    sender_city: str
    sender_postal_code: str
    sender_country: str
    payer_code: str
    service_alias_courier: str
    service_alias_locker: str


def _get_shipment_cfg() -> DpdShipmentConfig:
    # 1) DB config (admin)
    try:
        from .models import DpdConfig as DpdDbConfig

        cfg = DpdDbConfig.objects.order_by("id").first()
        if cfg:
            return DpdShipmentConfig(
                sender_name=str(cfg.sender_name or "").strip(),
                sender_phone=str(cfg.sender_phone or "").strip(),
                sender_street=str(cfg.sender_street or "").strip(),
                sender_city=str(cfg.sender_city or "").strip(),
                sender_postal_code=str(cfg.sender_postal_code or "").strip(),
                sender_country=str(cfg.sender_country or "").strip(),
                payer_code=str(cfg.payer_code or "").strip(),
                service_alias_courier=str(
                    cfg.service_alias_courier or "").strip(),
                service_alias_locker=str(
                    cfg.service_alias_locker or "").strip(),
            )
    except Exception:
        # DB may be unavailable during migrations/startup
        pass

    # 2) fallback to settings/env
    return DpdShipmentConfig(
        sender_name=str(
            getattr(settings, "DPD_SENDER_NAME", "") or "").strip(),
        sender_phone=str(
            getattr(settings, "DPD_SENDER_PHONE", "") or "").strip(),
        sender_street=str(
            getattr(settings, "DPD_SENDER_STREET", "") or "").strip(),
        sender_city=str(
            getattr(settings, "DPD_SENDER_CITY", "") or "").strip(),
        sender_postal_code=str(
            getattr(settings, "DPD_SENDER_POSTAL_CODE", "") or "").strip(),
        sender_country=str(
            getattr(settings, "DPD_SENDER_COUNTRY", "") or "").strip(),
        payer_code=str(getattr(settings, "DPD_PAYER_CODE", "") or "").strip(),
        service_alias_courier=str(
            getattr(settings, "DPD_SERVICE_ALIAS_COURIER", "") or "").strip(),
        service_alias_locker=str(
            getattr(settings, "DPD_SERVICE_ALIAS_LOCKER", "") or "").strip(),
    )


def _require(value: str, *, key: str) -> str:
    v = (value or "").strip()
    if not v:
        raise DpdLabelConfigError(f"Trūksta konfigūracijos: {key}")
    return v


def _estimate_order_weight_kg(order: Order) -> float:
    total_g = 0
    for ln in order.lines.select_related("variant").all():
        if ln.variant_id and getattr(ln.variant, "weight_g", 0):
            total_g += int(ln.variant.weight_g) * int(ln.qty)

    if total_g <= 0:
        return 1.0

    kg = Decimal(total_g) / Decimal(1000)
    if kg < Decimal("0.1"):
        kg = Decimal("0.1")

    if kg > Decimal("31.5"):
        kg = Decimal("31.5")

    return float(kg)


def _sender_address(cfg: DpdShipmentConfig) -> dict[str, Any]:
    return {
        "name": _require(cfg.sender_name, key="DPD_SENDER_NAME"),
        "phone": _require(cfg.sender_phone, key="DPD_SENDER_PHONE"),
        "street": _require(cfg.sender_street, key="DPD_SENDER_STREET"),
        "city": _require(cfg.sender_city, key="DPD_SENDER_CITY"),
        "postalCode": _require(cfg.sender_postal_code, key="DPD_SENDER_POSTAL_CODE"),
        "country": _require(cfg.sender_country, key="DPD_SENDER_COUNTRY"),
    }


def _receiver_address_for_order(order: Order) -> dict[str, Any]:
    shipping_method = (order.shipping_method or "").strip()

    full_name = (order.shipping_full_name or "").strip() or "Customer"
    phone = (order.shipping_phone or "").strip()

    if shipping_method == "dpd_locker":
        pudo_id = (order.pickup_point_id or "").strip()
        if not pudo_id:
            raise DpdLabelConfigError(
                "dpd_locker: trūksta pickup point (pickup_point_id)")
        if not phone:
            raise DpdLabelConfigError(
                "dpd_locker: trūksta telefono (shipping_phone)")

        return {
            "name": full_name,
            "phone": phone,
            "pudoId": pudo_id,
        }

    # Courier
    street = (order.shipping_line1 or "").strip()
    city = (order.shipping_city or "").strip()
    postal_code = (order.shipping_postal_code or "").strip()
    country = (order.shipping_country_code or order.country_code or "").strip()

    missing = []
    if not street:
        missing.append("shipping_line1")
    if not city:
        missing.append("shipping_city")
    if not postal_code:
        missing.append("shipping_postal_code")
    if not country:
        missing.append("shipping_country_code")
    if not phone:
        missing.append("shipping_phone")

    if missing:
        raise DpdLabelConfigError(
            f"dpd_courier: trūksta laukų: {', '.join(missing)}")

    return {
        "name": full_name,
        "phone": phone,
        "street": street,
        "city": city,
        "postalCode": postal_code,
        "country": country,
    }


def _service_alias_for_order(order: Order, cfg: DpdShipmentConfig) -> str:
    shipping_method = (order.shipping_method or "").strip()
    if shipping_method == "dpd_locker":
        return _require(cfg.service_alias_locker, key="DPD_SERVICE_ALIAS_LOCKER")
    return _require(cfg.service_alias_courier, key="DPD_SERVICE_ALIAS_COURIER")


def _extract_service_aliases(obj: Any) -> list[str]:
    aliases: list[str] = []
    if isinstance(obj, list):
        for it in obj:
            aliases.extend(_extract_service_aliases(it))
        return aliases
    if isinstance(obj, dict):
        alias = obj.get("serviceAlias")
        if isinstance(alias, str) and alias.strip():
            aliases.append(alias.strip())
        additional = obj.get("additionalServices")
        if isinstance(additional, list):
            aliases.extend(_extract_service_aliases(additional))
        return aliases
    return aliases


def _autodetect_pudo_service_alias(
    order: Order,
    *,
    cfg: DpdShipmentConfig,
    client: DpdClient,
) -> str | None:
    country_from = (cfg.sender_country or "").strip()
    country_to = ""
    if getattr(order, "pickup_locker_id", None) and getattr(order, "pickup_locker", None):
        country_to = str(getattr(order.pickup_locker, "country_code", "") or "").strip()
    if not country_to:
        country_to = str(order.shipping_country_code or order.country_code or "").strip()

    if not country_from or not country_to:
        return None

    def _is_pudo_service(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        st = item.get("serviceType")
        if isinstance(st, list):
            return any(str(x).strip().lower() == "pudo" for x in st)
        if isinstance(st, str):
            return st.strip().lower() == "pudo"
        return False

    payer_code = (cfg.payer_code or "").strip() or None

    # According to DPD docs, serviceType accepts e.g. "Pudo".
    # Some accounts may require payerCode to see specific services.
    attempts: list[dict[str, Any]] = []
    attempts.append({"service_type": "Pudo", "payer_code": payer_code})
    attempts.append({"service_type": "PUDO", "payer_code": payer_code})
    attempts.append({"service_type": None, "payer_code": payer_code})
    if payer_code is None:
        attempts.append({"service_type": "Pudo", "payer_code": None})
        attempts.append({"service_type": None, "payer_code": None})

    for at in attempts:
        data = client.list_services(
            country_from=country_from,
            country_to=country_to,
            service_type=at["service_type"],
            payer_code=at["payer_code"],
        )

        # If we didn't filter by service_type (or API ignores it), filter locally.
        if at["service_type"] is None and isinstance(data, list):
            data = [x for x in data if _is_pudo_service(x)]

        aliases = _extract_service_aliases(data)
        if aliases:
            return aliases[0]

    return None


def build_shipment_dto(order: Order, *, cfg: DpdShipmentConfig | None = None) -> dict[str, Any]:
    cfg = cfg or _get_shipment_cfg()

    weight_kg = _estimate_order_weight_kg(order)
    dto: dict[str, Any] = {
        "senderAddress": _sender_address(cfg),
        "receiverAddress": _receiver_address_for_order(order),
        "service": {"serviceAlias": _service_alias_for_order(order, cfg)},
        "parcels": [{"weight": weight_kg}],
        "shipmentReferences": [str(order.id)],
        "contentDescription": "e-commerce",
    }

    if cfg.payer_code:
        dto["payerCode"] = cfg.payer_code

    return dto


def ensure_dpd_shipment(order: Order, *, client: DpdClient | None = None) -> tuple[str, str]:
    if (order.carrier_code or "").strip() == "dpd" and (order.tracking_number or "").strip():
        return (order.carrier_shipment_id or ""), order.tracking_number

    if (order.shipping_method or "").strip() not in {"dpd_locker", "dpd_courier"}:
        raise DpdLabelConfigError(
            "Šis užsakymas nėra DPD (dpd_locker/dpd_courier).")

    client = client or DpdClient()
    cfg = _get_shipment_cfg()
    dto = build_shipment_dto(order, cfg=cfg)

    try:
        created = client.create_shipments(shipments=[dto])
    except DpdApiError as e:
        shipping_method = (order.shipping_method or "").strip()
        msg = str(e)
        if (
            shipping_method == "dpd_locker"
            and "main service type is not PUDO" in msg
        ):
            pudo_alias = _autodetect_pudo_service_alias(order, cfg=cfg, client=client)
            if not pudo_alias:
                raise DpdLabelConfigError(
                    "DPD: nurodytas paštomatas (pudoId), bet parinktas ne-PUDO serviceAlias. "
                    "Nepavyko automatiškai parinkti PUDO serviceAlias per /services – patikrinkite DPD_SERVICE_ALIAS_LOCKER."
                ) from e
            dto2 = {**dto, "service": {"serviceAlias": pudo_alias}}
            created = client.create_shipments(shipments=[dto2])
        else:
            raise
    if not created:
        raise RuntimeError("DPD create_shipments grąžino tuščią atsakymą")

    shipment = created[0]
    shipment_id = str(shipment.get("id") or "").strip()
    parcel_numbers = shipment.get("parcelNumbers") or []
    parcel = str(parcel_numbers[0]).strip() if parcel_numbers else ""

    # DPD docs: parcelNumbers may be empty by default; parcel identifiers can be returned
    # only in specific flows (e.g. when label requested together with creation).
    # Still store shipment_id so we can generate labels later using shipmentIds.
    order.carrier_code = "dpd"
    order.carrier_shipment_id = shipment_id
    if parcel:
        order.tracking_number = parcel
    order.save(update_fields=["carrier_code", "carrier_shipment_id", "tracking_number", "updated_at"])

    return shipment_id, parcel


def generate_a6_label_pdf_for_order(
    order: Order,
    *,
    client: DpdClient | None = None,
    store_on_order: bool = True,
) -> bytes:
    client = client or DpdClient()
    shipment_id, parcel = ensure_dpd_shipment(order, client=client)

    payload: dict[str, Any] = {
        "downloadLabel": True,
        "emailLabel": False,
        "labelFormat": "application/pdf",
        "paperSize": "A6",
    }

    # Prefer generating by shipmentIds – works even if parcelNumbers were not returned at creation time.
    if shipment_id:
        payload["shipmentIds"] = [shipment_id]
    elif parcel:
        payload["parcelNumbers"] = [parcel]
    else:
        raise RuntimeError("DPD: nerastas nei shipment_id, nei parcelNumber lipduko generavimui")

    pdf = client.create_labels_pdf(
        payload=payload, endpoint="/shipments/labels")

    # Best-effort: retrieve parcelNumbers (tracking) after label generation.
    # DPD may not return parcelNumbers during shipment creation.
    if not parcel and shipment_id:
        try:
            shipments = client.get_shipments(ids=[shipment_id])
            sh = shipments[0] if shipments else {}
            pn = sh.get("parcelNumbers") or []
            parcel = str(pn[0]).strip() if pn else ""
        except Exception:
            parcel = ""

    if parcel and not (order.tracking_number or "").strip():
        order.tracking_number = parcel

    if store_on_order:
        suffix = parcel or shipment_id or ""
        filename = f"dpd_label_a6_order_{order.id}_{suffix}.pdf"
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


def generate_a6_labels_pdf_for_orders(
    orders: Iterable[Order],
    *,
    client: DpdClient | None = None,
) -> tuple[bytes, list[tuple[int, str]]]:
    client = client or DpdClient()
    cfg = _get_shipment_cfg()

    parcels: list[str] = []
    shipment_ids: list[str] = []
    updated: list[tuple[int, str]] = []

    to_create: list[Order] = []
    for o in orders:
        existing_shipment_id = (o.carrier_shipment_id or "").strip()
        existing_parcel = (o.tracking_number or "").strip()
        if (o.carrier_code or "").strip() == "dpd" and (existing_shipment_id or existing_parcel):
            if existing_shipment_id:
                shipment_ids.append(existing_shipment_id)
                updated.append((o.id, existing_parcel or existing_shipment_id))
            elif existing_parcel:
                parcels.append(existing_parcel)
                updated.append((o.id, existing_parcel))
        else:
            to_create.append(o)

    if to_create:
        shipment_payloads = [build_shipment_dto(o, cfg=cfg) for o in to_create]
        try:
            created = client.create_shipments(shipments=shipment_payloads)
        except DpdApiError as e:
            msg = str(e)
            if "main service type is not PUDO" in msg:
                # One or more locker shipments have pudoId but non-PUDO serviceAlias.
                # Try to auto-detect a PUDO alias and retry for all dpd_locker orders.
                pudo_alias = None
                for o in to_create:
                    if (o.shipping_method or "").strip() == "dpd_locker":
                        pudo_alias = _autodetect_pudo_service_alias(o, cfg=cfg, client=client)
                        if pudo_alias:
                            break

                if not pudo_alias:
                    raise DpdLabelConfigError(
                        "DPD: nurodytas paštomatas (pudoId), bet parinktas ne-PUDO serviceAlias. "
                        f"Nepavyko automatiškai parinkti PUDO serviceAlias per /services (countryFrom={cfg.sender_country}, countryTo={getattr(o, 'shipping_country_code', '') or getattr(o, 'country_code', '')}). "
                        "Patikrinkite DPD_SERVICE_ALIAS_LOCKER / DPD config -> service alias locker ir (jei turite) užpildykite payer code."
                    ) from e

                shipment_payloads2: list[dict[str, Any]] = []
                for dto, o in zip(shipment_payloads, to_create):
                    if (o.shipping_method or "").strip() == "dpd_locker":
                        shipment_payloads2.append({**dto, "service": {"serviceAlias": pudo_alias}})
                    else:
                        shipment_payloads2.append(dto)

                created = client.create_shipments(shipments=shipment_payloads2)
            else:
                raise

        by_ref: dict[str, dict[str, Any]] = {}
        for sh in created:
            refs = sh.get("shipmentReferences") or []
            if refs:
                by_ref[str(refs[0])] = sh

        for idx, o in enumerate(to_create):
            sh = by_ref.get(str(o.id))
            if sh is None and idx < len(created):
                sh = created[idx]
            if not sh:
                raise RuntimeError(
                    f"DPD create_shipments: nerasta siunta order:{o.id}")

            shipment_id = str(sh.get("id") or "").strip()
            parcel_numbers = sh.get("parcelNumbers") or []
            parcel = str(parcel_numbers[0]).strip() if parcel_numbers else ""

            o.carrier_code = "dpd"
            o.carrier_shipment_id = shipment_id
            if parcel:
                o.tracking_number = parcel
            o.save(
                update_fields=[
                    "carrier_code",
                    "carrier_shipment_id",
                    "tracking_number",
                    "updated_at",
                ]
            )

            if shipment_id:
                shipment_ids.append(shipment_id)
                updated.append((o.id, parcel or shipment_id))
            elif parcel:
                parcels.append(parcel)
                updated.append((o.id, parcel))

    if not shipment_ids and not parcels:
        raise RuntimeError("Nėra shipmentIds/parcelNumbers lipdukų generavimui")

    payload: dict[str, Any] = {
        "downloadLabel": True,
        "emailLabel": False,
        "labelFormat": "application/pdf",
        "paperSize": "A6",
    }
    # /shipments/labels allows either shipmentIds or parcelNumbers (not both).
    # Prefer shipmentIds (works even if parcelNumbers were not returned at creation time).
    if shipment_ids:
        payload["shipmentIds"] = shipment_ids
    else:
        payload["parcelNumbers"] = parcels
    pdf = client.create_labels_pdf(
        payload=payload, endpoint="/shipments/labels")

    # Best-effort: backfill tracking numbers for shipments created without parcelNumbers.
    if shipment_ids:
        try:
            ships = client.get_shipments(ids=shipment_ids)
            by_id: dict[str, dict[str, Any]] = {}
            for sh in ships:
                sid = str(sh.get("id") or "").strip()
                if sid:
                    by_id[sid] = sh

            for o in orders:
                if (o.carrier_code or "").strip() != "dpd":
                    continue
                if (o.tracking_number or "").strip():
                    continue
                sid = (o.carrier_shipment_id or "").strip()
                if not sid:
                    continue
                sh = by_id.get(sid) or {}
                pn = sh.get("parcelNumbers") or []
                parcel = str(pn[0]).strip() if pn else ""
                if parcel:
                    o.tracking_number = parcel
                    o.save(update_fields=["tracking_number", "updated_at"])
        except Exception:
            pass

    return pdf, updated
