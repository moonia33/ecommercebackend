from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    ConsentType,
    CustomerGroup,
    User,
    UserAddress,
    UserConsent,
    UserPickupPoint,
    UserPhone,
)


class UserPhoneInline(admin.TabularInline):
    model = UserPhone
    extra = 0


class UserAddressInline(admin.TabularInline):
    model = UserAddress
    extra = 0


class UserPickupPointInline(admin.StackedInline):
    model = UserPickupPoint
    extra = 0
    max_num = 1


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "is_staff", "is_active", "date_joined")
    search_fields = ("email",)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name")}),
        ("Business", {"fields": ("customer_groups",)}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "is_staff", "is_active"),
            },
        ),
    )

    readonly_fields = ("last_login", "date_joined")
    filter_horizontal = ("groups", "user_permissions", "customer_groups")
    inlines = (UserPhoneInline, UserAddressInline, UserPickupPointInline)


@admin.register(CustomerGroup)
class CustomerGroupAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "priority",
        "pricing_type",
        "allow_additional_discounts",
        "allow_coupons",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    ordering = ("-priority", "code")


@admin.register(ConsentType)
class ConsentTypeAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "version", "is_required",
                    "is_active", "sort_order")
    list_filter = ("is_required", "is_active")
    search_fields = ("key", "name")
    ordering = ("sort_order", "key")


@admin.register(UserPhone)
class UserPhoneAdmin(admin.ModelAdmin):
    list_display = ("user", "phone", "is_primary", "is_verified", "created_at")
    list_filter = ("is_primary", "is_verified")
    search_fields = ("user__email", "phone")
    ordering = ("-is_primary", "phone")


@admin.register(UserPickupPoint)
class UserPickupPointAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "shipping_method_code",
        "pickup_point_id",
        "country_code",
        "updated_at",
    )
    list_filter = ("shipping_method_code", "country_code")
    search_fields = ("user__email", "pickup_point_id", "pickup_point_name")
    ordering = ("-updated_at",)


@admin.register(UserAddress)
class UserAddressAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "country_code",
        "city",
        "postal_code",
        "is_default_shipping",
        "is_default_billing",
        "updated_at",
    )
    list_filter = ("country_code", "is_default_shipping", "is_default_billing")
    search_fields = ("user__email", "city", "postal_code", "line1")
    ordering = ("-is_default_shipping", "-is_default_billing", "-updated_at")


@admin.register(UserConsent)
class UserConsentAdmin(admin.ModelAdmin):
    list_display = ("user", "consent_type", "accepted", "updated_at")
    list_filter = ("accepted", "consent_type")
    search_fields = ("user__email", "consent_type__key")
    ordering = ("-updated_at",)

    fields = ("user", "consent_type", "accepted", "source",
              "accepted_at", "revoked_at", "updated_at")
    readonly_fields = ("accepted_at", "revoked_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        # On edit, don't allow changing unique key parts
        if obj is not None:
            return tuple(self.readonly_fields) + ("user", "consent_type")
        return self.readonly_fields

    def save_model(self, request, obj, form, change):
        # Keep timestamps consistent with status changes
        accepted = bool(obj.accepted)
        source = obj.source or "admin"
        if change:
            previous = UserConsent.objects.get(pk=obj.pk)
            if previous.accepted != accepted:
                obj.set_status(accepted, source=source)
                return
            if source != previous.source:
                obj.source = source
        else:
            # Ensure accepted_at/revoked_at set on first save
            obj.save()
            obj.set_status(accepted, source=source)
            return

        super().save_model(request, obj, form, change)
