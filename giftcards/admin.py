from django.contrib import admin

from .models import GiftCard, GiftCardTransaction


class TxInline(admin.TabularInline):
    model = GiftCardTransaction
    extra = 0
    readonly_fields = ("kind", "amount", "balance_after", "order", "note", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(GiftCard)
class GiftCardAdmin(admin.ModelAdmin):
    list_display = ("code_tail", "balance", "currency", "initial_amount",
                    "status", "owner", "purchaser", "expires_at", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("code", "owner__email", "purchaser__email", "recipient_email")
    readonly_fields = ("code", "created_at", "updated_at")
    inlines = [TxInline]

    def code_tail(self, obj):
        return f"…{obj.code[-4:]}"
    code_tail.short_description = "Code"


@admin.register(GiftCardTransaction)
class GiftCardTransactionAdmin(admin.ModelAdmin):
    list_display = ("card", "kind", "amount", "balance_after", "order", "created_at")
    list_filter = ("kind",)
    search_fields = ("card__code",)
    readonly_fields = ("card", "kind", "amount", "balance_after", "order", "note", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
