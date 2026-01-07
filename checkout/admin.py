from __future__ import annotations

from django import forms
from django.contrib import admin
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils import timezone

from .models import Cart, CartItem, Order, OrderConsent, OrderLine, PaymentIntent


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    autocomplete_fields = ("variant",)


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "updated_at")
    search_fields = ("user__email",)
    inlines = (CartItemInline,)


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("id", "cart", "variant", "qty", "updated_at")
    search_fields = ("cart__user__email", "variant__sku",
                     "variant__product__name")
    autocomplete_fields = ("variant",)


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 1
    autocomplete_fields = ("variant",)

    class Form(forms.ModelForm):
        sku = forms.CharField(required=False)
        name = forms.CharField(required=False)
        vat_rate = forms.DecimalField(required=False)

        class Meta:
            model = OrderLine
            fields = "__all__"

        def clean(self):
            cleaned = super().clean()
            variant = cleaned.get("variant")
            if variant:
                if not cleaned.get("sku"):
                    cleaned["sku"] = variant.sku
                if not cleaned.get("name"):
                    cleaned["name"] = getattr(variant.product, "name", "")
                if cleaned.get("vat_rate") is None and getattr(variant.product, "tax_class", None):
                    from pricing.services import get_vat_rate

                    cleaned["vat_rate"] = get_vat_rate(
                        country_code=self.instance.order.country_code,
                        tax_class=variant.product.tax_class,
                    )
            if cleaned.get("vat_rate") is None:
                cleaned["vat_rate"] = 0
            return cleaned

    form = Form

    fields = (
        "variant",
        "sku",
        "name",
        "unit_net",
        "vat_rate",
        "qty",
        "unit_vat",
        "unit_gross",
        "total_net",
        "total_vat",
        "total_gross",
    )
    readonly_fields = (
        "unit_vat",
        "unit_gross",
        "total_net",
        "total_vat",
        "total_gross",
    )


class OrderConsentInline(admin.TabularInline):
    model = OrderConsent
    extra = 0
    readonly_fields = ("kind", "document_version",
                       "accepted_at", "ip_address", "user_agent")

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "status",
        "delivery_status",
        "shipping_method",
        "carrier_code",
        "tracking_number",
        "total_gross",
        "created_at",
    )
    list_filter = ("status", "delivery_status", "carrier_code")
    search_fields = ("id", "user__email", "tracking_number")
    autocomplete_fields = ("pickup_locker",)
    readonly_fields = (
        "created_at",
        "updated_at",
        "carrier_shipment_id",
        "shipping_label_generated_at",
        "shipping_label_pdf",
        "items_net",
        "items_vat",
        "items_gross",
        "shipping_net",
        "shipping_vat",
        "shipping_gross",
        "total_net",
        "total_vat",
        "total_gross",
    )
    fields = (
        "user",
        "status",
        "delivery_status",
        "carrier_code",
        "carrier_shipment_id",
        "tracking_number",
        "shipping_label_generated_at",
        "shipping_label_pdf",
        "pickup_locker",
        "unisend_terminal",
        "pickup_point_id",
        "pickup_point_name",
        "pickup_point_raw",
        "currency",
        "country_code",
        "shipping_method",
        "shipping_net_manual",
        "items_net",
        "items_vat",
        "items_gross",
        "shipping_net",
        "shipping_vat",
        "shipping_gross",
        "total_net",
        "total_vat",
        "total_gross",
        "shipping_full_name",
        "shipping_company",
        "shipping_line1",
        "shipping_city",
        "shipping_postal_code",
        "shipping_country_code",
        "shipping_phone",
        "created_at",
        "updated_at",
    )
    inlines = (OrderConsentInline, OrderLineInline,)

    class Form(forms.ModelForm):
        shipping_method = forms.ChoiceField(required=True)
        unisend_terminal = forms.ModelChoiceField(
            required=False,
            queryset=None,
            label="Unisend terminal",
        )

        class Meta:
            model = Order
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            from shipping.models import ShippingMethod
            from unisend.models import UnisendTerminal

            choices = [(m.code, f"{m.name} ({m.code})") for m in ShippingMethod.objects.filter(
                is_active=True).order_by("sort_order", "code")]
            if not choices:
                choices = [("lpexpress", "LPExpress (lpexpress)")]
            self.fields["shipping_method"].choices = choices

            # Populate Unisend terminal selector (active only). If order has pickup_point_id,
            # preselect that terminal for convenience.
            self.fields["unisend_terminal"].queryset = UnisendTerminal.objects.filter(is_active=True).order_by(
                "country_code", "locality", "name", "terminal_id"
            )
            current_pid = ""
            try:
                current_pid = str(getattr(self.instance, "pickup_point_id", "") or "").strip()
            except Exception:
                current_pid = ""
            if current_pid and not self.initial.get("unisend_terminal"):
                t = UnisendTerminal.objects.filter(terminal_id=current_pid).first()
                if t:
                    self.initial["unisend_terminal"] = t

        def clean(self):
            cleaned = super().clean()
            from shipping.models import ShippingMethod
            from dpd.models import DpdLocker
            from unisend.models import UnisendTerminal

            code = (cleaned.get("shipping_method") or "").strip()
            if code:
                m = ShippingMethod.objects.filter(code=code).first()
                if m and not cleaned.get("carrier_code"):
                    cleaned["carrier_code"] = m.carrier_code or ""

                # Pickup point enforcement + autofill
                locker = cleaned.get("pickup_locker")
                unisend_terminal = cleaned.get("unisend_terminal")
                carrier = (getattr(m, "carrier_code", "") or "").strip().lower() if m else ""

                if m and m.requires_pickup_point and carrier == "dpd":
                    if locker is None:
                        raise forms.ValidationError(
                            {"pickup_locker": "Šiam pristatymo metodui būtina pasirinkti paštomatą."}
                        )
                else:
                    # Non-DPD pickup (e.g. lpexpress) or non-pickup method: clear DPD locker FK.
                    cleaned["pickup_locker"] = None

                if isinstance(locker, DpdLocker):
                    cleaned["pickup_point_id"] = locker.locker_id
                    cleaned["pickup_point_name"] = locker.name
                    cleaned["pickup_point_raw"] = locker.raw or {}

                # If method is lpexpress with pickup required, allow selecting Unisend terminal.
                if carrier == "lpexpress" and m and m.requires_pickup_point:
                    if isinstance(unisend_terminal, UnisendTerminal):
                        cleaned["pickup_point_id"] = unisend_terminal.terminal_id
                        cleaned["pickup_point_name"] = unisend_terminal.name
                        cleaned["pickup_point_raw"] = unisend_terminal.raw or {}

                # If method is lpexpress, also accept manual pickup_point_id and enrich it.
                if carrier == "lpexpress":
                    pid = str(cleaned.get("pickup_point_id") or "").strip()
                    if pid:
                        t = UnisendTerminal.objects.filter(terminal_id=pid, is_active=True).first()
                        if t:
                            cleaned["pickup_point_name"] = t.name
                            cleaned["pickup_point_raw"] = t.raw or {}

                # Courier method: no pickup point.
                if carrier == "lpexpress" and m and not m.requires_pickup_point:
                    cleaned["unisend_terminal"] = None
                    cleaned["pickup_point_id"] = ""
                    cleaned["pickup_point_name"] = ""
                    cleaned["pickup_point_raw"] = {}
            return cleaned

    form = Form

    actions = ("recalculate_selected", "generate_dpd_labels_a6", "generate_unisend_labels_10x15")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/dpd-label-a6/",
                self.admin_site.admin_view(self.generate_dpd_label_view),
                name="checkout_order_generate_dpd_label",
            )
            ,
            path(
                "<path:object_id>/unisend-label-10x15/",
                self.admin_site.admin_view(self.generate_unisend_label_view),
                name="checkout_order_generate_unisend_label",
            )
        ]
        return custom + urls

    def generate_dpd_label_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        from dpd.client import DpdApiError
        from dpd.labels import DpdLabelConfigError, generate_a6_label_pdf_for_order

        order = get_object_or_404(Order, pk=object_id)
        try:
            pdf = generate_a6_label_pdf_for_order(order, store_on_order=True)
            filename = f"dpd_label_a6_order_{order.id}.pdf"
            resp = HttpResponse(pdf, content_type="application/pdf")
            resp["Content-Disposition"] = f'attachment; filename="{filename}"'
            return resp
        except (DpdLabelConfigError, DpdApiError, RuntimeError, ValueError) as e:
            messages.error(request, f"Nepavyko sugeneruoti DPD lipduko: {e}")

        return redirect(reverse("admin:checkout_order_change", args=[order.pk]))

    def generate_unisend_label_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        from unisend.client import UnisendApiError
        from unisend.labels import UnisendLabelConfigError, generate_label_pdf_for_order

        order = get_object_or_404(Order, pk=object_id)
        try:
            pdf = generate_label_pdf_for_order(order, store_on_order=True)
            filename = f"unisend_label_10x15_order_{order.id}.pdf"
            resp = HttpResponse(pdf, content_type="application/pdf")
            resp["Content-Disposition"] = f'attachment; filename="{filename}"'
            return resp
        except (UnisendLabelConfigError, UnisendApiError, RuntimeError, ValueError) as e:
            messages.error(request, f"Nepavyko sugeneruoti Unisend lipduko: {e}")

        return redirect(reverse("admin:checkout_order_change", args=[order.pk]))

    @admin.action(description="Perskaičiuoti sumas (items/shipping/total)")
    def recalculate_selected(self, request, queryset):
        for o in queryset.prefetch_related("lines"):
            o.recalculate_totals()
            o.save(
                update_fields=[
                    "items_net",
                    "items_vat",
                    "items_gross",
                    "shipping_net",
                    "shipping_vat",
                    "shipping_gross",
                    "total_net",
                    "total_vat",
                    "total_gross",
                    "shipping_net_manual",
                    "updated_at",
                ]
            )

    @admin.action(description="Generuoti DPD A6 lipdukus (PDF) pasirinktiems")
    def generate_dpd_labels_a6(self, request: HttpRequest, queryset):
        from dpd.client import DpdApiError
        from dpd.labels import DpdLabelConfigError, generate_a6_labels_pdf_for_orders

        orders = list(
            queryset.filter(shipping_method__in=[
                            "dpd_locker", "dpd_courier"]).prefetch_related("lines")
        )
        if not orders:
            self.message_user(
                request,
                "Nepasirinkta jokių DPD užsakymų (dpd_locker/dpd_courier).",
                level=messages.WARNING,
            )
            return None

        try:
            pdf, updated = generate_a6_labels_pdf_for_orders(orders)
        except (DpdLabelConfigError, DpdApiError, RuntimeError, ValueError) as e:
            self.message_user(
                request,
                f"Nepavyko sugeneruoti DPD lipdukų: {e}",
                level=messages.ERROR,
            )
            return None

        filename = f"dpd_labels_a6_{timezone.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'

        self.message_user(
            request,
            f"Sugeneruota DPD A6 lipdukų: {len(updated)}.",
            level=messages.SUCCESS,
        )

        now = timezone.now()
        for o in orders:
            if o.delivery_status != Order.DeliveryStatus.LABEL_CREATED:
                o.delivery_status = Order.DeliveryStatus.LABEL_CREATED
            if o.shipping_label_generated_at is None:
                o.shipping_label_generated_at = now
            o.save(update_fields=["delivery_status", "shipping_label_generated_at", "updated_at"])
        return resp

    @admin.action(description="Generuoti Unisend 10x15 lipdukus (PDF) pasirinktiems")
    def generate_unisend_labels_10x15(self, request: HttpRequest, queryset):
        from unisend.client import UnisendApiError
        from unisend.labels import UnisendLabelConfigError, generate_labels_pdf_for_orders

        orders = list(
            queryset.filter(shipping_method__in=["lpexpress", "lpexpress_courier"]).prefetch_related("lines")
        )
        if not orders:
            self.message_user(
                request,
                "Nepasirinkta jokių Unisend užsakymų (lpexpress).",
                level=messages.WARNING,
            )
            return None

        try:
            pdf, _updated = generate_labels_pdf_for_orders(orders)
            resp = HttpResponse(pdf, content_type="application/pdf")
            resp["Content-Disposition"] = 'attachment; filename="unisend_labels_10x15.pdf"'

            now = timezone.now()
            for o in orders:
                if o.delivery_status != Order.DeliveryStatus.LABEL_CREATED:
                    o.delivery_status = Order.DeliveryStatus.LABEL_CREATED
                if o.shipping_label_generated_at is None:
                    o.shipping_label_generated_at = now
                o.save(update_fields=["delivery_status", "shipping_label_generated_at", "updated_at"])
            return resp
        except (UnisendLabelConfigError, UnisendApiError, RuntimeError, ValueError) as e:
            self.message_user(
                request,
                f"Nepavyko sugeneruoti Unisend lipdukų: {e}",
                level=messages.ERROR,
            )
            return None

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        order = form.instance
        order.recalculate_totals()
        order.save(
            update_fields=[
                "items_net",
                "items_vat",
                "items_gross",
                "shipping_net",
                "shipping_vat",
                "shipping_gross",
                "total_net",
                "total_vat",
                "total_gross",
                "shipping_net_manual",
                "updated_at",
            ]
        )

    def save_model(self, request, obj, form, change):
        # Ensure pickup snapshot stays in sync with selected locker
        try:
            from shipping.models import ShippingMethod

            m = ShippingMethod.objects.filter(code=obj.shipping_method).first()
        except Exception:
            m = None

        carrier = (getattr(m, "carrier_code", "") or "").strip().lower() if m else ""

        if m and m.requires_pickup_point and carrier == "dpd" and obj.pickup_locker:
            obj.pickup_point_id = obj.pickup_locker.locker_id
            obj.pickup_point_name = obj.pickup_locker.name
            obj.pickup_point_raw = obj.pickup_locker.raw or {}
        else:
            # For non-DPD methods (incl. lpexpress), keep pickup_point_* as manually set,
            # but never keep a DPD locker FK.
            obj.pickup_locker = None

        super().save_model(request, obj, form, change)


@admin.register(OrderConsent)
class OrderConsentAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "kind",
                    "document_version", "accepted_at")
    list_filter = ("kind",)
    search_fields = ("order__id", "document_version")
    readonly_fields = ("order", "kind", "document_version",
                       "accepted_at", "ip_address", "user_agent")


@admin.register(OrderLine)
class OrderLineAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "sku", "qty", "total_gross")
    search_fields = ("order__id", "sku", "name")
    autocomplete_fields = ("variant",)
    readonly_fields = ("unit_vat", "unit_gross",
                       "total_net", "total_vat", "total_gross")

    class Form(forms.ModelForm):
        sku = forms.CharField(required=False)
        name = forms.CharField(required=False)
        vat_rate = forms.DecimalField(required=False)

        class Meta:
            model = OrderLine
            fields = "__all__"

        def clean(self):
            cleaned = super().clean()
            variant = cleaned.get("variant")
            order = cleaned.get("order")

            if variant:
                if not cleaned.get("sku"):
                    cleaned["sku"] = variant.sku
                if not cleaned.get("name"):
                    cleaned["name"] = getattr(variant.product, "name", "")
                if (
                    cleaned.get("vat_rate") is None
                    and getattr(variant.product, "tax_class", None)
                    and order
                ):
                    from pricing.services import get_vat_rate

                    cleaned["vat_rate"] = get_vat_rate(
                        country_code=order.country_code,
                        tax_class=variant.product.tax_class,
                    )

            if cleaned.get("vat_rate") is None:
                cleaned["vat_rate"] = 0
            return cleaned

    form = Form

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.order_id:
            o = obj.order
            o.recalculate_totals()
            o.save(
                update_fields=[
                    "items_net",
                    "items_vat",
                    "items_gross",
                    "shipping_net",
                    "shipping_vat",
                    "shipping_gross",
                    "total_net",
                    "total_vat",
                    "total_gross",
                    "updated_at",
                ]
            )


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    list_display = ("id", "provider", "status", "order",
                    "amount_gross", "created_at")
    list_filter = ("provider", "status")
    search_fields = ("order__id", "external_id")
    readonly_fields = ("created_at", "updated_at",
                       "raw_request", "raw_response")
