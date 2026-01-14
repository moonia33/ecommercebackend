from __future__ import annotations

from django import forms
from django.contrib import admin
from django.contrib.admin.helpers import ActionForm
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import F, IntegerField, Sum, Value
from django.db.models.functions import Coalesce
from django.forms.models import BaseInlineFormSet


from api.models import Site

from .widgets import TableEditorWidget, ToastUIMarkdownWidget

from .models import (
    Brand,
    Category,
    ContentBlock,
    ContentBlockTranslation,
    ContentRule,
    Feature,
    FeatureValue,
    OptionType,
    OptionValue,
    Product,
    ProductFeatureValue,
    ProductGroup,
    ProductImage,
    ProductOptionType,
    TaxClass,
    TaxRate,
    Variant,
    VariantOptionValue,
    Warehouse,
    InventoryItem,
    BackInStockSubscription,
    EnrichmentRule,
    EnrichmentRun,
    EnrichmentMatch,
    SiteBrandExclusion,
    SiteCategoryBrandExclusion,
    SiteCategoryVisibility,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "parent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

    class CategoryVisibilityActionForm(ActionForm):
        site = forms.ModelChoiceField(queryset=Site.objects.all(), required=False)
        include_descendants = forms.BooleanField(required=False, initial=True)

    action_form = CategoryVisibilityActionForm

    @admin.action(description="Pridėti į site visibility")
    def add_to_site_visibility(self, request, queryset):
        site = request.POST.get("site")
        include_descendants = request.POST.get("include_descendants") in {"1", "true", "True", "on"}

        if not site:
            self.message_user(request, "Pasirink Site prieš vykdant veiksmą.", level=messages.ERROR)
            return

        try:
            site_id = int(site)
        except Exception:
            self.message_user(request, "Neteisingas Site pasirinkimas.", level=messages.ERROR)
            return

        created = 0
        updated = 0
        for c in queryset:
            obj, was_created = SiteCategoryVisibility.objects.get_or_create(
                site_id=site_id,
                category_id=int(c.id),
                defaults={"include_descendants": include_descendants, "is_active": True},
            )
            if was_created:
                created += 1
            else:
                fields_to_update = []
                if bool(obj.include_descendants) != bool(include_descendants):
                    obj.include_descendants = include_descendants
                    fields_to_update.append("include_descendants")
                if not bool(obj.is_active):
                    obj.is_active = True
                    fields_to_update.append("is_active")
                if fields_to_update:
                    obj.save(update_fields=fields_to_update)
                    updated += 1

        self.message_user(
            request,
            f"Site visibility: sukurta {created}, atnaujinta {updated}.",
            level=messages.SUCCESS,
        )

    actions = ("add_to_site_visibility",)

    class Form(forms.ModelForm):
        description = forms.CharField(
            required=False,
            widget=ToastUIMarkdownWidget(mode="wysiwyg", height="520px"),
        )

        class Meta:
            model = Category
            fields = "__all__"

    form = Form

    fieldsets = (
        (None, {"fields": ("name", "slug", "parent", "is_active")}),
        ("Turinys", {"fields": ("description",)}),
        (
            "Media",
            {
                "fields": (
                    "hero_image",
                    "hero_image_url",
                    "hero_image_alt",
                    "menu_icon",
                    "menu_icon_url",
                    "menu_icon_alt",
                )
            },
        ),
        ("SEO", {"fields": ("seo_title", "seo_description", "seo_keywords")}),
    )


@admin.register(SiteCategoryVisibility)
class SiteCategoryVisibilityAdmin(admin.ModelAdmin):
    list_display = ("site", "category", "include_descendants", "is_active", "updated_at")
    list_filter = ("site", "is_active", "include_descendants")
    search_fields = ("site__code", "category__slug", "category__name")
    autocomplete_fields = ("site", "category")


@admin.register(SiteBrandExclusion)
class SiteBrandExclusionAdmin(admin.ModelAdmin):
    list_display = ("site", "brand", "is_active", "updated_at")
    list_filter = ("site", "is_active")
    search_fields = ("site__code", "brand__slug", "brand__name")
    autocomplete_fields = ("site", "brand")


@admin.register(SiteCategoryBrandExclusion)
class SiteCategoryBrandExclusionAdmin(admin.ModelAdmin):
    list_display = ("site", "category", "brand", "include_descendants", "is_active", "updated_at")
    list_filter = ("site", "is_active", "include_descendants")
    search_fields = (
        "site__code",
        "category__slug",
        "category__name",
        "brand__slug",
        "brand__name",
    )
    autocomplete_fields = ("site", "category", "brand")


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    fields = ("image", "image_url", "alt_text", "sort_order")


class ProductFeatureValueInline(admin.TabularInline):
    model = ProductFeatureValue
    extra = 0


class ProductOptionTypeInline(admin.TabularInline):
    model = ProductOptionType
    extra = 0


class VariantInlineForm(forms.ModelForm):
    sku = forms.CharField(required=False)

    class Meta:
        model = Variant
        fields = "__all__"


class VariantInlineFormSet(BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._product = self.instance

        # On the "add product" page the parent instance has no PK yet.
        # Accessing reverse relations (product.option_types) would raise:
        # ValueError: instance needs a primary key before this relationship can be used.
        if not getattr(self._product, "pk", None):
            self._product_option_types = []
            self._option_fields = []
            return

        self._product_option_types = list(
            self._product.option_types.select_related("option_type").order_by(
                "sort_order", "id"
            )
        )
        self._option_fields: list[tuple[OptionType, str]] = [
            (pot.option_type, f"opt_{pot.option_type_id}")
            for pot in self._product_option_types
        ]

    def add_fields(self, form, index):
        super().add_fields(form, index)

        for option_type, field_name in self._option_fields:
            # Field may already be declared on the form class via VariantInline.get_formset
            if field_name not in form.fields:
                form.fields[field_name] = forms.ModelChoiceField(
                    label=option_type.name or option_type.code,
                    queryset=OptionValue.objects.filter(
                        option_type=option_type, is_active=True
                    ).order_by("sort_order", "label"),
                    required=False,
                )

            if form.instance.pk:
                existing = (
                    form.instance.option_values.filter(option_type=option_type)
                    .select_related("option_value")
                    .first()
                )
                if existing:
                    form.initial[field_name] = existing.option_value_id

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        active_option_fields = list(self._option_fields)
        seen: dict[str, int] = {}

        for i, form in enumerate(self.forms):
            if not hasattr(form, "cleaned_data"):
                continue
            if self.can_delete and self._should_delete_form(form):
                continue

            # Skip completely empty extra rows
            if not form.instance.pk and not form.has_changed():
                continue

            # Enforce SKU presence (auto-generated later if blank)
            # Keep this validation light; we fill blanks in save().

            # If product has option types, enforce full selection and prevent duplicates.
            if active_option_fields:
                signature_parts: list[str] = []
                for option_type, field_name in active_option_fields:
                    ov = form.cleaned_data.get(field_name)
                    if ov is None:
                        raise ValidationError(
                            f"Variantui privaloma parinkti '{option_type.code}' reikšmę."
                        )
                    signature_parts.append(str(ov.pk))

                signature = "|".join(signature_parts)
                if signature in seen:
                    raise ValidationError(
                        "Negalima turėti dviejų variantų su ta pačia option kombinacija."
                    )
                seen[signature] = i

    def _generate_sku(self, base: str, option_values: list[OptionValue]) -> str:
        parts = [base] + [ov.code for ov in option_values if ov]
        candidate = "-".join([p for p in parts if p])
        if len(candidate) <= 64:
            return candidate

        # Fallback: keep base and append a compact hash
        import hashlib

        digest = hashlib.sha1(candidate.encode("utf-8")).hexdigest()[:10]
        trimmed_base = base[: (64 - 1 - len(digest))]
        return f"{trimmed_base}-{digest}"

    def save(self, commit=True):
        # Save variants first
        instances = super().save(commit=commit)

        option_type_ids = [
            pot.option_type_id for pot in self._product_option_types]

        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if self.can_delete and self._should_delete_form(form):
                continue
            if not form.instance.pk and not form.has_changed():
                continue

            variant = form.instance
            if not variant.pk:
                continue

            selected_values: list[OptionValue] = []
            selected_by_type: dict[int, OptionValue] = {}
            for option_type, field_name in self._option_fields:
                ov = form.cleaned_data.get(field_name)
                if ov is not None:
                    selected_values.append(ov)
                    selected_by_type[option_type.id] = ov

            # If SKU is blank, auto-generate one from product SKU + option codes
            if not variant.sku:
                base = self._product.sku
                variant.sku = self._generate_sku(base, selected_values)

                # Ensure uniqueness by suffixing -2, -3 ... if needed
                if Variant.objects.exclude(pk=variant.pk).filter(sku=variant.sku).exists():
                    n = 2
                    while True:
                        suffix = f"-{n}"
                        sku_candidate = variant.sku
                        if len(sku_candidate) + len(suffix) > 64:
                            sku_candidate = sku_candidate[: (64 - len(suffix))]
                        sku_candidate = f"{sku_candidate}{suffix}"
                        if not Variant.objects.exclude(pk=variant.pk).filter(
                            sku=sku_candidate
                        ).exists():
                            variant.sku = sku_candidate
                            break
                        n += 1

                variant.save(update_fields=["sku"])

            # Remove option values for option types that are no longer enabled on product
            VariantOptionValue.objects.filter(variant=variant).exclude(
                option_type_id__in=option_type_ids
            ).delete()

            existing = {
                v.option_type_id: v
                for v in VariantOptionValue.objects.filter(
                    variant=variant, option_type_id__in=option_type_ids
                )
            }

            for option_type, _field_name in self._option_fields:
                ov = selected_by_type.get(option_type.id)
                if ov is None:
                    VariantOptionValue.objects.filter(
                        variant=variant, option_type=option_type
                    ).delete()
                    continue

                current = existing.get(option_type.id)
                if current:
                    if current.option_value_id != ov.id:
                        current.option_value = ov
                        current.save(update_fields=["option_value"])
                else:
                    VariantOptionValue.objects.create(
                        variant=variant,
                        option_type=option_type,
                        option_value=ov,
                    )

        return instances


class VariantInline(admin.TabularInline):
    model = Variant
    extra = 0
    form = VariantInlineForm
    formset = VariantInlineFormSet

    def get_formset(self, request, obj=None, **kwargs):
        # When editing an existing Product, declare dynamic opt_* fields on the form
        # class up-front. Otherwise, if get_fields() includes opt_* names, Django will
        # raise: FieldError: Unknown field(s) (opt_*) specified for Variant.
        if obj is not None and getattr(obj, "pk", None):
            product_option_types = list(
                obj.option_types.select_related("option_type").order_by(
                    "sort_order", "id"
                )
            )

            declared: dict[str, object] = {}
            for pot in product_option_types:
                option_type = pot.option_type
                field_name = f"opt_{option_type.id}"
                declared[field_name] = forms.ModelChoiceField(
                    label=option_type.name or option_type.code,
                    queryset=OptionValue.objects.filter(
                        option_type=option_type, is_active=True
                    ).order_by("sort_order", "label"),
                    required=False,
                )

            DynamicForm = type(
                "VariantInlineDynamicForm",
                (self.form,),
                declared,
            )
            kwargs["form"] = DynamicForm

        return super().get_formset(request, obj, **kwargs)

    def get_fields(self, request, obj=None):
        base_fields = [
            "sku",
            "barcode",
            "name",
            "price_eur",
            "is_active",
        ]

        if obj is None:
            return base_fields

        option_type_ids = list(
            obj.option_types.order_by("sort_order", "id").values_list(
                "option_type_id", flat=True
            )
        )
        dynamic_fields = [f"opt_{ot_id}" for ot_id in option_type_ids]
        return base_fields + dynamic_fields


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "brand", "category", "group", "is_active")
    list_filter = ("is_active", "brand", "category", "group")
    search_fields = ("sku", "name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("tax_class",)
    inlines = (
        ProductImageInline,
        ProductFeatureValueInline,
        ProductOptionTypeInline,
        VariantInline,
    )

    class Form(forms.ModelForm):
        description = forms.CharField(
            required=False,
            widget=ToastUIMarkdownWidget(mode="wysiwyg", height="520px"),
        )

        class Meta:
            model = Product
            fields = "__all__"

    form = Form

    fieldsets = (
        (None, {"fields": ("sku", "name", "slug", "is_active")}),
        ("Klasifikacija", {
         "fields": ("brand", "category", "group", "tax_class")}),
        ("Turinys", {"fields": ("description",)}),
        ("SEO", {"fields": ("seo_title", "seo_description", "seo_keywords")}),
        ("Meta", {"fields": ("created_at", "updated_at")}),
    )

    readonly_fields = ("created_at", "updated_at")


@admin.register(TaxClass)
class TaxClassAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    ordering = ("sort_order", "code")


@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display = ("country_code", "tax_class", "rate",
                    "valid_from", "valid_to", "is_active")
    list_filter = ("country_code", "tax_class", "is_active")
    search_fields = ("country_code", "tax_class__code", "tax_class__name")
    ordering = ("country_code", "tax_class__code", "-valid_from")
    autocomplete_fields = ("tax_class",)


class FeatureValueInline(admin.TabularInline):
    model = FeatureValue
    extra = 0


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_filterable",
                    "allows_multiple", "is_active")
    list_filter = ("is_filterable", "allows_multiple", "is_active")
    search_fields = ("code", "name")
    ordering = ("sort_order", "code")
    inlines = (FeatureValueInline,)


class OptionValueInline(admin.TabularInline):
    model = OptionValue
    extra = 0


@admin.register(OptionType)
class OptionTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "display_type", "swatch_type", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    ordering = ("sort_order", "code")
    inlines = (OptionValueInline,)


class VariantOptionValueInline(admin.TabularInline):
    model = VariantOptionValue
    extra = 0


class InventoryItemInline(admin.TabularInline):
    model = InventoryItem
    extra = 0
    autocomplete_fields = ("warehouse",)
    fields = ("warehouse", "qty_on_hand", "qty_reserved", "cost_eur")


@admin.register(Variant)
class VariantAdmin(admin.ModelAdmin):
    list_display = (
        "sku",
        "barcode",
        "product",
        "option_values_display",
        "price_eur",
        "cost_eur",
        "stock_available",
        "is_active",
    )
    list_filter = ("is_active", "product")
    search_fields = (
        "sku",
        "barcode",
        "product__sku",
        "product__name",
        "option_values__option_value__label",
        "option_values__option_value__code",
        "option_values__option_type__code",
        "option_values__option_type__name",
    )
    ordering = ("sku",)
    inlines = (VariantOptionValueInline, InventoryItemInline)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.select_related("product").prefetch_related(
            "option_values__option_type",
            "option_values__option_value",
        )
        available_expr = Coalesce(
            Sum(
                F("inventory_items__qty_on_hand") -
                F("inventory_items__qty_reserved"),
                output_field=IntegerField(),
            ),
            Value(0),
        )
        return qs.annotate(_stock_available=available_expr)

    @admin.display(description="Options")
    def option_values_display(self, obj: Variant) -> str:
        rows = list(
            obj.option_values.select_related(
                "option_type",
                "option_value",
            ).all()
        )
        parts = []
        for r in rows:
            parts.append(f"{r.option_type.code}:{r.option_value.label}")
        return ", ".join(parts)

    @admin.display(description="Stock (available)")
    def stock_available(self, obj: Variant) -> int:
        # Sum across warehouses.
        annotated = getattr(obj, "_stock_available", None)
        if annotated is not None:
            return int(annotated)
        return 0


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "country_code",
        "city",
        "dispatch_days_min",
        "dispatch_days_max",
        "is_active",
    )
    list_filter = ("is_active", "country_code")
    search_fields = ("code", "name", "city")
    ordering = ("sort_order", "code")


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = (
        "variant",
        "warehouse",
        "offer_visibility",
        "condition_grade",
        "offer_priority",
        "never_discount",
        "offer_price_override_eur",
        "offer_discount_percent",
        "qty_on_hand",
        "qty_reserved",
        "qty_available",
        "cost_eur",
        "updated_at",
    )
    list_filter = ("warehouse", "offer_visibility", "condition_grade", "never_discount")
    search_fields = (
        "variant__sku",
        "variant__product__sku",
        "variant__product__name",
        "warehouse__code",
        "warehouse__name",
    )
    autocomplete_fields = ("variant", "warehouse")

    @admin.display(description="Available")
    def qty_available(self, obj: InventoryItem) -> int:
        return obj.qty_available


@admin.register(ProductGroup)
class ProductGroupAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name")


@admin.register(BackInStockSubscription)
class BackInStockSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("email", "channel", "product", "variant", "is_active", "created_at", "notified_at")
    list_filter = ("channel", "is_active")
    search_fields = ("email", "product__sku", "product__name", "variant__sku")
    autocomplete_fields = ("product", "variant")


class ContentBlockTranslationInline(admin.StackedInline):
    model = ContentBlockTranslation
    extra = 0
    show_change_link = True

    fieldsets = (
        (None, {"fields": ("language_code", "title")}),
        ("Turinys", {"fields": ("markdown", "payload")}),
    )

    class Form(forms.ModelForm):
        markdown = forms.CharField(
            required=False,
            widget=ToastUIMarkdownWidget(mode="wysiwyg", height="360px"),
        )

        payload = forms.JSONField(
            required=False,
            widget=TableEditorWidget(),
        )

        class Meta:
            model = ContentBlockTranslation
            fields = "__all__"

    form = Form


@admin.register(ContentBlock)
class ContentBlockAdmin(admin.ModelAdmin):
    list_display = ("key", "type", "placement", "priority", "is_active", "valid_from", "valid_to")
    list_filter = ("is_active", "type", "placement")
    search_fields = ("key",)

    inlines = (ContentBlockTranslationInline,)

    fieldsets = (
        (None, {"fields": ("key", "type", "placement", "is_active", "priority")}),
        ("Galiojimas", {"fields": ("valid_from", "valid_to")}),
    )


@admin.register(ContentRule)
class ContentRuleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "content_block",
        "priority",
        "is_active",
        "is_exclusive",
        "channel",
        "brand",
        "category",
        "include_descendants",
        "product_group",
        "product",
        "valid_from",
        "valid_to",
    )
    list_filter = ("is_active", "is_exclusive", "channel", "brand")
    search_fields = ("content_block__key",)
    autocomplete_fields = ("content_block", "brand", "category", "product_group", "product")


@admin.register(ContentBlockTranslation)
class ContentBlockTranslationAdmin(admin.ModelAdmin):
    list_display = ("content_block", "language_code", "title")
    list_filter = ("language_code",)
    search_fields = ("content_block__key", "title")
    autocomplete_fields = ("content_block",)

    class Form(forms.ModelForm):
        markdown = forms.CharField(
            required=False,
            widget=ToastUIMarkdownWidget(mode="wysiwyg", height="520px"),
        )
        payload = forms.JSONField(
            required=False,
            widget=TableEditorWidget(),
            help_text="Naudojama tik kai ContentBlock.type=table (redaguokite kaip lentelę).",
        )

        class Meta:
            model = ContentBlockTranslation
            fields = "__all__"

    form = Form

@admin.register(EnrichmentRule)
class EnrichmentRuleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "feature",
        "matcher_type",
        "priority",
        "is_active",
        "brand",
        "category",
        "product_group",
        "updated_at",
    )
    list_filter = ("is_active", "matcher_type", "feature")
    search_fields = ("name", "pattern", "value_template", "fixed_value")
    autocomplete_fields = ("feature", "brand", "category", "product_group")
    ordering = ("-priority", "id")

    actions = ("run_selected_dry_run", "run_selected_apply")

    def run_selected_dry_run(self, request, queryset):
        from catalog.enrichment import apply_enrichment_rules

        rule_ids = list(queryset.values_list("id", flat=True))
        run, result = apply_enrichment_rules(
            dry_run=True,
            rule_ids=rule_ids,
            triggered_by=request.user,
        )
        self.message_user(
            request,
            (
                "Dry-run done. "
                f"run_id={run.id}, processed_products={result.processed_products}, "
                f"matched={result.matched}, assigned={result.assigned}, "
                f"created_feature_values={result.created_feature_values}, "
                f"skipped_existing={result.skipped_existing}, skipped_conflict={result.skipped_conflict}"
            ),
        )

    run_selected_dry_run.short_description = "Run selected rules (dry-run)"

    def run_selected_apply(self, request, queryset):
        from catalog.enrichment import apply_enrichment_rules

        rule_ids = list(queryset.values_list("id", flat=True))
        run, result = apply_enrichment_rules(
            dry_run=False,
            rule_ids=rule_ids,
            triggered_by=request.user,
        )
        self.message_user(
            request,
            (
                "Apply done. "
                f"run_id={run.id}, processed_products={result.processed_products}, "
                f"matched={result.matched}, assigned={result.assigned}, "
                f"created_feature_values={result.created_feature_values}, "
                f"skipped_existing={result.skipped_existing}, skipped_conflict={result.skipped_conflict}"
            ),
        )

    run_selected_apply.short_description = "Run selected rules (apply)"


@admin.register(EnrichmentRun)
class EnrichmentRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status",
        "dry_run",
        "triggered_by",
        "started_at",
        "finished_at",
    )
    list_filter = ("status", "dry_run")
    search_fields = ("id", "error")
    autocomplete_fields = ("triggered_by",)
    readonly_fields = (
        "status",
        "dry_run",
        "triggered_by",
        "started_at",
        "finished_at",
        "summary",
        "error",
    )


@admin.register(EnrichmentMatch)
class EnrichmentMatchAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "run",
        "rule",
        "product",
        "action",
        "matched_field",
        "extracted_value",
        "created_at",
    )
    list_filter = ("action", "matched_field")
    search_fields = (
        "product__sku",
        "product__name",
        "matched_text",
        "extracted_value",
    )
    autocomplete_fields = ("run", "rule", "product")
    readonly_fields = (
        "run",
        "rule",
        "product",
        "action",
        "matched_field",
        "matched_text",
        "extracted_value",
        "created_at",
    )
