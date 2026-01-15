from django.contrib import admin

from .models import Site, SiteConfig


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "primary_domain",
        "default_language_code",
        "default_country_code",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active",)
    search_fields = ("code", "primary_domain")
    ordering = ("code",)


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    list_display = (
        "site",
        "default_from_email",
        "smtp_host",
        "smtp_port",
        "smtp_use_tls",
        "is_smtp_configured",
        "updated_at",
    )
    list_filter = ("smtp_use_tls", "smtp_use_ssl")
    search_fields = ("site__code", "site__primary_domain", "default_from_email", "smtp_host")
    autocomplete_fields = ("site",)

    fieldsets = (
        (None, {"fields": ("site",)}),
        (
            "Email",
            {
                "fields": (
                    "default_from_email",
                    "smtp_host",
                    "smtp_port",
                    "smtp_user",
                    "smtp_password",
                    "smtp_use_tls",
                    "smtp_use_ssl",
                    "smtp_timeout",
                )
            },
        ),
        (
            "Legal",
            {
                "fields": (
                    "terms_url",
                    "privacy_url",
                    "terms_version",
                    "privacy_version",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Neopay",
            {
                "fields": (
                    "neopay_project_id",
                    "neopay_project_key",
                    "neopay_client_redirect_url",
                    "neopay_enable_bank_preselect",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Frontend routes",
            {
                "fields": (
                    "category_path_template",
                    "brand_path_template",
                    "cms_page_path_template",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(boolean=True, description="SMTP configured")
    def is_smtp_configured(self, obj: SiteConfig) -> bool:
        return bool((obj.smtp_host or "").strip())
