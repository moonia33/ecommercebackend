from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import Context, Engine, TemplateDoesNotExist
from django.utils import timezone

from .models import EmailTemplate, OutboundEmail


@dataclass(frozen=True)
class SendEmailResult:
    ok: bool
    outbound_id: int | None
    error: str | None = None


def _render_django_template(source: str, context: dict[str, Any]) -> str:
    engine = Engine.get_default()
    template = engine.from_string(source)
    return template.render(Context(context))


def send_templated_email(
    *,
    template_key: str,
    to_email: str,
    context: dict[str, Any] | None = None,
    from_email: str | None = None,
) -> SendEmailResult:
    """Send an email based on a DB-stored template.

    Returns success flag + OutboundEmail log id.
    """

    template = EmailTemplate.objects.filter(
        key=template_key, is_active=True).first()
    if not template:
        outbound = OutboundEmail.objects.create(
            to_email=to_email,
            template_key=template_key,
            subject="",
            status=OutboundEmail.Status.FAILED,
            error_message="Template not found or inactive",
        )
        return SendEmailResult(ok=False, outbound_id=outbound.id, error=outbound.error_message)

    render_ctx: dict[str, Any] = {
        "site_name": getattr(settings, "SITE_NAME", ""),
        "support_email": getattr(settings, "DEFAULT_FROM_EMAIL", ""),
        **(context or {}),
    }

    try:
        subject = _render_django_template(template.subject, render_ctx).strip()
        body_text = _render_django_template(template.body_text, render_ctx)
        body_html = _render_django_template(
            template.body_html, render_ctx) if template.body_html else ""
    except Exception as exc:
        outbound = OutboundEmail.objects.create(
            to_email=to_email,
            template_key=template_key,
            subject=template.subject,
            body_text=template.body_text,
            body_html=template.body_html,
            status=OutboundEmail.Status.FAILED,
            error_message=f"Render failed: {exc}",
        )
        return SendEmailResult(ok=False, outbound_id=outbound.id, error=outbound.error_message)

    outbound = OutboundEmail.objects.create(
        to_email=to_email,
        template_key=template_key,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        status=OutboundEmail.Status.PENDING,
    )

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=body_text,
            from_email=from_email or getattr(
                settings, "DEFAULT_FROM_EMAIL", None) or None,
            to=[to_email],
        )
        if body_html:
            msg.attach_alternative(body_html, "text/html")
        msg.send(fail_silently=False)
    except Exception as exc:
        outbound.status = OutboundEmail.Status.FAILED
        outbound.error_message = str(exc)
        outbound.save(update_fields=["status", "error_message"])
        return SendEmailResult(ok=False, outbound_id=outbound.id, error=outbound.error_message)

    outbound.status = OutboundEmail.Status.SENT
    outbound.sent_at = timezone.now()
    outbound.save(update_fields=["status", "sent_at"])
    return SendEmailResult(ok=True, outbound_id=outbound.id)
