from django.db import models


class EmailTemplate(models.Model):
    key = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=200, blank=True)

    subject = models.CharField(max_length=255)
    body_text = models.TextField(help_text="Django template syntax")
    body_html = models.TextField(blank=True, help_text="Optional HTML body")

    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self) -> str:
        return self.key


class OutboundEmail(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    to_email = models.EmailField()
    template_key = models.SlugField(max_length=100, blank=True)
    subject = models.CharField(max_length=255)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.to_email} [{self.status}]"
