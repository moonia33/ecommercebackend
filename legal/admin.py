from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from api.admin_i18n import apply_language_code_choices
from catalog.widgets import ToastUIMarkdownWidget

from . import services
from .models import (
    LegalAcceptance,
    LegalDocument,
    LegalDocumentVersion,
    LegalDocumentVersionTranslation,
)


class LegalDocumentVersionInline(admin.TabularInline):
    model = LegalDocumentVersion
    extra = 0
    fields = ("version", "status", "effective_from", "requires_reconsent", "published_at")
    readonly_fields = fields
    show_change_link = True
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(LegalDocument)
class LegalDocumentAdmin(admin.ModelAdmin):
    list_display = ("kind", "slug", "shop", "required_at_checkout", "current_version",
                    "upcoming_version", "is_active", "sort_order")
    list_filter = ("kind", "shop", "required_at_checkout", "is_active")
    search_fields = ("slug",)
    autocomplete_fields = ("shop",)
    inlines = [LegalDocumentVersionInline]

    @admin.display(description="Galiojanti versija")
    def current_version(self, obj):
        v = services.current_version(obj)
        return v.version if v else "—"

    @admin.display(description="Įsigalios")
    def upcoming_version(self, obj):
        v = services.upcoming_version(obj)
        if not v:
            return "—"
        return f"{v.version} ({timezone.localtime(v.effective_from):%Y-%m-%d %H:%M})"


class TranslationForm(forms.ModelForm):
    language_code = forms.ChoiceField(choices=())

    class Meta:
        model = LegalDocumentVersionTranslation
        fields = ("language_code", "title", "change_summary", "body_markdown")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_language_code_choices(self, exclude_default=False)


class TranslationInline(admin.StackedInline):
    model = LegalDocumentVersionTranslation
    form = TranslationForm
    extra = 0
    fields = ("language_code", "title", "change_summary", "body_markdown", "content_hash")
    readonly_fields = ("content_hash",)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "body_markdown":
            kwargs["widget"] = ToastUIMarkdownWidget
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def _locked(self, obj):
        return bool(obj and obj.is_locked)

    def has_add_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_delete_permission(request, obj)


@admin.register(LegalDocumentVersion)
class LegalDocumentVersionAdmin(admin.ModelAdmin):
    list_display = ("document", "version", "status", "effective_from", "requires_reconsent",
                    "published_at", "acceptance_count")
    list_filter = ("status", "requires_reconsent", "document__kind", "document__shop")
    search_fields = ("version", "translations__title")
    fields = ("document", "version", "status", "effective_from", "requires_reconsent",
              "published_at", "created_by", "created_at")
    readonly_fields = ("status", "published_at", "created_by", "created_at")
    inlines = [TranslationInline]
    actions = ("publish_versions", "duplicate_as_draft")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.is_locked:
            return self.fields
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_locked:
            return False
        return super().has_delete_permission(request, obj)

    @admin.display(description="Sutikimų")
    def acceptance_count(self, obj):
        return obj.acceptances.count()

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Publikuoti (nuo įsigaliojimo datos tekstas nebekeičiamas)")
    def publish_versions(self, request, queryset):
        done = 0
        for v in queryset:
            try:
                v.publish()
                done += 1
            except ValidationError as exc:
                self.message_user(request, f"{v}: {'; '.join(exc.messages)}", messages.ERROR)
        if done:
            self.message_user(request, f"Publikuota: {done}", messages.SUCCESS)

    @admin.action(description="Kopijuoti kaip naują juodraštį")
    def duplicate_as_draft(self, request, queryset):
        for v in queryset:
            with transaction.atomic():
                new = LegalDocumentVersion.objects.create(
                    document=v.document, version="",
                    effective_from=timezone.now(), created_by=request.user)
                for t in v.translations.all():
                    LegalDocumentVersionTranslation.objects.create(
                        version=new, language_code=t.language_code, title=t.title,
                        body_markdown=t.body_markdown)
            self.message_user(request, f"Sukurtas juodraštis {new.version} ({v.document})")


@admin.register(LegalAcceptance)
class LegalAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("accepted_at", "user", "version", "source", "order", "language_code",
                    "ip_address")
    list_filter = ("source", "version__document__kind", "version__document__shop")
    search_fields = ("user__email", "order__id", "version__version")
    date_hierarchy = "accepted_at"
    readonly_fields = ("user", "version", "order", "source", "language_code", "accepted_at",
                       "ip_address", "user_agent")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
