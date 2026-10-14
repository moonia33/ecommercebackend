from django.contrib import admin

from .models import ImportItem, ImportRun


class ImportItemInline(admin.TabularInline):
    model = ImportItem
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ("row_no", "external_code", "status", "error", "product", "variant")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "kind",
        "source",
        "file_name",
        "status",
        "total_items",
        "ok_items",
        "failed_items",
        "skipped_items",
        "mode",
        "triggered_by",
        "created_at",
        "finished_at",
    )
    list_filter = ("kind", "status", "mode", "source")
    search_fields = ("file_name", "file_sha256", "error_message")
    readonly_fields = (
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    )
    inlines = [ImportItemInline]
    date_hierarchy = "created_at"


@admin.register(ImportItem)
class ImportItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "run",
        "row_no",
        "external_code",
        "status",
        "product",
        "variant",
        "created_at",
    )
    list_filter = ("status", "run__kind", "run__source")
    search_fields = ("external_code", "error")
    readonly_fields = ("created_at", "updated_at", "payload")
    raw_id_fields = ("run", "product", "variant")
