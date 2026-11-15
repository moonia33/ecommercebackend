import logging
import smtplib
from datetime import timedelta

from celery import shared_task
from django.core.files.storage import storages
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

logger = logging.getLogger(__name__)


def _resolve_attachment(ref: str):
    """Attachment refs are 'storage_alias:path'; bare path -> default.

    Falls back to default when the alias isn't configured (e.g. no
    private bucket in dev — files then live in default storage).
    """
    if ":" in ref:
        alias, _, path = ref.partition(":")
        if alias and alias != "default":
            try:
                return storages[alias], path
            except Exception:
                pass
        ref = path
    return storages["default"], ref


def _permanently_refused(exc: smtplib.SMTPRecipientsRefused) -> bool:
    """Visi gavėjai atmesti 5xx kodu (4xx – laikina, kartojama)."""
    codes = [code for code, _msg in (exc.recipients or {}).values()]
    return bool(codes) and all(int(code) >= 500 for code in codes)


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
        for ref in outbound.attachments or []:
            storage, path = _resolve_attachment(str(ref))
            name = path.rsplit("/", 1)[-1] or "attachment"
            with storage.open(path, "rb") as f:
                msg.attach(name, f.read())
        msg.send(fail_silently=False)
    except smtplib.SMTPRecipientsRefused as exc:
        if not _permanently_refused(exc):
            outbound.status = OutboundEmail.Status.FAILED
            outbound.error_message = str(exc)
            outbound.save(update_fields=["status", "error_message"])
            raise self.retry(exc=exc)
        # Neteisingas gavėjas (5xx) – kartojimas nepadės (ir nekelia Sentry klaidos kas kartą).
        from .services import RECIPIENT_REFUSED_PREFIX

        logger.warning("Email %s recipient refused: %s", outbound.pk, exc)
        outbound.status = OutboundEmail.Status.FAILED
        outbound.error_message = f"{RECIPIENT_REFUSED_PREFIX}: {exc}"[:2000]
        outbound.save(update_fields=["status", "error_message"])
        return
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
    from .services import PERMANENT_ERROR_PREFIXES

    failed = OutboundEmail.objects.filter(status=OutboundEmail.Status.FAILED)
    for prefix in PERMANENT_ERROR_PREFIXES:
        failed = failed.exclude(error_message__startswith=prefix)
    failed = failed[:50]

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
