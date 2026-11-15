from django.contrib import admin

from .models import (
    ProductCooccur,
    ProductMomentum,
    ProductRelation,
    RecommendationSnapshot,
    SeasonalBoost,
)


@admin.register(ProductRelation)
class ProductRelationAdmin(admin.ModelAdmin):
    list_display = (
        "source_product", "target_product",
        "kind", "source", "weight",
    )
    list_filter = ("kind", "source")
    search_fields = (
        "source_product__name", "source_product__sku",
        "target_product__name", "target_product__sku",
    )
    autocomplete_fields = ("source_product", "target_product")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ProductCooccur)
class ProductCooccurAdmin(admin.ModelAdmin):
    list_display = (
        "product_a", "product_b", "order_count", "weight", "score", "updated_at",
    )
    search_fields = (
        "product_a__name", "product_a__sku",
        "product_b__name", "product_b__sku",
    )
    ordering = ("-score", "-order_count")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ProductMomentum)
class ProductMomentumAdmin(admin.ModelAdmin):
    list_display = (
        "product", "momentum", "sales_recent", "baseline_daily", "updated_at",
    )
    search_fields = ("product__name", "product__sku")
    ordering = ("-momentum",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SeasonalBoost)
class SeasonalBoostAdmin(admin.ModelAdmin):
    list_display = (
        "name", "mode", "activity", "category",
        "boost", "is_active", "start_mmdd", "end_mmdd", "expires_at",
    )
    list_filter = ("mode", "is_active")
    search_fields = ("name", "activity__name", "category__name")
    autocomplete_fields = ("activity", "category")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {
            "fields": ("name", "mode", "is_active", "boost"),
        }),
        ("Target", {
            "fields": ("activity", "category"),
        }),
        ("Schedule (mode=scheduled)", {
            "fields": ("start_mmdd", "end_mmdd"),
        }),
        ("Manual trigger", {
            "fields": ("expires_at",),
            "description": "Jei tuščia — lieka aktyvu iki rankinio išjungimo.",
        }),
        ("Meta", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(RecommendationSnapshot)
class RecommendationSnapshotAdmin(admin.ModelAdmin):
    list_display = ("seed_product", "kind", "ids_count", "computed_at")
    list_filter = ("kind",)
    search_fields = ("seed_product__name", "seed_product__sku")

    @admin.display(description="Kandidatų")
    def ids_count(self, obj):
        return len(obj.ranked_ids or [])

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
