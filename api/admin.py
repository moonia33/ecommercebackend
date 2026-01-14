from django.contrib import admin

from .models import Site


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
