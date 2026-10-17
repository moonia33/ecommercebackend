from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def issue_invoice_task(self, order_id: int):
    """Issue sales invoice for a delivered order: snapshot -> PDF -> email."""
    from checkout.models import Order

    from .services import (
        ensure_invoice_pdf,
        issue_invoice_for_order,
        send_invoice_email,
    )

    try:
        order = Order.objects.select_related("shop", "user").get(pk=order_id)
    except Order.DoesNotExist:
        return

    invoice = issue_invoice_for_order(order)

    try:
        ensure_invoice_pdf(invoice)
    except Exception as exc:
        raise self.retry(exc=exc)

    try:
        send_invoice_email(invoice)
    except Exception as exc:
        raise self.retry(exc=exc)

    logger.info(
        "invoice issued: %s order=%s", invoice.number, order_id)
    return invoice.number
