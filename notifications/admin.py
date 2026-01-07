from django.contrib import admin

from .models import EmailTemplate, OutboundEmail


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("key", "name", "subject")
    ordering = ("key",)


@admin.register(OutboundEmail)
class OutboundEmailAdmin(admin.ModelAdmin):
    list_display = ("to_email", "template_key",
                    "status", "created_at", "sent_at")
    list_filter = ("status", "template_key")
    search_fields = ("to_email", "subject", "template_key")
    ordering = ("-created_at",)

    readonly_fields = (
        "to_email",
        "template_key",
        "subject",
        "body_text",
        "body_html",
        "status",
        "error_message",
        "created_at",
        "sent_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
