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
        self.assertEqual(len(res.json()), 1)
        self.assertEqual(res.json()[0]["id"], sid)

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
