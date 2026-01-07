from __future__ import annotations

from django import forms
from django.contrib import admin
from django.contrib.admin import helpers
from django.http import HttpResponseRedirect

from .client import DpdClient
from .models import DpdConfig, DpdLocker


@admin.register(DpdConfig)
class DpdConfigAdmin(admin.ModelAdmin):
    list_display = ("__str__", "base_url", "status_lang", "updated_at")
    fields = (
        "base_url",
        "token",
        "status_lang",
        "sender_name",
        "sender_phone",
        "sender_street",
        "sender_city",
        "sender_postal_code",
        "sender_country",
        "payer_code",
        "service_alias_courier",
        "service_alias_locker",
        "updated_at",
    )
    readonly_fields = ("updated_at",)

    class Form(forms.ModelForm):
        class Meta:
            model = DpdConfig
            fields = "__all__"
            widgets = {
                "token": forms.PasswordInput(render_value=True),
            }

    form = Form

    def has_add_permission(self, request):
        # singleton: max 1
        return not DpdConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # Jei nėra įrašo – sukurk automatiškai, kad admin'e visada būtų kur suvesti.
        if not DpdConfig.objects.exists():
            DpdConfig.get_solo()
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(DpdLocker)
class DpdLockerAdmin(admin.ModelAdmin):
    list_display = ("locker_id", "country_code", "city", "name",
                    "postal_code", "is_active", "updated_at")
    list_filter = ("is_active", "country_code", "city")
    search_fields = ("locker_id", "city", "name", "street", "postal_code")
    ordering = ("country_code", "city", "name")

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

            # Allow running sync actions without selecting rows.
            if action in {"sync_lt", "sync_lv", "sync_ee", "sync_all"} and not selected:
                func = getattr(self, action, None)
                if callable(func):
                    func(request, DpdLocker.objects.none())
                    return HttpResponseRedirect(request.get_full_path())

        return super().changelist_view(request, extra_context=extra_context)

    def response_action(self, request, queryset):
        action = request.POST.get("action")
        selected = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)

        # Allow running sync actions without selecting rows.
        if action in {"sync_lt", "sync_lv", "sync_ee", "sync_all"} and not selected:
            func = getattr(self, action, None)
            if callable(func):
                func(request, queryset)
                return HttpResponseRedirect(request.get_full_path())

        return super().response_action(request, queryset)

    def _sync_country(self, request, *, country_code: str) -> None:
        params: dict = {"countryCode": country_code, "limit": 10000}
        items = DpdClient().list_lockers(params=params)
        created = 0
        updated = 0

        for it in items:
            if not isinstance(it, dict):
                continue
            locker_id = str(it.get("id") or it.get("lockerId")
                            or it.get("code") or "").strip()
            if not locker_id:
                continue

            addr = it.get("address")
            addr = addr if isinstance(addr, dict) else {}

            obj, was_created = DpdLocker.objects.get_or_create(
                locker_id=locker_id)
            obj.country_code = str(
                it.get("countryCode")
                or addr.get("country")
                or country_code
                or ""
            ).strip().upper()
            obj.city = str(it.get("city") or addr.get("city") or "").strip()
            obj.name = str(it.get("name") or "").strip()
            obj.street = str(
                it.get("street")
                or addr.get("street")
                or it.get("address")
                or ""
            ).strip()
            obj.postal_code = str(
                it.get("postalCode")
                or addr.get("postalCode")
                or addr.get("postal_code")
                or ""
            ).strip()
            obj.raw = it
            obj.is_active = True
            obj.save()

            if was_created:
                created += 1
            else:
                updated += 1

        self.message_user(
            request,
            f"DPD lockers sync ({country_code}): created={created}, updated={updated}, total_seen={created + updated}",
        )

    @admin.action(description="Sync DPD lockers: LT")
    def sync_lt(self, request, queryset):
        self._sync_country(request, country_code="LT")

    @admin.action(description="Sync DPD lockers: LV")
    def sync_lv(self, request, queryset):
        self._sync_country(request, country_code="LV")

    @admin.action(description="Sync DPD lockers: EE")
    def sync_ee(self, request, queryset):
        self._sync_country(request, country_code="EE")

    @admin.action(description="Sync DPD lockers: LT + LV + EE")
    def sync_all(self, request, queryset):
        for cc in ("LT", "LV", "EE"):
            self._sync_country(request, country_code=cc)
