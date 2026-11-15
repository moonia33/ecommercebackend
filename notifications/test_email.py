import smtplib
from unittest.mock import patch

from django.test import TestCase, override_settings

from notifications.models import EmailTemplate, OutboundEmail
from notifications.services import send_templated_email
from notifications.tasks import retry_failed_emails_task, send_email_task


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InvalidRecipientTests(TestCase):
    def setUp(self):
        EmailTemplate.objects.update_or_create(
            key="auth_otp_code",
            language_code="lt",
            defaults={"subject": "Kodas", "body_text": "{{ code }}", "is_active": True},
        )

    def test_invalid_address_is_not_queued(self):
        with patch("notifications.tasks.send_email_task.delay") as delay:
            result = send_templated_email(
                template_key="auth_otp_code", to_email="886541", context={"code": "1"}
            )
        self.assertFalse(result.ok)
        delay.assert_not_called()
        outbound = OutboundEmail.objects.get(pk=result.outbound_id)
        self.assertEqual(outbound.status, OutboundEmail.Status.FAILED)
        self.assertTrue(outbound.error_message.startswith("Invalid recipient"))

    def test_refused_recipient_fails_permanently_without_retry(self):
        outbound = OutboundEmail.objects.create(to_email="886541", subject="s", body_text="b")
        refused = smtplib.SMTPRecipientsRefused({"886541": (501, b"must contain a domain")})
        with patch("django.core.mail.EmailMultiAlternatives.send", side_effect=refused):
            send_email_task.apply(args=[outbound.id]).get()
        outbound.refresh_from_db()
        self.assertEqual(outbound.status, OutboundEmail.Status.FAILED)
        self.assertTrue(outbound.error_message.startswith("Recipient refused"))

        with patch("notifications.tasks.send_email_task.delay") as delay:
            retry_failed_emails_task()
        delay.assert_not_called()

    def test_temporary_recipient_refusal_is_retried(self):
        outbound = OutboundEmail.objects.create(to_email="a@example.com", subject="s", body_text="b")
        refused = smtplib.SMTPRecipientsRefused({"a@example.com": (450, b"mailbox busy")})
        with patch("django.core.mail.EmailMultiAlternatives.send", side_effect=refused):
            send_email_task.apply(args=[outbound.id])
        outbound.refresh_from_db()
        self.assertEqual(outbound.status, OutboundEmail.Status.FAILED)
        self.assertFalse(outbound.error_message.startswith("Recipient refused"))

    def test_transient_failure_is_still_retried(self):
        outbound = OutboundEmail.objects.create(
            to_email="a@example.com",
            subject="s",
            status=OutboundEmail.Status.FAILED,
            error_message="Connection unexpectedly closed",
        )
        with patch("notifications.tasks.send_email_task.delay") as delay:
            retry_failed_emails_task()
        delay.assert_called_once_with(outbound.id)

    def test_otp_request_rejects_non_email(self):
        response = self.client.post(
            "/api/v1/auth/otp/request", {"email": "886541"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(OutboundEmail.objects.exists())
