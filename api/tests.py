from django.db import IntegrityError
from django.test import Client, RequestFactory, TestCase, override_settings

from accounts.jwt_utils import issue_access_token
from api.i18n import get_request_language_code, translation_fallback_chain
from api.models import Shop, ShopLanguage
from checkout.models import Cart, OpsJob, Order, OrderLine, PaymentIntent
from catalog.models import Brand, Category, InventoryItem, Product, TaxClass, Variant, Warehouse
from cms.models import CmsPage, CmsPageTranslation
from homebuilder.models import HomePage, HomePageTranslation
from accounts.models import User


@override_settings(ALLOWED_HOSTS=["*"], SHOP_CONTEXT_ENABLED=True, SHOP_CONTEXT_STRICT=True)
class MultiSiteIsolationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.shop_a = Shop.objects.create(
            code="a",
            name="Shop A",
            primary_domain="a.local",
            default_language_code="lt",
            default_country_code="LT",
            currency="EUR",
            is_active=True,
        )
        self.shop_b = Shop.objects.create(
            code="b",
            name="Shop B",
            primary_domain="b.local",
            default_language_code="lt",
            default_country_code="LT",
            currency="EUR",
            is_active=True,
        )

    def test_strict_mode_unresolved_host_returns_404(self):
        res = self.client.get("/api/v1/home", HTTP_HOST="unknown.local")
        self.assertEqual(res.status_code, 404)

        res_cart = self.client.get("/api/v1/checkout/cart?country_code=LT", HTTP_HOST="unknown.local")
        self.assertEqual(res_cart.status_code, 404)

    def test_cms_page_is_isolated_per_shop(self):
        page_a = CmsPage.objects.create(slug="about", shop=self.shop_a, is_active=True)
        page_b = CmsPage.objects.create(slug="about", shop=self.shop_b, is_active=True)
        CmsPageTranslation.objects.create(cms_page=page_a, language_code="lt", title="Apie A")
        CmsPageTranslation.objects.create(cms_page=page_b, language_code="lt", title="Apie B")

        res_a = self.client.get("/api/v1/cms/pages/about", HTTP_HOST="a.local")
        res_b = self.client.get("/api/v1/cms/pages/about", HTTP_HOST="b.local")

        self.assertEqual(res_a.status_code, 200)
        self.assertEqual(res_b.status_code, 200)
        self.assertEqual(res_a.json().get("title"), "Apie A")
        self.assertEqual(res_b.json().get("title"), "Apie B")

    def test_homepage_is_isolated_per_shop(self):
        hp_a = HomePage.objects.create(code="home", shop=self.shop_a, is_active=True)
        hp_b = HomePage.objects.create(code="home", shop=self.shop_b, is_active=True)
        HomePageTranslation.objects.create(home_page=hp_a, language_code="lt", title="Titulas A")
        HomePageTranslation.objects.create(home_page=hp_b, language_code="lt", title="Titulas B")

        res_a = self.client.get("/api/v1/home", HTTP_HOST="a.local")
        res_b = self.client.get("/api/v1/home", HTTP_HOST="b.local")

        self.assertEqual(res_a.status_code, 200)
        self.assertEqual(res_b.status_code, 200)
        self.assertEqual(res_a.json().get("title"), "Titulas A")
        self.assertEqual(res_b.json().get("title"), "Titulas B")

    def test_guest_cart_can_exist_per_shop_for_same_session_key(self):
        session = self.client.session
        session["cart_init"] = True
        session.save()
        skey = session.session_key
        self.assertTrue(bool(skey))

        cart_a = Cart.objects.create(user=None, session_key=skey, shop=self.shop_a)
        cart_b = Cart.objects.create(user=None, session_key=skey, shop=self.shop_b)

        self.assertNotEqual(cart_a.id, cart_b.id)
        self.assertEqual(Cart.objects.filter(session_key=skey).count(), 2)

        with self.assertRaises(IntegrityError):
            Cart.objects.create(user=None, session_key=skey, shop=self.shop_a)

    def test_order_idempotency_key_isolated_by_shop(self):
        user = User.objects.create_user(email="ms@example.com", password="test12345")

        o1 = Order.objects.create(user=user, shop=self.shop_a, idempotency_key="abc")
        o2 = Order.objects.create(user=user, shop=self.shop_b, idempotency_key="abc")

        self.assertNotEqual(o1.id, o2.id)

        with self.assertRaises(IntegrityError):
            Order.objects.create(user=user, shop=self.shop_a, idempotency_key="abc")


@override_settings(
    ALLOWED_HOSTS=["*"],
    LANGUAGE_CODE="lt",
    SUPPORTED_LANGUAGE_CODES=["lt", "pl", "en", "de"],
    LANGUAGES=[("lt", "Lithuanian"), ("pl", "Polish"), ("en", "English"), ("de", "German")],
)
class ShopLanguageSelectionTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.shop = Shop.objects.create(
            code="s1",
            name="Shop 1",
            primary_domain="s1.local",
            default_language_code="pl",
            default_country_code="PL",
            currency="EUR",
            is_active=True,
        )
        ShopLanguage.objects.create(shop=self.shop, language_code="pl", is_active=True, is_default=True, sort_order=0)
        ShopLanguage.objects.create(shop=self.shop, language_code="en", is_active=True, is_default=False, sort_order=1)
        ShopLanguage.objects.create(shop=self.shop, language_code="de", is_active=False, is_default=False, sort_order=2)

    def test_request_language_respects_shop_enabled_languages(self):
        req = self.rf.get("/x", {"lang": "de"}, HTTP_ACCEPT_LANGUAGE="en")
        req.shop = self.shop
        self.assertEqual(get_request_language_code(req), "en")

        req2 = self.rf.get("/x", {"lang": "en"}, HTTP_ACCEPT_LANGUAGE="de")
        req2.shop = self.shop
        self.assertEqual(get_request_language_code(req2), "en")

    def test_request_language_from_path_prefix(self):
        req = self.rf.get("/pl/produkty")
        req.shop = self.shop
        self.assertEqual(get_request_language_code(req), "pl")

        req2 = self.rf.get("/de/produkte", HTTP_ACCEPT_LANGUAGE="en")
        req2.shop = self.shop
        self.assertEqual(get_request_language_code(req2), "en")  # de not shop-enabled

        req3 = self.rf.get("/products")
        req3.shop = self.shop
        self.assertEqual(get_request_language_code(req3), "pl")  # shop default

    def test_fallback_chain_prefers_shop_default_and_enabled(self):
        chain = translation_fallback_chain("de", shop=self.shop)
        self.assertEqual(chain[0], "de")
        self.assertIn("pl", chain)
        self.assertIn("en", chain)


@override_settings(ALLOWED_HOSTS=["*"], SHOP_CONTEXT_ENABLED=True, SHOP_CONTEXT_STRICT=True)
class OpsOrderSearchTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.shop = Shop.objects.create(
            code="ops",
            name="Ops Shop",
            primary_domain="ops.local",
            default_language_code="lt",
            default_country_code="LT",
            currency="EUR",
            is_active=True,
        )
        ShopLanguage.objects.create(shop=self.shop, language_code="lt", is_active=True, is_default=True, sort_order=0)

        self.staff_user = User.objects.create_user(
            email="ops-staff@example.com",
            password="pass1234",
            is_staff=True,
        )
        self.non_staff_user = User.objects.create_user(
            email="ops-user@example.com",
            password="pass1234",
            is_staff=False,
        )

        tax, _ = TaxClass.objects.get_or_create(code="standard", defaults={"name": "Standard"})
        suffix = self._testMethodName.lower().replace("_", "-")
        category = Category.objects.create(name="Tools", slug=f"tools-{suffix}", is_active=True)
        brand = Brand.objects.create(name="Bosch", slug=f"bosch-{suffix}", is_active=True)
        product = Product.objects.create(
            sku="P-1",
            name="Bosch Grąžtas",
            slug="bosch-graztas",
            tax_class=tax,
            brand=brand,
            category=category,
            is_active=True,
        )
        variant = Variant.objects.create(
            product=product,
            sku="ABC-123",
            name="13mm",
            price_eur="10.00",
            is_active=True,
        )
        warehouse = Warehouse.objects.create(
            code="remote_wh_1",
            name="Remote WH",
            kind="remote",
            country_code="PL",
            is_active=True,
        )
        inventory = InventoryItem.objects.create(
            variant=variant,
            warehouse=warehouse,
            qty_on_hand=10,
            qty_reserved=0,
        )

        self.order = Order.objects.create(
            user=self.staff_user,
            shop=self.shop,
            status=Order.Status.PAID,
            fulfillment_mode=Order.FulfillmentMode.DROPSHIP,
            supplier_reservation_status=Order.SupplierReservationStatus.PENDING,
            supplier_reference="SUP-7782",
            shipping_method="dpd_pickup",
            delivery_min_date="2026-10-20",
            delivery_max_date="2026-10-24",
            country_code="LT",
            currency="EUR",
        )
        self.order.lines.create(
            variant=variant,
            offer=inventory,
            sku="ABC-123",
            name="Bosch Grąžtas",
            unit_net="10.00",
            vat_rate="0.21",
            unit_vat="2.10",
            unit_gross="12.10",
            qty=2,
            total_net="20.00",
            total_vat="4.20",
            total_gross="24.20",
        )
        self.payment = PaymentIntent.objects.create(
            order=self.order,
            provider=PaymentIntent.Provider.BANK_TRANSFER,
            status=PaymentIntent.Status.PENDING,
            currency="EUR",
            amount_gross="24.20",
        )
        self.inventory_item = inventory
        self.variant = variant
        self.product = product

    def _set_auth_cookie(self, user: User):
        token = issue_access_token(user_id=user.id)
        self.client.cookies["access_token"] = token

    def test_staff_can_search_orders_by_product_supplier_manufacturer(self):
        self._set_auth_cookie(self.staff_user)
        res = self.client.get(
            "/api/v1/ops/orders",
            {
                "product_q": "ABC-123",
                "supplier_code": "remote_wh_1",
                "manufacturer_q": "bosch",
                "remote_only": "true",
            },
            HTTP_HOST="ops.local",
        )
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["id"], self.order.id)
        self.assertEqual(payload["results"][0]["shop"]["code"], "ops")
        self.assertEqual(payload["results"][0]["customer"]["email"], self.staff_user.email)
        self.assertEqual(payload["results"][0]["amount_currency"], "EUR")
        self.assertEqual(payload["results"][0]["amount_gross"], "24.20")
        self.assertEqual(payload["results"][0]["matched_suppliers"], ["remote_wh_1"])
        self.assertEqual(payload["results"][0]["matched_items_count"], 1)
        self.assertEqual(payload["results"][0]["matched_lines"][0]["warehouse_country"], "PL")

    def test_staff_can_filter_orders_by_shop_code(self):
        self._set_auth_cookie(self.staff_user)
        res = self.client.get(
            "/api/v1/ops/orders",
            {"shop_code": "ops"},
            HTTP_HOST="ops.local",
        )
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(payload["results"][0]["shop"]["code"], "ops")

    def test_non_staff_is_forbidden(self):
        self._set_auth_cookie(self.non_staff_user)
        res = self.client.get("/api/v1/ops/orders", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 403)

    def test_ops_order_actions_and_jobs(self):
        self._set_auth_cookie(self.staff_user)
        assign_res = self.client.post(
            f"/api/v1/ops/orders/{self.order.id}/actions/assign",
            data={"assignee": "operator-1", "note": "assign"},
            content_type="application/json",
            HTTP_HOST="ops.local",
        )
        self.assertEqual(assign_res.status_code, 200)

        bulk_res = self.client.post(
            "/api/v1/ops/orders/actions/bulk",
            data={"action": "move_to_picking", "selected_ids": [self.order.id], "note": "bulk"},
            content_type="application/json",
            HTTP_HOST="ops.local",
        )
        self.assertEqual(bulk_res.status_code, 200)
        payload = bulk_res.json()
        self.assertIn(payload["status"], ["success", "failed"])
        self.assertTrue(OpsJob.objects.filter(id=payload["id"]).exists())

    def test_ops_payments_inventory_and_catalog_endpoints(self):
        self._set_auth_cookie(self.staff_user)

        list_payments = self.client.get("/api/v1/ops/payments", HTTP_HOST="ops.local")
        self.assertEqual(list_payments.status_code, 200)
        self.assertGreaterEqual(list_payments.json()["total_count"], 1)

        mark_paid = self.client.post(
            f"/api/v1/ops/payments/{self.payment.id}/mark-paid",
            data={"note": "manual paid"},
            content_type="application/json",
            HTTP_HOST="ops.local",
        )
        self.assertEqual(mark_paid.status_code, 200)
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentIntent.Status.SUCCEEDED)
        self.assertEqual(self.order.status, Order.Status.PAID)

        adjust = self.client.post(
            f"/api/v1/ops/inventory/items/{self.inventory_item.id}/adjust",
            data={"delta": 1, "reason_code": "restock", "comment": "test"},
            content_type="application/json",
            HTTP_HOST="ops.local",
        )
        self.assertEqual(adjust.status_code, 200)
        self.inventory_item.refresh_from_db()
        self.assertEqual(self.inventory_item.qty_on_hand, 11)

        toggle = self.client.post(
            f"/api/v1/ops/catalog/products/{self.product.id}/toggle-active",
            data={},
            content_type="application/json",
            HTTP_HOST="ops.local",
        )
        self.assertEqual(toggle.status_code, 200)
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)

        upd_price = self.client.post(
            f"/api/v1/ops/catalog/variants/{self.variant.id}/update-price",
            data={"price_eur": "12.50"},
            content_type="application/json",
            HTTP_HOST="ops.local",
        )
        self.assertEqual(upd_price.status_code, 200)
        self.variant.refresh_from_db()
        self.assertEqual(str(self.variant.price_eur), "12.50")

    def test_order_line_without_order_raises_integrity_not_related_object_error(self):
        line = OrderLine(
            variant=self.variant,
            sku="",
            name="",
            unit_net="10.00",
            vat_rate=None,
            unit_vat="0.00",
            unit_gross="0.00",
            qty=1,
            total_net="0.00",
            total_vat="0.00",
            total_gross="0.00",
        )
        with self.assertRaises(IntegrityError):
            line.save()

    def test_order_line_autofills_unit_net_from_variant_price(self):
        line = OrderLine(
            order=self.order,
            variant=self.variant,
            sku="",
            name="",
            unit_net=None,
            vat_rate=None,
            unit_vat="0.00",
            unit_gross="0.00",
            qty=1,
            total_net="0.00",
            total_vat="0.00",
            total_gross="0.00",
        )
        line.save()
        self.assertEqual(str(line.unit_net), str(self.variant.price_eur))
        self.assertEqual(line.sku, self.variant.sku)
        self.assertEqual(line.name, self.variant.product.name)


@override_settings(ALLOWED_HOSTS=["*"], SHOP_CONTEXT_ENABLED=True, SHOP_CONTEXT_STRICT=True)
class OrderStatusLabelsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.shop = Shop.objects.create(
            code="sl",
            name="Status Shop",
            primary_domain="sl.local",
            default_language_code="lt",
            default_country_code="LT",
            currency="EUR",
            is_active=True,
        )
        ShopLanguage.objects.create(
            shop=self.shop, language_code="lt", is_active=True, is_default=True, sort_order=0)
        ShopLanguage.objects.create(
            shop=self.shop, language_code="en", is_active=True, is_default=False, sort_order=1)

        self.user = User.objects.create_user(
            email="status@example.com", password="pass1234")

        self.order = Order.objects.create(
            user=self.user,
            shop=self.shop,
            status=Order.Status.PAID,
            delivery_status=Order.DeliveryStatus.IN_TRANSIT,
            fulfillment_mode=Order.FulfillmentMode.STOCK,
            shipping_method="dpd_pickup",
            country_code="LT",
            currency="EUR",
        )
        PaymentIntent.objects.create(
            order=self.order,
            provider=PaymentIntent.Provider.BANK_TRANSFER,
            status=PaymentIntent.Status.PENDING,
            currency="EUR",
            amount_gross="12.10",
        )

        token = issue_access_token(user_id=self.user.id)
        self.client.cookies["access_token"] = token

    def test_statuses_endpoint_returns_localized_labels(self):
        res = self.client.get("/api/v1/checkout/statuses", HTTP_HOST="sl.local")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertIn("order_status", data)
        self.assertIn("delivery_status", data)
        self.assertIn("payment_status", data)

        order_statuses = {o["code"]: o["label"] for o in data["order_status"]}
        self.assertEqual(order_statuses["paid"], "Apmokėta")
        self.assertEqual(order_statuses["pending_payment"], "Laukiama apmokėjimo")

        delivery_statuses = {o["code"]: o["label"] for o in data["delivery_status"]}
        self.assertEqual(delivery_statuses["in_transit"], "Siunta kelyje")
        self.assertEqual(delivery_statuses["at_pickup_point"], "Pristatyta į paštomatą")

    def test_statuses_endpoint_respects_lang_param(self):
        res = self.client.get("/api/v1/checkout/statuses?lang=en", HTTP_HOST="sl.local")
        self.assertEqual(res.status_code, 200)
        order_statuses = {o["code"]: o["label"] for o in res.json()["order_status"]}
        self.assertEqual(order_statuses["paid"], "Paid")

    def test_orders_endpoint_returns_localized_labels(self):
        res = self.client.get("/api/v1/checkout/orders", HTTP_HOST="sl.local")
        self.assertEqual(res.status_code, 200)
        orders = res.json()
        self.assertEqual(len(orders), 1)

        o = orders[0]
        self.assertEqual(o["status"], "paid")
        self.assertEqual(o["status_label"], "Apmokėta")
        self.assertEqual(o["delivery_status"], "in_transit")
        self.assertEqual(o["delivery_status_label"], "Siunta kelyje")
        self.assertEqual(o["payment_provider_label"], "Bankinis pavedimas")
        self.assertEqual(o["payment_status_label"], "Laukiama")

    def test_orders_endpoint_localizes_to_en(self):
        res = self.client.get("/api/v1/checkout/orders?lang=en", HTTP_HOST="sl.local")
        self.assertEqual(res.status_code, 200)
        o = res.json()[0]
        self.assertEqual(o["status_label"], "Paid")
        self.assertEqual(o["delivery_status_label"], "Parcel in transit")
        self.assertEqual(o["payment_status_label"], "Pending")


@override_settings(ALLOWED_HOSTS=["*"], SHOP_CONTEXT_ENABLED=True, SHOP_CONTEXT_STRICT=True)
class OpsShipmentsAndOrderManagementTests(TestCase):
    def setUp(self):
        from catalog.models import TaxRate
        from shipping.models import ShippingMethod, ShippingRate
        from checkout.models import Shipment, Surcharge  # noqa: F401

        self.client = Client()
        self.shop = Shop.objects.create(
            code="ops", name="Ops Shop", primary_domain="ops.local",
            default_language_code="lt", default_country_code="LT",
            currency="EUR", is_active=True,
        )
        ShopLanguage.objects.create(
            shop=self.shop, language_code="lt", is_active=True,
            is_default=True, sort_order=0)

        self.staff = User.objects.create_user(
            email="staff@example.com", password="pass1234", is_staff=True)
        self.customer = User.objects.create_user(
            email="cust@example.com", password="pass1234")

        tax, _ = TaxClass.objects.get_or_create(
            code="standard", defaults={"name": "Standard"})
        import datetime
        TaxRate.objects.create(
            tax_class=tax, country_code="LT", rate="0.21",
            valid_from=datetime.date(2020, 1, 1), is_active=True)

        self.local_wh = Warehouse.objects.create(
            code="main", name="Main WH", country_code="LT", is_active=True)
        self.remote_wh = Warehouse.objects.create(
            code="sup1", name="Supplier WH", country_code="PL",
            kind="remote", is_active=True)

        suffix = self._testMethodName.lower()
        cat = Category.objects.create(
            name="C", slug=f"c-{suffix}", is_active=True)
        product = Product.objects.create(
            sku="P-1", name="Drill", slug=f"p-{suffix}",
            tax_class=tax, category=cat, is_active=True)
        self.variant = Variant.objects.create(
            product=product, sku="V-1", name="v1",
            price_eur="10.00", is_active=True)
        self.variant2 = Variant.objects.create(
            product=product, sku="V-2", name="v2",
            price_eur="20.00", is_active=True)
        self.local_offer = InventoryItem.objects.create(
            variant=self.variant, warehouse=self.local_wh, qty_on_hand=10)
        self.remote_offer = InventoryItem.objects.create(
            variant=self.variant2, warehouse=self.remote_wh, qty_on_hand=5)

        sm_locker, _ = ShippingMethod.objects.get_or_create(
            code="dpd_locker", defaults={
                "name": "DPD locker", "carrier_code": "dpd",
                "requires_pickup_point": True, "is_active": True})
        sm_courier, _ = ShippingMethod.objects.get_or_create(
            code="dpd_courier", defaults={
                "name": "DPD courier", "carrier_code": "dpd",
                "is_active": True})
        sm_locker.is_active = True
        sm_locker.save()
        sm_courier.is_active = True
        sm_courier.save()
        ShippingRate.objects.update_or_create(
            method=sm_locker, country_code="LT",
            defaults={"net_eur": "2.00", "is_active": True})
        ShippingRate.objects.update_or_create(
            method=sm_courier, country_code="LT",
            defaults={"net_eur": "5.00", "is_active": True})

        self.order = Order.objects.create(
            user=self.customer, shop=self.shop,
            status=Order.Status.PAID,
            shipping_method="dpd_locker", country_code="LT",
        )
        OrderLine.objects.create(
            order=self.order, variant=self.variant, offer=self.local_offer,
            sku="V-1", name="Drill", unit_net="10.00", vat_rate="0.21",
            qty=1, total_net="10.00", total_vat="2.10", total_gross="12.10")
        self.order.recalculate_totals()
        self.order.save()

    def _auth(self):
        self.client.cookies["access_token"] = issue_access_token(
            user_id=self.staff.id)

    def test_fulfillment_mode_auto_local(self):
        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_mode, "stock")

    def test_fulfillment_mode_mixed_and_remote(self):
        from checkout.models import Shipment  # noqa
        OrderLine.objects.create(
            order=self.order, variant=self.variant2, offer=self.remote_offer,
            sku="V-2", name="Drill2", unit_net="20.00", vat_rate="0.21",
            qty=1, total_net="20.00", total_vat="4.20", total_gross="24.20")
        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_mode, "mixed")

        o2 = Order.objects.create(user=self.customer, shop=self.shop,
                                  status=Order.Status.PAID)
        OrderLine.objects.create(
            order=o2, variant=self.variant2, offer=self.remote_offer,
            sku="V-2", name="x", unit_net="20.00", vat_rate="0.21",
            qty=1, total_net="20.00", total_vat="4.20", total_gross="24.20")
        o2.refresh_from_db()
        self.assertEqual(o2.fulfillment_mode, "remote")

    def test_shipment_create_registry_search_and_update(self):
        self._auth()
        res = self.client.post(
            f"/api/v1/ops/orders/{self.order.id}/shipments",
            data={"kind": "outbound", "tracking_number": "ABC123"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        sid = res.json()["id"]
        self.assertEqual(res.json()["tracking_number"], "ABC123")

        res = self.client.get(
            "/api/v1/ops/shipments/registry",
            {"tracking": "abc12"}, HTTP_HOST="ops.local")
        self.assertEqual(len(res.json()["results"]), 1)
        self.assertEqual(res.json()["results"][0]["id"], sid)

        res = self.client.post(
            f"/api/v1/ops/orders/{self.order.id}/shipments",
            data={"kind": "return", "tracking_number": "RET999",
                  "notes": "customer return"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.json()["kind"], "return")

        res = self.client.patch(
            f"/api/v1/ops/shipments/{sid}",
            data={"carrier_code": "dpd", "status": "in_transit"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.tracking_number, "ABC123")
        self.assertEqual(self.order.delivery_status, "in_transit")

    def test_registry_lists_all_shipments_per_order(self):
        """The /siuntos list is Shipment-based: every parcel of an order must
        appear as its own row (outbound + return + cancelled forks)."""
        self._auth()
        from checkout.models import Shipment
        out1 = Shipment.objects.create(order=self.order, kind="outbound",
                                       status="delivered", tracking_number="T1")
        out2 = Shipment.objects.create(order=self.order, kind="outbound",
                                       status="pending", tracking_number="T2")
        ret = Shipment.objects.create(order=self.order, kind="return",
                                      status="in_transit", tracking_number="R1")

        res = self.client.get(
            "/api/v1/ops/shipments/registry",
            {"order_id": self.order.id}, HTTP_HOST="ops.local")
        body = res.json()
        self.assertEqual(body["total_count"], 3)
        self.assertEqual({r["id"] for r in body["results"]},
                         {out1.id, out2.id, ret.id})

        res = self.client.get(
            "/api/v1/ops/shipments/registry",
            {"order_id": self.order.id, "kind": "return"}, HTTP_HOST="ops.local")
        self.assertEqual([r["id"] for r in res.json()["results"]], [ret.id])

        res = self.client.get(
            "/api/v1/ops/shipments/registry",
            {"order_id": self.order.id, "page_size": 2}, HTTP_HOST="ops.local")
        body = res.json()
        self.assertEqual(body["total_count"], 3)
        self.assertEqual(len(body["results"]), 2)
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["page_size"], 2)

    def test_aggregate_delivery_status(self):
        from checkout.models import Shipment, aggregate_delivery_status
        Shipment.objects.create(order=self.order, kind="outbound",
                                status="in_transit")
        s2 = Shipment.objects.create(order=self.order, kind="outbound",
                                     status="delivered")
        self.assertEqual(aggregate_delivery_status(self.order), "in_transit")
        s2.status = "delivered"
        s2.save()
        Shipment.objects.filter(order=self.order).update(status="delivered")
        self.assertEqual(aggregate_delivery_status(self.order), "delivered")

    def test_update_order_shipping_price_delta(self):
        self._auth()
        res = self.client.patch(
            f"/api/v1/ops/orders/{self.order.id}/shipping",
            data={"shipping_method": "dpd_courier"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        # locker 2.00 net vs courier 5.00 net -> delta = 3.63 gross (21%)
        self.assertEqual(payload["shipping_method"], "dpd_courier")
        self.assertEqual(payload["price_delta_gross"], "3.63")

    def test_surcharge_bank_transfer(self):
        self._auth()
        res = self.client.post(
            f"/api/v1/ops/orders/{self.order.id}/surcharges",
            data={"amount_gross": "3.63", "reason": "carrier change",
                  "method": "bank_transfer", "send_email": False},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["amount_gross"], "3.63")

        from checkout.models import Surcharge
        s = Surcharge.objects.get(id=payload["id"])
        self.assertEqual(s.reference, f"{self.order.id}-sur-{s.id}")

    def test_ops_create_order(self):
        self._auth()
        res = self.client.post(
            "/api/v1/ops/orders",

            data={
                "user_email": "newcustomer@example.com",
                "shipping_method": "dpd_courier",
                "shipping_full_name": "Jonas Jonaitis",
                "shipping_line1": "Gatve 1",
                "shipping_city": "Kaunas",
                "shipping_postal_code": "44000",
                "shipping_country_code": "LT",
                "lines": [{"variant_id": self.variant.id, "qty": 2}],
            },
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["totals"]["items_gross"], "24.20")
        # dpd_courier shipping = 5.00 net * 1.21 = 6.05 gross
        self.assertEqual(payload["totals"]["shipping_gross"], "6.05")

    def test_ops_merge_orders(self):
        self._auth()
        o2 = Order.objects.create(
            user=self.customer, shop=self.shop,
            status=Order.Status.PENDING_PAYMENT,
            shipping_method="dpd_locker", country_code="LT")
        OrderLine.objects.create(
            order=o2, variant=self.variant2, offer=self.remote_offer,
            sku="V-2", name="Drill2", unit_net="20.00", vat_rate="0.21",
            qty=1, total_net="20.00", total_vat="4.20", total_gross="24.20")
        o2.recalculate_totals()
        o2.save()

        res = self.client.post(
            "/api/v1/ops/orders/actions/merge",
            data={"primary_order_id": self.order.id,
                  "merged_order_ids": [o2.id]},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)

        o2.refresh_from_db()
        self.assertEqual(o2.status, Order.Status.CANCELLED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.lines.count(), 2)
        # 12.10 + 24.20 = 36.30 items gross
        self.assertEqual(str(self.order.items_gross), "36.30")


@override_settings(ALLOWED_HOSTS=["*"], SHOP_CONTEXT_ENABLED=True, SHOP_CONTEXT_STRICT=True)
class OpsOrderEditingBalanceTests(TestCase):
    """Order line editing + automatic charge/refund balance on paid orders."""

    def _mk_paid_order(self):
        from checkout.models import PaymentIntent
        order = Order.objects.create(
            user=self.customer, shop=self.shop,
            status=Order.Status.PAID,
            shipping_method="dpd_locker", country_code="LT")
        OrderLine.objects.create(
            order=order, variant=self.variant, offer=self.local_offer,
            sku="V-1", name="Drill", unit_net="10.00", vat_rate="0.21",
            qty=1, total_net="10.00", total_vat="2.10", total_gross="12.10")
        order.recalculate_totals()
        order.save()
        PaymentIntent.objects.create(
            order=order, provider="montonio",
            status=PaymentIntent.Status.SUCCEEDED,
            amount_gross=order.total_gross)
        return order

    def setUp(self):
        from catalog.models import TaxRate
        from shipping.models import ShippingMethod, ShippingRate

        self.client = Client()
        self.shop = Shop.objects.create(
            code="opsx", name="Ops2", primary_domain="ops.local",
            default_language_code="lt", default_country_code="LT",
            currency="EUR", is_active=True)
        ShopLanguage.objects.create(
            shop=self.shop, language_code="lt", is_active=True,
            is_default=True, sort_order=0)
        self.staff = User.objects.create_user(
            email="staff2@example.com", password="pass1234", is_staff=True)
        self.customer = User.objects.create_user(
            email="cust2@example.com", password="pass1234")

        tax, _ = TaxClass.objects.get_or_create(
            code="standard", defaults={"name": "Standard"})
        import datetime
        TaxRate.objects.get_or_create(
            tax_class=tax, country_code="LT",
            defaults={"rate": "0.21",
                      "valid_from": datetime.date(2020, 1, 1),
                      "is_active": True})

        self.local_wh = Warehouse.objects.create(
            code="main2", name="Main WH2", country_code="LT", is_active=True)
        cat = Category.objects.create(
            name="C", slug=f"c2-{self._testMethodName.lower()}", is_active=True)
        product = Product.objects.create(
            sku="P-2", name="Drill", slug=f"p2-{self._testMethodName.lower()}",
            tax_class=tax, category=cat, is_active=True)
        self.variant = Variant.objects.create(
            product=product, sku="V-10", name="v1",
            price_eur="10.00", is_active=True)
        self.variant2 = Variant.objects.create(
            product=product, sku="V-20", name="v2",
            price_eur="20.00", is_active=True)
        self.local_offer = InventoryItem.objects.create(
            variant=self.variant, warehouse=self.local_wh, qty_on_hand=10)
        self.local_offer2 = InventoryItem.objects.create(
            variant=self.variant2, warehouse=self.local_wh, qty_on_hand=10)

        for code, name, net in [("dpd_locker", "DPD locker", "2.00"),
                                ("dpd_courier", "DPD courier", "5.00")]:
            sm, _ = ShippingMethod.objects.get_or_create(
                code=code, defaults={
                    "name": name, "carrier_code": "dpd", "is_active": True})
            sm.is_active = True
            sm.save()
            ShippingRate.objects.update_or_create(
                method=sm, country_code="LT",
                defaults={"net_eur": net, "is_active": True})

    def _auth(self):
        self.client.cookies["access_token"] = issue_access_token(
            user_id=self.staff.id)

    def test_line_add_paid_order_creates_pending_charge(self):
        from checkout.models import PaymentIntent, Surcharge
        order = self._mk_paid_order()
        self._auth()

        res = self.client.post(
            f"/api/v1/ops/orders/{order.id}/lines",
            data={"variant_id": self.variant2.id, "qty": 1},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["matched_lines"]), 2)

        order.refresh_from_db()
        # total = 12.10 + 24.20 + shipping 2.42 = 38.72
        self.assertEqual(str(order.total_gross), "38.72")

        s = order.surcharges.get()
        self.assertEqual(s.direction, "charge")
        self.assertEqual(s.source, "auto")
        self.assertEqual(s.status, "pending")
        self.assertEqual(str(s.amount_gross), "24.20")
        self.assertEqual(s.method, "")
        self.assertEqual(res.json()["totals"]["balance_due"], "24.20")
        self.assertEqual(res.json()["totals"]["paid_gross"], str(
            PaymentIntent.objects.get(order=order).amount_gross))

    def test_line_remove_paid_order_creates_pending_refund(self):
        order = self._mk_paid_order()
        line = order.lines.get()
        self._auth()

        res = self.client.patch(
            f"/api/v1/ops/orders/{order.id}/lines/{line.id}",
            data={"qty": 0},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["matched_lines"]), 0)

        order.refresh_from_db()
        # items 0, shipping 2.42 -> total 2.42; paid was 14.52 -> refund 12.10
        self.assertEqual(str(order.total_gross), "2.42")
        s = order.surcharges.get()
        self.assertEqual(s.direction, "refund")
        self.assertEqual(str(s.amount_gross), "12.10")

    def test_line_qty_reduce_updates_pending_balance(self):
        order = self._mk_paid_order()
        line = order.lines.get()
        self._auth()

        # increase qty 1 -> 3: +2 * 12.10 = +24.20 charge
        res = self.client.patch(
            f"/api/v1/ops/orders/{order.id}/lines/{line.id}",
            data={"qty": 3},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        s = order.surcharges.get()
        self.assertEqual(str(s.amount_gross), "24.20")

        # reduce back to 1: balance clears -> pending row cancelled
        res = self.client.patch(
            f"/api/v1/ops/orders/{order.id}/lines/{line.id}",
            data={"qty": 1},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        s.refresh_from_db()
        self.assertEqual(s.status, "cancelled")

    def test_line_add_unpaid_order_no_surcharge(self):
        order = Order.objects.create(
            user=self.customer, shop=self.shop,
            status=Order.Status.PENDING_PAYMENT,
            shipping_method="dpd_locker", country_code="LT")
        OrderLine.objects.create(
            order=order, variant=self.variant, offer=self.local_offer,
            sku="V-1", name="Drill", unit_net="10.00", vat_rate="0.21",
            qty=1, total_net="10.00", total_vat="2.10", total_gross="12.10")
        order.recalculate_totals()
        order.save()
        self._auth()

        res = self.client.post(
            f"/api/v1/ops/orders/{order.id}/lines",
            data={"variant_id": self.variant2.id, "qty": 1},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(order.surcharges.count(), 0)

    def test_shipping_change_new_shipment_and_auto_charge(self):
        from checkout.models import Shipment
        order = self._mk_paid_order()
        self._auth()

        # first change: creates an outbound shipment
        res = self.client.patch(
            f"/api/v1/ops/orders/{order.id}/shipping",
            data={"shipping_method": "dpd_courier"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertTrue(payload["new_shipment_id"])
        self.assertEqual(payload["balance_due"], "3.63")
        s1 = Shipment.objects.get(id=payload["new_shipment_id"])
        self.assertEqual(s1.shipping_method, "dpd_courier")
        self.assertEqual(s1.status, "pending")

        s = order.surcharges.get()
        self.assertEqual(s.direction, "charge")
        self.assertEqual(str(s.amount_gross), "3.63")

        # second change back to locker: another new shipment, old cancelled,
        # balance clears -> pending charge cancelled
        res = self.client.patch(
            f"/api/v1/ops/orders/{order.id}/shipping",
            data={"shipping_method": "dpd_locker"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        s1.refresh_from_db()
        self.assertEqual(s1.status, "cancelled")
        self.assertEqual(order.shipments.filter(
            status="pending", kind="outbound").count(), 1)
        s.refresh_from_db()
        self.assertEqual(s.status, "cancelled")

    def test_variant_search_picker(self):
        self._auth()
        res = self.client.get(
            "/api/v1/ops/catalog/variants/search",
            {"q": "V-20"}, HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        rows = res.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["variant_id"], self.variant2.id)
        self.assertEqual(rows[0]["unit_gross"], "24.20")
        self.assertEqual(rows[0]["stock_available"], 10)

        res = self.client.get(
            "/api/v1/ops/catalog/variants/search",
            {"q": "drill"}, HTTP_HOST="ops.local")
        self.assertEqual(len(res.json()), 2)

    def test_order_address_picker_and_snapshot_fallback(self):
        from accounts.models import UserAddress
        from checkout.services import order_shipping_snapshot

        order = Order.objects.create(
            user=self.customer, shop=self.shop,
            status=Order.Status.PAID,
            shipping_method="dpd_courier", country_code="LT")
        addr = UserAddress.objects.create(
            user=self.customer, full_name="Jonas Jonaitis",
            line1="Strazdu 1", city="Kaunas", postal_code="53454",
            country_code="LT", phone="+37060000001",
            is_default_shipping=True)

        # Label-layer snapshot falls back to the saved address.
        snap = order_shipping_snapshot(order)
        self.assertEqual(snap["line1"], "Strazdu 1")
        self.assertEqual(snap["phone"], "+37060000001")

        self._auth()
        # List customer addresses for the picker.
        res = self.client.get(
            f"/api/v1/ops/orders/{order.id}/addresses",
            HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 1)

        # Apply the saved address.
        res = self.client.patch(
            f"/api/v1/ops/orders/{order.id}/address",
            data={"user_address_id": addr.id},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.shipping_line1, "Strazdu 1")
        self.assertEqual(order.shipping_city, "Kaunas")

        # Manual address entry saved to the customer profile.
        res = self.client.patch(
            f"/api/v1/ops/orders/{order.id}/address",
            data={"line1": "Gedimino 5", "city": "Vilnius",
                  "postal_code": "01103", "country_code": "lt",
                  "phone": "+37060000002", "save_to_customer": True,
                  "set_default_shipping": True},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.shipping_line1, "Gedimino 5")
        new_addr = order.user.addresses.get(is_default_shipping=True)
        self.assertEqual(new_addr.line1, "Gedimino 5")
        addr.refresh_from_db()
        self.assertFalse(addr.is_default_shipping)

    def test_dpd_ensure_shipment_writes_model_fields(self):
        from unittest.mock import MagicMock, patch
        from checkout.models import Shipment
        from dpd.labels import ensure_dpd_shipment

        order = Order.objects.create(
            user=self.customer, shop=self.shop,
            status=Order.Status.PAID,
            shipping_method="dpd_courier", country_code="LT")
        shipment = Shipment.objects.create(
            order=order, kind="outbound", status="pending",
            shipping_method="dpd_courier")

        client = MagicMock()
        client.create_shipments.return_value = [
            {"id": "SH-X", "parcelNumbers": ["0581999"]}]
        with patch("dpd.labels.build_shipment_dto", return_value={}), \
                patch("dpd.labels._get_shipment_cfg", return_value=MagicMock()):
            sid, parcel = ensure_dpd_shipment(
                order, client=client, shipment=shipment)

        self.assertEqual(sid, "SH-X")
        self.assertEqual(parcel, "0581999")
        shipment.refresh_from_db()
        self.assertEqual(shipment.carrier_code, "dpd")
        self.assertEqual(shipment.carrier_shipment_id, "SH-X")
        self.assertEqual(shipment.tracking_number, "0581999")
        order.refresh_from_db()
        self.assertEqual(order.tracking_number, "0581999")

    def test_preferred_pickup_point_fallback(self):
        from accounts.models import UserPickupPoint
        from checkout.services import preferred_pickup_point

        order = Order.objects.create(
            user=self.customer, shop=self.shop,
            status=Order.Status.PAID,
            shipping_method="dpd_locker", country_code="LT")
        self.assertIsNone(preferred_pickup_point(order))

        UserPickupPoint.objects.create(
            user=self.customer, shipping_method_code="dpd_locker",
            pickup_point_id="LT99999", pickup_point_name="Test locker",
            country_code="LT")
        pref = preferred_pickup_point(order)
        self.assertEqual(pref["pickup_point_id"], "LT99999")

        # Different method -> not applied.
        order.shipping_method = "unisend_pickup"
        self.assertIsNone(preferred_pickup_point(order))

    def test_shipment_locker_populates_pudo_id(self):
        from dpd.models import DpdLocker
        locker = DpdLocker.objects.create(
            locker_id="LT11111", name="Test locker", city="Vilnius",
            country_code="LT", is_active=True)
        order = self._mk_paid_order()
        self._auth()
        res = self.client.post(
            f"/api/v1/ops/orders/{order.id}/shipments",
            data={"kind": "outbound", "shipping_method": "dpd_locker",
                  "pickup_locker_id": locker.id},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["pickup_point_id"], "LT11111")

        res = self.client.patch(
            f"/api/v1/ops/orders/{order.id}/shipping",
            data={"shipping_method": "dpd_locker",
                  "pickup_locker_id": locker.id},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.pickup_point_id, "LT11111")

    def test_label_regen_forks_shipment(self):
        from unittest.mock import patch
        from django.utils import timezone
        from checkout.models import Shipment

        order = self._mk_paid_order()
        shipment = Shipment.objects.create(
            order=order, kind="outbound", status="packed",
            shipping_method="dpd_locker", carrier_code="dpd",
            tracking_number="OLD123", carrier_shipment_id="SHIP-OLD",
            label_generated_at=timezone.now())
        self._auth()

        with patch(
            "dpd.labels.generate_a6_label_pdf_for_order"
        ) as gen:
            res = self.client.post(
                f"/api/v1/ops/shipments/id/{shipment.id}/label",
                content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        gen.assert_called_once()
        new_id = res.json()["updated_ids"][0]
        self.assertNotEqual(new_id, shipment.id)

        shipment.refresh_from_db()
        self.assertEqual(shipment.status, "cancelled")
        self.assertEqual(shipment.tracking_number, "OLD123")
        fresh = Shipment.objects.get(id=new_id)
        self.assertEqual(fresh.status, "pending")
        self.assertEqual(fresh.tracking_number, "")
        order.refresh_from_db()
        self.assertEqual(order.tracking_number, "")


@override_settings(ALLOWED_HOSTS=["*"], SHOP_CONTEXT_ENABLED=True, SHOP_CONTEXT_STRICT=True)
class OpsInventoryReservationTests(TestCase):
    """Admin order management must keep warehouse reservations in sync:
    create/add/qty/remove/merge -> InventoryAllocation + offer + supplier flag."""

    def setUp(self):
        from catalog.models import TaxRate
        from shipping.models import ShippingMethod, ShippingRate

        self.client = Client()
        self.shop = Shop.objects.create(
            code="opsr", name="OpsR", primary_domain="ops.local",
            default_language_code="lt", default_country_code="LT",
            currency="EUR", is_active=True)
        ShopLanguage.objects.create(
            shop=self.shop, language_code="lt", is_active=True,
            is_default=True, sort_order=0)
        self.staff = User.objects.create_user(
            email="staffr@example.com", password="pass1234", is_staff=True)
        self.customer = User.objects.create_user(
            email="custr@example.com", password="pass1234")

        tax, _ = TaxClass.objects.get_or_create(
            code="standard", defaults={"name": "Standard"})
        import datetime
        TaxRate.objects.get_or_create(
            tax_class=tax, country_code="LT",
            defaults={"rate": "0.21",
                      "valid_from": datetime.date(2020, 1, 1),
                      "is_active": True})

        self.local_wh = Warehouse.objects.create(
            code="locwh", name="Local WH", country_code="LT",
            kind=Warehouse.Kind.LOCAL, sort_order=0, is_active=True)
        self.remote_wh = Warehouse.objects.create(
            code="suppx", name="Supplier X", country_code="PL",
            kind=Warehouse.Kind.REMOTE, sort_order=10, is_active=True)

        cat = Category.objects.create(
            name="C", slug=f"c3-{self._testMethodName.lower()}", is_active=True)
        product = Product.objects.create(
            sku="P-3", name="Reel", slug=f"p3-{self._testMethodName.lower()}",
            tax_class=tax, category=cat, is_active=True)
        self.variant = Variant.objects.create(
            product=product, sku="V-100", name="v1",
            price_eur="10.00", is_active=True)
        self.variant_remote = Variant.objects.create(
            product=product, sku="V-200", name="v2",
            price_eur="20.00", is_active=True)
        self.variant_nostock = Variant.objects.create(
            product=product, sku="V-300", name="v3",
            price_eur="5.00", is_active=True)
        self.local_offer = InventoryItem.objects.create(
            variant=self.variant, warehouse=self.local_wh, qty_on_hand=10)
        self.remote_offer = InventoryItem.objects.create(
            variant=self.variant_remote, warehouse=self.remote_wh,
            qty_on_hand=50)

        sm, _ = ShippingMethod.objects.get_or_create(
            code="dpd_locker", defaults={
                "name": "DPD locker", "carrier_code": "dpd",
                "is_active": True})
        ShippingRate.objects.update_or_create(
            method=sm, country_code="LT",
            defaults={"net_eur": "2.00", "is_active": True})

    def _auth(self):
        self.client.cookies["access_token"] = issue_access_token(
            user_id=self.staff.id)

    def _create_order(self, variant_id, qty=1):
        return self.client.post(
            "/api/v1/ops/orders",
            data={
                "user_id": self.customer.id,
                "shipping_method": "dpd_locker",
                "lines": [{"variant_id": variant_id, "qty": qty}],
            },
            content_type="application/json", HTTP_HOST="ops.local")

    def test_create_order_reserves_stock_and_sets_offer(self):
        from checkout.models import InventoryAllocation
        self._auth()
        res = self._create_order(self.variant.id, qty=3)
        self.assertEqual(res.status_code, 200, res.content[:300])

        self.local_offer.refresh_from_db()
        self.assertEqual(int(self.local_offer.qty_reserved), 3)

        line = OrderLine.objects.get(sku="V-100")
        self.assertEqual(line.offer_id, self.local_offer.id)
        alloc = InventoryAllocation.objects.get(order_line=line)
        self.assertEqual(alloc.status, "reserved")
        self.assertEqual(int(alloc.qty), 3)
        self.assertEqual(alloc.inventory_item_id, self.local_offer.id)

        out = res.json()
        m = out["matched_lines"][0]
        self.assertEqual(m["warehouse_code"], "locwh")
        self.assertEqual(m["warehouse_kind"], "local")
        self.assertEqual(m["allocated_qty"], 3)
        self.assertEqual(m["unallocated_qty"], 0)

        order = Order.objects.get(id=out["id"])
        self.assertEqual(order.supplier_reservation_status, "not_needed")

    def test_remote_item_reserves_but_status_stays_manual(self):
        """Remote-warehouse stock is reserved internally, but
        supplier_reservation_status stays a manual flag (not_needed) —
        the manager marks 'awaiting supplier' by hand."""
        self._auth()
        res = self._create_order(self.variant_remote.id, qty=2)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.remote_offer.refresh_from_db()
        self.assertEqual(int(self.remote_offer.qty_reserved), 2)

        order = Order.objects.get(id=res.json()["id"])
        self.assertEqual(order.supplier_reservation_status, "not_needed")
        m = res.json()["matched_lines"][0]
        self.assertEqual(m["warehouse_code"], "suppx")
        self.assertEqual(m["warehouse_kind"], "remote")

    def test_unstocked_item_stays_unallocated(self):
        self._auth()
        res = self._create_order(self.variant_nostock.id, qty=1)
        self.assertEqual(res.status_code, 200, res.content[:300])
        order = Order.objects.get(id=res.json()["id"])
        self.assertEqual(order.supplier_reservation_status, "not_needed")
        m = res.json()["matched_lines"][0]
        self.assertEqual(m["unallocated_qty"], 1)
        self.assertEqual(m["warehouse_code"], "")

    def test_line_add_qty_change_and_remove_adjust_reservation(self):
        self._auth()
        res = self._create_order(self.variant.id, qty=2)
        oid = res.json()["id"]
        line_id = res.json()["matched_lines"][0]["order_line_id"]

        self.local_offer.refresh_from_db()
        self.assertEqual(int(self.local_offer.qty_reserved), 2)

        # increase to 5
        res = self.client.patch(
            f"/api/v1/ops/orders/{oid}/lines/{line_id}",
            data={"qty": 5}, content_type="application/json",
            HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.local_offer.refresh_from_db()
        self.assertEqual(int(self.local_offer.qty_reserved), 5)

        # decrease to 1
        res = self.client.patch(
            f"/api/v1/ops/orders/{oid}/lines/{line_id}",
            data={"qty": 1}, content_type="application/json",
            HTTP_HOST="ops.local")
        self.local_offer.refresh_from_db()
        self.assertEqual(int(self.local_offer.qty_reserved), 1)

        # remove entirely
        res = self.client.patch(
            f"/api/v1/ops/orders/{oid}/lines/{line_id}",
            data={"qty": 0}, content_type="application/json",
            HTTP_HOST="ops.local")
        self.local_offer.refresh_from_db()
        self.assertEqual(int(self.local_offer.qty_reserved), 0)
        from checkout.models import InventoryAllocation
        self.assertFalse(
            InventoryAllocation.objects.filter(
                order_line_id=line_id).exists())

    def test_add_line_reserves_new_line(self):
        self._auth()
        res = self._create_order(self.variant.id, qty=1)
        oid = res.json()["id"]

        res = self.client.post(
            f"/api/v1/ops/orders/{oid}/lines",
            data={"variant_id": self.variant_remote.id, "qty": 2},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.remote_offer.refresh_from_db()
        self.assertEqual(int(self.remote_offer.qty_reserved), 2)
        order = Order.objects.get(id=oid)
        # supplier flag is manual — stock state doesn't change it
        self.assertEqual(order.supplier_reservation_status, "not_needed")


    def test_supplier_patch_sets_status_and_reference(self):
        self._auth()
        res = self._create_order(self.variant_remote.id, qty=1)
        oid = res.json()["id"]

        res = self.client.patch(
            f"/api/v1/ops/orders/{oid}/supplier",
            data={"supplier_reservation_status": "reserved",
                  "supplier_reference": "PO-777",
                  "supplier_notes": "confirmed by supplier"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        order = Order.objects.get(id=oid)
        self.assertEqual(order.supplier_reservation_status, "reserved")
        self.assertIsNotNone(order.supplier_reserved_at)
        self.assertEqual(order.supplier_reference, "PO-777")

    def test_merge_moves_allocations_to_primary(self):
        from checkout.models import InventoryAllocation
        self._auth()
        res1 = self._create_order(self.variant.id, qty=1)
        res2 = self._create_order(self.variant_remote.id, qty=1)
        oid1, oid2 = res1.json()["id"], res2.json()["id"]

        res = self.client.post(
            "/api/v1/ops/orders/actions/merge",
            data={"primary_order_id": oid1, "merged_order_ids": [oid2]},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            set(InventoryAllocation.objects.filter(
                order_id=oid1).values_list("qty", flat=True)),
            {1, 1})
        self.assertFalse(
            InventoryAllocation.objects.filter(order_id=oid2).exists())
        order = Order.objects.get(id=oid1)
        # manual flag — merge must not touch it
        self.assertEqual(order.supplier_reservation_status, "not_needed")

    def test_supplier_code_filter_matches_allocations(self):
        self._auth()
        res = self._create_order(self.variant_remote.id, qty=1)
        oid = res.json()["id"]
        res = self.client.get(
            "/api/v1/ops/orders",
            {"supplier_code": "suppx"}, HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        ids = [r["id"] for r in res.json()["results"]]
        self.assertIn(oid, ids)

    def test_supplier_status_and_barcode_filters(self):
        self._auth()
        res = self._create_order(self.variant_remote.id, qty=1)
        oid = res.json()["id"]

        # manager marks the order manually after ordering at the supplier
        self.client.patch(
            f"/api/v1/ops/orders/{oid}/supplier",
            data={"supplier_reservation_status": "pending"},
            content_type="application/json", HTTP_HOST="ops.local")

        res = self.client.get(
            "/api/v1/ops/orders",
            {"supplier_status": "pending"}, HTTP_HOST="ops.local")
        ids = [r["id"] for r in res.json()["results"]]
        self.assertIn(oid, ids)

        res = self.client.get(
            "/api/v1/ops/orders",
            {"supplier_status": "reserved"}, HTTP_HOST="ops.local")
        ids = [r["id"] for r in res.json()["results"]]
        self.assertNotIn(oid, ids)

        # barcode (EAN) lookup through product_q — scanner input
        self.variant_remote.barcode = "4770000000000"
        self.variant_remote.save(update_fields=["barcode"])
        res = self.client.get(
            "/api/v1/ops/orders",
            {"product_q": "4770000000000"}, HTTP_HOST="ops.local")
        ids = [r["id"] for r in res.json()["results"]]
        self.assertIn(oid, ids)


@override_settings(ALLOWED_HOSTS=["*"], SHOP_CONTEXT_ENABLED=True, SHOP_CONTEXT_STRICT=True)
class OpsLineEditDiscountTests(TestCase):
    """Order line editing (qty/price/name) and order discounts
    (percent/amount/coupon) with balance reconciliation."""

    def setUp(self):
        from catalog.models import TaxRate
        from shipping.models import ShippingMethod, ShippingRate

        self.client = Client()
        self.shop = Shop.objects.create(
            code="opsd", name="OpsD", primary_domain="ops.local",
            default_language_code="lt", default_country_code="LT",
            currency="EUR", is_active=True)
        ShopLanguage.objects.create(
            shop=self.shop, language_code="lt", is_active=True,
            is_default=True, sort_order=0)
        self.staff = User.objects.create_user(
            email="staffd@example.com", password="pass1234", is_staff=True)
        self.customer = User.objects.create_user(
            email="custd@example.com", password="pass1234")

        tax, _ = TaxClass.objects.get_or_create(
            code="standard", defaults={"name": "Standard"})
        import datetime
        TaxRate.objects.get_or_create(
            tax_class=tax, country_code="LT",
            defaults={"rate": "0.21",
                      "valid_from": datetime.date(2020, 1, 1),
                      "is_active": True})

        self.local_wh = Warehouse.objects.create(
            code="locwh", name="Local WH", country_code="LT",
            kind=Warehouse.Kind.LOCAL, sort_order=0, is_active=True)

        cat = Category.objects.create(
            name="C", slug=f"c4-{self._testMethodName.lower()}", is_active=True)
        product = Product.objects.create(
            sku="P-4", name="Reel", slug=f"p4-{self._testMethodName.lower()}",
            tax_class=tax, category=cat, is_active=True)
        self.variant = Variant.objects.create(
            product=product, sku="V-400", name="v1",
            price_eur="10.00", is_active=True)
        InventoryItem.objects.create(
            variant=self.variant, warehouse=self.local_wh, qty_on_hand=10)

        sm, _ = ShippingMethod.objects.get_or_create(
            code="dpd_locker", defaults={
                "name": "DPD locker", "carrier_code": "dpd",
                "is_active": True})
        ShippingRate.objects.update_or_create(
            method=sm, country_code="LT",
            defaults={"net_eur": "2.00", "is_active": True})

    def _auth(self):
        self.client.cookies["access_token"] = issue_access_token(
            user_id=self.staff.id)

    def _create_order(self, qty=2):
        res = self.client.post(
            "/api/v1/ops/orders",
            data={
                "user_id": self.customer.id,
                "shipping_method": "dpd_locker",
                "lines": [{"variant_id": self.variant.id, "qty": qty}],
            },
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()["id"]

    def _line(self, oid):
        res = self.client.get(
            f"/api/v1/ops/orders/{oid}", HTTP_HOST="ops.local")
        return res.json()["matched_lines"][0]

    def test_line_price_edit_gross(self):
        from decimal import Decimal as D
        self._auth()
        oid = self._create_order(qty=2)
        line = self._line(oid)

        res = self.client.patch(
            f"/api/v1/ops/orders/{oid}/lines/{line['order_line_id']}",
            data={"unit_gross": "9.50"}, content_type="application/json",
            HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])

        m = self._line(oid)
        self.assertEqual(m["unit_gross"], "9.50")
        self.assertEqual(m["line_total_gross"], "19.00")
        order = Order.objects.get(id=oid)
        # shipping 2.00 net * 1.21 = 2.42
        self.assertEqual(order.total_gross, D("21.42"))

    def test_line_qty_and_name_edit(self):
        self._auth()
        oid = self._create_order(qty=2)
        line = self._line(oid)

        res = self.client.patch(
            f"/api/v1/ops/orders/{oid}/lines/{line['order_line_id']}",
            data={"qty": 3, "name": "Custom name"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        m = self._line(oid)
        self.assertEqual(m["qty"], 3)
        self.assertEqual(m["name"], "Custom name")

        res = self.client.patch(
            f"/api/v1/ops/orders/{oid}/lines/{line['order_line_id']}",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 400)

    def test_percent_discount_reduces_total(self):
        from decimal import Decimal as D
        self._auth()
        oid = self._create_order(qty=2)  # items net 20.00

        res = self.client.post(
            f"/api/v1/ops/orders/{oid}/discount",
            data={"kind": "percent", "value": "10"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])

        out = res.json()
        self.assertEqual(out["discounts"][0]["kind"], "manual")
        self.assertEqual(out["discounts"][0]["gross"], "2.42")
        order = Order.objects.get(id=oid)
        self.assertEqual(order.total_gross, D("24.20") + D("2.42") - D("2.42"))

    def test_amount_discount_capped_at_items(self):
        from decimal import Decimal as D
        self._auth()
        oid = self._create_order(qty=1)  # items gross 12.10

        res = self.client.post(
            f"/api/v1/ops/orders/{oid}/discount",
            data={"kind": "amount", "value": "5.00", "name": "Apology"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        d = res.json()["discounts"][0]
        self.assertEqual(d["name"], "Apology")
        self.assertEqual(d["gross"], "5.00")

        res = self.client.post(
            f"/api/v1/ops/orders/{oid}/discount",
            data={"kind": "amount", "value": "999.00"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        order = Order.objects.get(id=oid)
        # capped: discounts cover items only; shipping still due
        self.assertGreater(order.total_gross, D("0.00"))
        self.assertEqual(order.total_gross, order.shipping_gross)

    def test_coupon_discount_applies_and_reserves(self):
        from promotions.models import Coupon, CouponRedemption
        self._auth()
        Coupon.objects.create(
            code="sale10", name="Sale 10%", percent_off=10, is_active=True)
        oid = self._create_order(qty=2)  # items net 20.00 -> -2.00 net

        res = self.client.post(
            f"/api/v1/ops/orders/{oid}/discount",
            data={"kind": "coupon", "code": "SALE10"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        d = res.json()["discounts"][0]
        self.assertEqual(d["kind"], "coupon")
        self.assertEqual(d["code"], "sale10")
        self.assertEqual(d["net"], "2.00")
        self.assertTrue(
            CouponRedemption.objects.filter(order_id=oid).exists())

        # second coupon rejected
        Coupon.objects.create(code="x5", percent_off=5, is_active=True)
        res = self.client.post(
            f"/api/v1/ops/orders/{oid}/discount",
            data={"kind": "coupon", "code": "x5"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 409)

    def test_coupon_invalid_and_remove_releases(self):
        from promotions.models import Coupon, CouponRedemption
        self._auth()
        oid = self._create_order(qty=1)

        res = self.client.post(
            f"/api/v1/ops/orders/{oid}/discount",
            data={"kind": "coupon", "code": "nope"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 400)

        coupon = Coupon.objects.create(
            code="ok5", percent_off=5, is_active=True)
        res = self.client.post(
            f"/api/v1/ops/orders/{oid}/discount",
            data={"kind": "coupon", "code": "ok5"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        did = res.json()["discounts"][0]["id"]
        coupon.refresh_from_db()
        self.assertEqual(int(coupon.times_redeemed), 1)

        res = self.client.post(
            f"/api/v1/ops/orders/{oid}/discounts/{did}/remove",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()["discounts"], [])
        self.assertFalse(
            CouponRedemption.objects.filter(order_id=oid).exists())
        coupon.refresh_from_db()
        self.assertEqual(int(coupon.times_redeemed), 0) 


@override_settings(ALLOWED_HOSTS=["*"], SHOP_CONTEXT_ENABLED=True, SHOP_CONTEXT_STRICT=True)
class OpsInvoiceEndpointsTests(OpsLineEditDiscountTests):
    """Invoice issue / credit / cancel ops endpoints."""

    def test_issue_credit_cancel_flow(self):
        from billing.models import Invoice
        self._auth()
        oid = self._create_order(qty=2)

        # no invoice yet -> issue manually
        res = self.client.post(
            f"/api/v1/ops/orders/{oid}/invoice",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        inv = res.json()
        self.assertEqual(inv["kind"], "sales")
        self.assertEqual(inv["status"], "issued")
        self.assertTrue(inv["number"].startswith("SF-"))

        # full credit
        res = self.client.post(
            f"/api/v1/ops/invoices/{inv['id']}/credit",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        credit = res.json()
        self.assertEqual(credit["kind"], "credit")
        self.assertTrue(credit["number"].startswith("KRF-"))
        self.assertEqual(credit["credit_of_number"], inv["number"])
        self.assertTrue(credit["total_gross"].startswith("-"))

        # second full credit rejected (lines already fully credited)
        res = self.client.post(
            f"/api/v1/ops/invoices/{inv['id']}/credit",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 400)

        # cancel the credit
        res = self.client.post(
            f"/api/v1/ops/invoices/{credit['id']}/cancel",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "cancelled")

        # after cancelling, crediting works again
        res = self.client.post(
            f"/api/v1/ops/invoices/{inv['id']}/credit",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)

        self.assertEqual(
            Invoice.objects.filter(order_id=oid, kind="credit").count(), 2)

    def test_invoice_list_scoped_to_order(self):
        self._auth()
        oid = self._create_order(qty=1)
        self.client.post(
            f"/api/v1/ops/orders/{oid}/invoice",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        res = self.client.get(
            "/api/v1/ops/invoices", {"order_id": oid}, HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 1)
        self.assertEqual(res.json()[0]["order_id"], oid)


class OpsReturnTests(OpsLineEditDiscountTests):
    """RMA lifecycle: create -> approve -> receive -> credit."""

    def _deliver(self, oid, days_ago=1):
        from django.utils import timezone
        order = Order.objects.get(id=oid)
        order.delivered_at = timezone.now() - timezone.timedelta(
            days=days_ago)
        order.save(update_fields=["delivered_at"])

    def _create_return(self, oid, line, qty=1, **extra):
        payload = {
            "lines": [{"order_line_id": line["order_line_id"], "qty": qty}],
            **extra,
        }
        return self.client.post(
            f"/api/v1/ops/orders/{oid}/returns",
            data=payload, content_type="application/json",
            HTTP_HOST="ops.local")

    def test_return_full_lifecycle(self):
        self._auth()
        oid = self._create_order(qty=2)
        self._deliver(oid)
        line = self._line(oid)

        res = self._create_return(oid, line, qty=1, approve=False)
        self.assertEqual(res.status_code, 200, res.content[:300])
        ret = res.json()
        self.assertEqual(ret["status"], "pending")
        self.assertTrue(ret["number"].startswith("RMA-"))

        res = self.client.post(
            f"/api/v1/ops/returns/{ret['id']}/status/approved",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()["status"], "approved")

        # return shipment with tracking
        res = self.client.post(
            f"/api/v1/ops/returns/{ret['id']}/shipment",
            data={"carrier_code": "dpd", "tracking_number": "R123"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()["tracking_number"], "R123")

        # receive goods
        rl = res.json()["lines"][0]
        res = self.client.post(
            f"/api/v1/ops/returns/{ret['id']}/receive",
            data={"lines": [{"return_line_id": rl["id"], "qty": 1}]},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()["status"], "received")
        self.assertEqual(res.json()["lines"][0]["received_qty"], 1)

        # credit requires a sales invoice first
        res = self.client.post(
            f"/api/v1/ops/returns/{ret['id']}/credit",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 400)
        self.client.post(
            f"/api/v1/ops/orders/{oid}/invoice",
            data={}, content_type="application/json", HTTP_HOST="ops.local")

        res = self.client.post(
            f"/api/v1/ops/returns/{ret['id']}/credit",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        out = res.json()
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["lines"][0]["credited_qty"], 1)
        self.assertTrue(out["credit_invoice_number"].startswith("KRF-"))

    def test_return_qty_and_window_validation(self):
        from django.utils import timezone
        self._auth()
        oid = self._create_order(qty=2)
        self._deliver(oid)
        line = self._line(oid)

        res = self._create_return(oid, line, qty=5)
        self.assertEqual(res.status_code, 400)

        res = self._create_return(oid, line, qty=2)
        self.assertEqual(res.status_code, 200)
        # cumulative: a second return for the same line is capped
        res = self._create_return(oid, line, qty=1)
        self.assertEqual(res.status_code, 400)

        # expired window: delivered 30d ago, default 14d
        oid2 = self._create_order(qty=1)
        self._deliver(oid2, days_ago=30)
        line2 = self._line(oid2)
        res = self._create_return(oid2, line2, qty=1)
        self.assertEqual(res.status_code, 400)
        res = self._create_return(oid2, line2, qty=1, allow_expired=True)
        self.assertEqual(res.status_code, 200, res.content[:300])

    def test_non_returnable_product_blocked(self):
        self._auth()
        p = self.variant.product
        p.returnable = False
        p.save(update_fields=["returnable"])
        oid = self._create_order(qty=1)
        self._deliver(oid)
        line = self._line(oid)
        res = self._create_return(oid, line, qty=1)
        self.assertEqual(res.status_code, 400)

    def test_receive_restocks_inventory(self):
        self._auth()
        oid = self._create_order(qty=2)
        self._deliver(oid)
        line = self._line(oid)
        before = InventoryItem.objects.get(
            variant=self.variant, warehouse=self.local_wh).qty_on_hand

        res = self._create_return(oid, line, qty=1)
        ret = res.json()
        rl = ret["lines"][0]
        res = self.client.post(
            f"/api/v1/ops/returns/{ret['id']}/receive",
            data={
                "lines": [{"return_line_id": rl["id"], "qty": 1}],
                "restock_warehouse_id": self.local_wh.id,
            },
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        after = InventoryItem.objects.get(
            variant=self.variant, warehouse=self.local_wh).qty_on_hand
        self.assertEqual(after, before + 1)

        # receiving more than requested is rejected
        res = self.client.post(
            f"/api/v1/ops/returns/{ret['id']}/receive",
            data={"lines": [{"return_line_id": rl["id"], "qty": 1}]},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 400)

    def test_return_reject_and_cancel(self):
        self._auth()
        oid = self._create_order(qty=1)
        self._deliver(oid)
        line = self._line(oid)

        res = self._create_return(oid, line, qty=1, approve=False)
        rid = res.json()["id"]
        res = self.client.post(
            f"/api/v1/ops/returns/{rid}/status/rejected",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        # rejected is terminal
        res = self.client.post(
            f"/api/v1/ops/returns/{rid}/status/approved",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 400)
        # rejected returns free up the qty for a new request
        res = self._create_return(oid, line, qty=1)
        self.assertEqual(res.status_code, 200)


class OpsCustomersAndDocumentsTests(OpsLineEditDiscountTests):
    """Customer detail regression + invoices registry endpoint."""

    def test_customer_detail_returns_stats(self):
        self._auth()
        self._create_order(qty=1)
        res = self.client.get(
            f"/api/v1/ops/customers/{self.customer.id}",
            HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        j = res.json()
        self.assertEqual(j["email"], self.customer.email)
        self.assertEqual(j["orders_count"], 1)
        self.assertEqual(len(j["orders"]), 1)

    def test_invoices_registry_filters(self):
        self._auth()
        oid = self._create_order(qty=1)
        self.client.post(
            f"/api/v1/ops/orders/{oid}/invoice",
            data={}, content_type="application/json", HTTP_HOST="ops.local")

        res = self.client.get(
            "/api/v1/ops/invoices/registry", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        j = res.json()
        self.assertEqual(j["total_count"], 1)
        inv = j["results"][0]
        self.assertEqual(inv["kind"], "sales")
        self.assertEqual(inv["order_id"], oid)
        self.assertEqual(inv["buyer_email"], self.customer.email)

        # kind filter excludes sales
        res = self.client.get(
            "/api/v1/ops/invoices/registry", {"kind": "credit"},
            HTTP_HOST="ops.local")
        self.assertEqual(res.json()["total_count"], 0)

        # q matches invoice number / buyer email
        res = self.client.get(
            "/api/v1/ops/invoices/registry", {"q": inv["number"]},
            HTTP_HOST="ops.local")
        self.assertEqual(res.json()["total_count"], 1)
        res = self.client.get(
            "/api/v1/ops/invoices/registry", {"q": "nomatch"},
            HTTP_HOST="ops.local")
        self.assertEqual(res.json()["total_count"], 0)

    def test_products_and_inventory_have_thumbnails(self):
        self._auth()
        res = self.client.get(
            "/api/v1/ops/catalog/products", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.assertIn("thumbnail_url", res.json()["results"][0])
        res = self.client.get(
            "/api/v1/ops/inventory/items", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        item = res.json()["results"][0]
        self.assertIn("thumbnail_url", item)
        self.assertIn("product_id", item)


class OpsInvoiceExportTests(OpsLineEditDiscountTests):
    """Bulk invoice export: PDF zip + CSV."""

    def _issue_invoice(self, oid):
        res = self.client.post(
            f"/api/v1/ops/orders/{oid}/invoice",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()

    def test_csv_export(self):
        self._auth()
        oid = self._create_order(qty=1)
        inv = self._issue_invoice(oid)

        res = self.client.post(
            "/api/v1/ops/invoices/export",
            data={"format": "csv", "invoice_ids": [inv["id"]]},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/csv", res["Content-Type"])
        body = b"".join(res.streaming_content).decode("utf-8-sig")
        rows = [r for r in body.strip().split("\r\n")]
        self.assertEqual(len(rows), 2)
        self.assertIn("number;kind;status", rows[0])
        self.assertIn(inv["number"], rows[1])
        self.assertIn(self.customer.email, rows[1])

    def test_zip_export_and_limits(self):
        self._auth()
        oid = self._create_order(qty=1)
        inv = self._issue_invoice(oid)

        res = self.client.post(
            "/api/v1/ops/invoices/export",
            data={"format": "pdf_zip", "invoice_ids": [inv["id"]]},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/zip")
        import io, zipfile
        zf = zipfile.ZipFile(io.BytesIO(b"".join(res.streaming_content)))
        self.assertEqual(zf.namelist(), [f"{inv['number']}.pdf"])
        self.assertTrue(zf.read(zf.namelist()[0]).startswith(b"%PDF"))

        # no documents -> 404; bad format -> 400
        res = self.client.post(
            "/api/v1/ops/invoices/export",
            data={"format": "csv", "invoice_ids": [99999]},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 404)
        res = self.client.post(
            "/api/v1/ops/invoices/export",
            data={"format": "xlsx", "invoice_ids": [inv["id"]]},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 400)

        # filter-based export (no ids) uses registry filters
        res = self.client.post(
            "/api/v1/ops/invoices/export",
            data={"format": "csv", "kind": "credit"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 404)
        res = self.client.post(
            "/api/v1/ops/invoices/export",
            data={"format": "csv", "kind": "sales"},
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200)


class OpsRefundFlowTests(OpsLineEditDiscountTests):
    """Montonio refunds: credit -> pending refund -> submit -> webhook."""

    def _paid_order(self):
        oid = self._create_order(qty=2)
        order = Order.objects.get(id=oid)
        order.status = Order.Status.PAID
        order.save(update_fields=["status"])
        PaymentIntent.objects.create(
            order=order,
            provider=PaymentIntent.Provider.MONTONIO,
            status=PaymentIntent.Status.SUCCEEDED,
            amount_gross=order.total_gross,
            currency="EUR",
            external_id="order-uuid-1",
        )
        return oid, order

    def _credit_order(self, oid):
        inv = self.client.post(
            f"/api/v1/ops/orders/{oid}/invoice",
            data={}, content_type="application/json", HTTP_HOST="ops.local"
        ).json()
        res = self.client.post(
            f"/api/v1/ops/invoices/{inv['id']}/credit",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()

    def test_credit_creates_pending_refund(self):
        from checkout.models import Surcharge
        from checkout.surcharges import balance_due
        self._auth()
        oid, order = self._paid_order()
        credit = self._credit_order(oid)

        order.refresh_from_db()
        self.assertLess(balance_due(order=order), 0)
        s = order.surcharges.get(
            direction=Surcharge.Direction.REFUND,
            status=Surcharge.Status.PENDING)
        self.assertEqual(
            str(s.amount_gross), credit["total_gross"].lstrip("-"))
        self.assertEqual(s.source, Surcharge.Source.AUTO)
        self.assertFalse(s.external_id)

    def test_refund_submit_and_cancel_guard(self):
        from unittest.mock import patch
        from checkout.models import Surcharge
        self._auth()
        oid, order = self._paid_order()
        self._credit_order(oid)
        s = order.surcharges.get(direction=Surcharge.Direction.REFUND)

        with patch(
            "payments.montonio.create_refund",
            return_value={"uuid": "refund-uuid-1", "status": "PENDING"},
        ):
            res = self.client.post(
                f"/api/v1/ops/surcharges/{s.id}/refund",
                data={}, content_type="application/json",
                HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content[:300])
        out = res.json()
        self.assertEqual(out["status"], "pending")
        self.assertTrue(out["refund_in_flight"])
        s.refresh_from_db()
        self.assertEqual(s.external_id, "refund-uuid-1")
        self.assertTrue(
            s.raw_response.get("idempotency_key"))

        # in-flight rows cannot be cancelled or resubmitted
        res = self.client.post(
            f"/api/v1/ops/surcharges/{s.id}/cancel",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 400)
        res = self.client.post(
            f"/api/v1/ops/surcharges/{s.id}/refund",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 400)

    def test_refund_requires_montonio_payment(self):
        from checkout.models import Surcharge
        self._auth()
        oid, order = self._paid_order()
        # a bank-transfer paid order has no Montonio intent
        pi = order.payment_intent
        pi.provider = PaymentIntent.Provider.BANK_TRANSFER
        pi.save(update_fields=["provider"])
        self._credit_order(oid)
        s = order.surcharges.get(direction=Surcharge.Direction.REFUND)
        res = self.client.post(
            f"/api/v1/ops/surcharges/{s.id}/refund",
            data={}, content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 400)

    def test_second_credit_while_inflight(self):
        from decimal import Decimal
        from unittest.mock import patch
        from checkout.models import Surcharge
        from checkout.surcharges import reconcile_order_balance
        self._auth()
        oid, order = self._paid_order()
        self._credit_order(oid)
        s1 = order.surcharges.get(direction=Surcharge.Direction.REFUND)
        with patch(
            "payments.montonio.create_refund",
            return_value={"uuid": "refund-uuid-1"},
        ):
            self.client.post(
                f"/api/v1/ops/surcharges/{s1.id}/refund",
                data={}, content_type="application/json",
                HTTP_HOST="ops.local")
        s1.refresh_from_db()

        # in-flight row keeps its amount; a new obligation creates a
        # separate pending row
        original_amount = s1.amount_gross
        order.items_gross = "1.00"
        order.total_gross = "10.00"
        order.save(update_fields=["items_gross", "total_gross"])
        reconcile_order_balance(order=order, reason="test")
        s1.refresh_from_db()
        self.assertEqual(s1.amount_gross, original_amount)
        extra = order.surcharges.get(
            direction=Surcharge.Direction.REFUND,
            status=Surcharge.Status.PENDING,
            external_id="")
        self.assertEqual(extra.amount_gross, Decimal("16.62"))
