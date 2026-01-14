from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template import Context, Engine, TemplateDoesNotExist
from django.utils import timezone

from api.i18n import get_default_language_code, normalize_language_code, translation_fallback_chain
from api.models import SiteConfig

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
    language_code: str | None = None,
    site_id: int | None = None,
) -> SendEmailResult:
    """Send an email based on a DB-stored template.

    Returns success flag + OutboundEmail log id.
    """

    resolved_language_code = normalize_language_code(language_code) or get_default_language_code()
    langs = translation_fallback_chain(resolved_language_code)

    tmpl_qs = EmailTemplate.objects.filter(
        key=template_key,
        is_active=True,
        language_code__in=langs,
    )
    if site_id is not None:
        tmpl_qs = tmpl_qs.filter(site_id=int(site_id))
    templates = list(
        tmpl_qs
    )
    order_index = {lang: i for i, lang in enumerate(langs)}
    template = None
    best_idx = 10_000
    for t in templates:
        idx = order_index.get(normalize_language_code(getattr(t, "language_code", "")), 10_000)
        if idx < best_idx:
            template = t
            best_idx = idx
    if not template:
        outbound = OutboundEmail.objects.create(
            site_id=(int(site_id) if site_id is not None else None),
            to_email=to_email,
            template_key=template_key,
            subject="",
            status=OutboundEmail.Status.FAILED,
            error_message="Template not found or inactive",
        )
        return SendEmailResult(ok=False, outbound_id=outbound.id, error=outbound.error_message)

    resolved_from_email = from_email
    connection = None

    if site_id is not None:
        cfg = SiteConfig.objects.filter(site_id=int(site_id)).only(
            "default_from_email",
            "smtp_host",
            "smtp_port",
            "smtp_user",
            "smtp_password",
            "smtp_use_tls",
            "smtp_use_ssl",
            "smtp_timeout",
        ).first()
        if cfg is not None:
            if not resolved_from_email:
                resolved_from_email = (cfg.default_from_email or "").strip() or None

            smtp_host = (cfg.smtp_host or "").strip()
            if smtp_host:
                connection = get_connection(
                    backend="django.core.mail.backends.smtp.EmailBackend",
                    host=smtp_host,
                    port=int(cfg.smtp_port or 587),
                    username=(cfg.smtp_user or "").strip() or None,
                    password=(cfg.smtp_password or "") or None,
                    use_tls=bool(cfg.smtp_use_tls),
                    use_ssl=bool(cfg.smtp_use_ssl),
                    timeout=int(cfg.smtp_timeout or 10),
                )

    if not resolved_from_email:
        resolved_from_email = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip() or None

    render_ctx: dict[str, Any] = {
        "site_name": getattr(settings, "SITE_NAME", ""),
        "support_email": resolved_from_email or "",
        **(context or {}),
    }

    try:
        subject = _render_django_template(template.subject, render_ctx).strip()
        body_text = _render_django_template(template.body_text, render_ctx)
        body_html = _render_django_template(
            template.body_html, render_ctx) if template.body_html else ""
    except Exception as exc:
        outbound = OutboundEmail.objects.create(
            site_id=(int(site_id) if site_id is not None else None),
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
        site_id=(int(site_id) if site_id is not None else None),
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
            from_email=resolved_from_email,
            to=[to_email],
            connection=connection,
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
