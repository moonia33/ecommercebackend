from __future__ import annotations

from django import forms
from django.contrib import admin
from django.contrib.admin import helpers
from django.http import HttpResponseRedirect

from .client import UnisendClient
from .models import UnisendApiConfig, UnisendTerminal


@admin.register(UnisendApiConfig)
class UnisendApiConfigAdmin(admin.ModelAdmin):
    list_display = ("__str__", "base_url", "username", "updated_at")
    fields = (
        "base_url",
        "username",
        "password",
        "client_system",
        "access_token",
        "refresh_token",
        "token_expires_at",
        "sender_name",
        "sender_email",
        "sender_phone",
        "sender_country",
        "sender_locality",
        "sender_postal_code",
        "sender_street",
        "sender_building",
        "sender_flat",
        "updated_at",
    )
    readonly_fields = ("updated_at",)

    class Form(forms.ModelForm):
        class Meta:
            model = UnisendApiConfig
            fields = "__all__"
            widgets = {
                "password": forms.PasswordInput(render_value=True),
                "access_token": forms.PasswordInput(render_value=True),
                "refresh_token": forms.PasswordInput(render_value=True),
            }

    form = Form

    def has_add_permission(self, request):
        return not UnisendApiConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if not UnisendApiConfig.objects.exists():
            UnisendApiConfig.get_solo()
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(UnisendTerminal)
class UnisendTerminalAdmin(admin.ModelAdmin):
    list_display = ("terminal_id", "country_code", "locality", "name", "postal_code", "is_active", "updated_at")
    list_filter = ("is_active", "country_code", "locality")
    search_fields = ("terminal_id", "locality", "name", "street", "postal_code")
    ordering = ("country_code", "locality", "name")

    actions = (
        "sync_lt",
        "sync_lv",
        "sync_ee",
        "sync_all",
    )

    def changelist_view(self, request, extra_context=None):
        if request.method == "POST":
            action = request.POST.get("action")
            selected = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)

            if action in {"sync_lt", "sync_lv", "sync_ee", "sync_all"} and not selected:
                func = getattr(self, action, None)
                if callable(func):
                    func(request, UnisendTerminal.objects.none())
                    return HttpResponseRedirect(request.get_full_path())

        return super().changelist_view(request, extra_context=extra_context)

    def response_action(self, request, queryset):
        action = request.POST.get("action")
        selected = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)

        if action in {"sync_lt", "sync_lv", "sync_ee", "sync_all"} and not selected:
            func = getattr(self, action, None)
            if callable(func):
                func(request, queryset)
                return HttpResponseRedirect(request.get_full_path())

        return super().response_action(request, queryset)

    def _sync_country(self, request, *, country_code: str) -> None:
        items = UnisendClient().list_terminals(receiver_country_code=country_code, size=10000)
        items_list = items
        if isinstance(items, dict) and isinstance(items.get("items"), list):
            items_list = items.get("items")

        created = 0
        updated = 0

        if not isinstance(items_list, list):
            self.message_user(request, f"Unisend terminals sync ({country_code}): unexpected response")
            return

        for it in items_list:
            if not isinstance(it, dict):
                continue
            tid = str(it.get("terminalId") or it.get("id") or "").strip()
            if not tid:
                continue

            obj, was_created = UnisendTerminal.objects.get_or_create(terminal_id=tid)

            obj.country_code = str(it.get("countryCode") or country_code or "").strip().upper()
            obj.name = str(it.get("name") or "").strip()
            obj.locality = str(it.get("locality") or it.get("city") or "").strip()
            obj.street = str(it.get("street") or "").strip()
            obj.postal_code = str(it.get("postalCode") or "").strip()

            lat = it.get("latitude")
            lng = it.get("longitude")
            try:
                obj.latitude = float(lat) if lat is not None and str(lat).strip() else None
            except Exception:
                obj.latitude = None
            try:
                obj.longitude = float(lng) if lng is not None and str(lng).strip() else None
            except Exception:
                obj.longitude = None

            obj.raw = it
            obj.is_active = True
            obj.save()

            if was_created:
                created += 1
            else:
                updated += 1

        self.message_user(request, f"Unisend terminals sync ({country_code}): created={created}, updated={updated}")

    @admin.action(description="Sync Unisend terminals: LT")
    def sync_lt(self, request, queryset):
        self._sync_country(request, country_code="LT")

    @admin.action(description="Sync Unisend terminals: LV")
    def sync_lv(self, request, queryset):
        self._sync_country(request, country_code="LV")

    @admin.action(description="Sync Unisend terminals: EE")
    def sync_ee(self, request, queryset):
        self._sync_country(request, country_code="EE")

    @admin.action(description="Sync Unisend terminals: LT + LV + EE")
    def sync_all(self, request, queryset):
        for cc in ("LT", "LV", "EE"):
            self._sync_country(request, country_code=cc)
