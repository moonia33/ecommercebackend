from __future__ import annotations

# Localized labels for order/payment/fulfillment status codes.
# Single source of truth — keep in sync with model TextChoices.

STATUS_LABELS: dict[str, dict[str, dict[str, str]]] = {
    "order_status": {
        "pending_payment": {
            "lt": "Laukiama apmokėjimo",
            "en": "Pending payment",
            "pl": "Oczekuje na płatność",
        },
        "paid": {
            "lt": "Apmokėta",
            "en": "Paid",
            "pl": "Opłacone",
        },
        "cancelled": {
            "lt": "Atšaukta",
            "en": "Cancelled",
            "pl": "Anulowane",
        },
    },
    "delivery_status": {
        "pending": {
            "lt": "Ruošiama",
            "en": "Pending",
            "pl": "W przygotowaniu",
        },
        "label_created": {
            "lt": "Sukurta siuntos etiketė",
            "en": "Label created",
            "pl": "Utworzono etykietę",
        },
        "shipped": {
            "lt": "Išsiųsta",
            "en": "Shipped",
            "pl": "Wysłano",
        },
        "delivered": {
            "lt": "Pristatyta",
            "en": "Delivered",
            "pl": "Dostarczono",
        },
        "cancelled": {
            "lt": "Atšaukta",
            "en": "Cancelled",
            "pl": "Anulowano",
        },
        "error": {
            "lt": "Klaida",
            "en": "Error",
            "pl": "Błąd",
        },
    },
    "fulfillment_mode": {
        "stock": {
            "lt": "Iš sandėlio",
            "en": "From stock",
            "pl": "Z magazynu",
        },
        "dropship": {
            "lt": "Tiesiogiai iš tiekėjo",
            "en": "Dropship",
            "pl": "Bezpośrednio od dostawcy",
        },
        "mixed": {
            "lt": "Mišrus",
            "en": "Mixed",
            "pl": "Mieszany",
        },
    },
    "supplier_reservation_status": {
        "not_needed": {
            "lt": "Nereikalinga",
            "en": "Not needed",
            "pl": "Niepotrzebna",
        },
        "pending": {
            "lt": "Rezervuojama",
            "en": "Pending",
            "pl": "Oczekuje",
        },
        "reserved": {
            "lt": "Rezervuota",
            "en": "Reserved",
            "pl": "Zarezerwowano",
        },
        "failed": {
            "lt": "Rezervacija nepavyko",
            "en": "Failed",
            "pl": "Nie powiodło się",
        },
        "cancelled": {
            "lt": "Atšaukta",
            "en": "Cancelled",
            "pl": "Anulowano",
        },
    },
    "payment_status": {
        "pending": {
            "lt": "Laukiama",
            "en": "Pending",
            "pl": "Oczekuje",
        },
        "redirected": {
            "lt": "Nukreipta į banką",
            "en": "Redirected",
            "pl": "Przekierowano",
        },
        "succeeded": {
            "lt": "Apmokėta",
            "en": "Succeeded",
            "pl": "Opłacono",
        },
        "failed": {
            "lt": "Nepavyko",
            "en": "Failed",
            "pl": "Nie powiodło się",
        },
        "cancelled": {
            "lt": "Atšaukta",
            "en": "Cancelled",
            "pl": "Anulowano",
        },
    },
    "payment_provider": {
        "bank_transfer": {
            "lt": "Bankinis pavedimas",
            "en": "Bank transfer",
            "pl": "Przelew bankowy",
        },
        "montonio": {
            "lt": "Montonio",
            "en": "Montonio",
            "pl": "Montonio",
        },
    },
}


def get_status_label(domain: str, code: str, language_code: str = "") -> str:
    labels = STATUS_LABELS.get(domain, {}).get(code)
    if not labels:
        return code or ""
    lang = (language_code or "").split("-")[0].strip().lower()
    return labels.get(lang) or labels.get("en") or code


def get_status_options(domain: str, language_code: str = "") -> list[dict[str, str]]:
    return [
        {"code": code, "label": get_status_label(domain, code, language_code)}
        for code in STATUS_LABELS.get(domain, {})
    ]
