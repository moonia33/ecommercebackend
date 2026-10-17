from datetime import timedelta

from celery import shared_task
from django.core.files.storage import default_storage
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_task(self, outbound_email_id: int):
    from .models import OutboundEmail

    try:
        outbound = OutboundEmail.objects.get(pk=outbound_email_id)
    except OutboundEmail.DoesNotExist:
        return

    if outbound.status == OutboundEmail.Status.SENT:
        return

    try:
        msg = EmailMultiAlternatives(
            subject=outbound.subject,
            body=outbound.body_text,
            to=[outbound.to_email],
        )
        if outbound.body_html:
            msg.attach_alternative(outbound.body_html, "text/html")
        for path in outbound.attachments or []:
            name = str(path).rsplit("/", 1)[-1] or "attachment"
            with default_storage.open(path, "rb") as f:
                msg.attach(name, f.read())
        msg.send(fail_silently=False)
    except Exception as exc:
        outbound.status = OutboundEmail.Status.FAILED
        outbound.error_message = str(exc)
        outbound.save(update_fields=["status", "error_message"])
        raise self.retry(exc=exc)

    outbound.status = OutboundEmail.Status.SENT
    outbound.sent_at = timezone.now()
    outbound.save(update_fields=["status", "sent_at"])


@shared_task
def retry_failed_emails_task():
    from .models import OutboundEmail

    failed = OutboundEmail.objects.filter(
        status=OutboundEmail.Status.FAILED,
    ).exclude(
        error_message__startswith="Template not found"
    ).exclude(
        error_message__startswith="Render failed"
    )[:50]

    for outbound in failed:
        outbound.status = OutboundEmail.Status.PENDING
        outbound.error_message = ""
        outbound.save(update_fields=["status", "error_message"])
        send_email_task.delay(outbound.id)

    # Sweep emails stuck in PENDING (e.g. broker was down when they were queued).
    stale_cutoff = timezone.now() - timedelta(minutes=10)
    stale = OutboundEmail.objects.filter(
        status=OutboundEmail.Status.PENDING,
        created_at__lt=stale_cutoff,
    )[:50]

    for outbound in stale:
        send_email_task.delay(outbound.id)
