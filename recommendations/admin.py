from django.contrib import admin

from .models import ProductCooccur, ProductRelation


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
    list_display = ("product_a", "product_b", "order_count", "updated_at")
    search_fields = (
        "product_a__name", "product_a__sku",
        "product_b__name", "product_b__sku",
    )
    ordering = ("-order_count",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
