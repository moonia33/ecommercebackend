from django.test import TestCase

from checkout.models import Order
from checkout.statuses import get_status_label, get_status_options
from dpd.status_mapping import extract_latest_event, map_dpd_status

DS = Order.DeliveryStatus


class DpdStatusMappingTests(TestCase):
    """statusCode + serviceCode → unified Order.DeliveryStatus."""

    def test_terminal_processing_is_in_transit(self):
        for code in ("01", "02", "09", "10", "20"):
            self.assertEqual(map_dpd_status(status_code=code), DS.IN_TRANSIT, code)

    def test_parcel_accepted_is_picked_up(self):
        for code in ("05", "15", "DOPKY"):
            self.assertEqual(map_dpd_status(status_code=code), DS.PICKED_UP, code)

    def test_courier_scans_are_in_transit(self):
        for code in ("03", "14"):
            self.assertEqual(map_dpd_status(status_code=code), DS.IN_TRANSIT, code)

    def test_delivered_to_consignee(self):
        self.assertEqual(map_dpd_status(status_code="13"), DS.DELIVERED)
        self.assertEqual(
            map_dpd_status(status_code="DEYY", prev_status_code="13"),
            DS.DELIVERED,
        )

    def test_status_13_with_return_service_is_returned(self):
        for sc in ("298", "299", "300", "301", "332"):
            self.assertEqual(
                map_dpd_status(status_code="13", service_codes=[sc]),
                DS.RETURNED,
                sc,
            )

    def test_return_service_codes_mark_returning(self):
        for code in ("03", "06", "09"):
            self.assertEqual(
                map_dpd_status(status_code=code, service_codes=["300"]),
                DS.RETURNING,
                code,
            )

    def test_locker_delivery_and_pickup(self):
        self.assertEqual(map_dpd_status(status_code="23"), DS.AT_PICKUP_POINT)
        self.assertEqual(map_dpd_status(status_code="DODEI"), DS.AT_PICKUP_POINT)
        self.assertEqual(map_dpd_status(status_code="DODEY"), DS.DELIVERED)

    def test_deyy_after_failed_delivery_stays_in_transit(self):
        # 04 = delivery failed → parcel returns to terminal for redelivery,
        # not (yet) a return-to-sender flow.
        self.assertEqual(
            map_dpd_status(status_code="DEYY", prev_status_code="04"),
            DS.IN_TRANSIT,
        )

    def test_failed_delivery_keeps_in_transit(self):
        self.assertEqual(map_dpd_status(status_code="04"), DS.IN_TRANSIT)

    def test_stopped_in_terminal_is_error(self):
        self.assertEqual(map_dpd_status(status_code="08"), DS.ERROR)

    def test_unknown_codes_return_none(self):
        self.assertIsNone(map_dpd_status(status_code=""))
        self.assertIsNone(map_dpd_status(status_code=None))
        self.assertIsNone(map_dpd_status(status_code="XX"))

    def test_extract_latest_event_shapes(self):
        raw = [
            {
                "statusCode": "13",
                "serviceCode": 300,
                "prevStatusCode": "03",
            }
        ]
        event = extract_latest_event(raw)
        self.assertEqual(event["status_code"], "13")
        self.assertEqual(event["service_codes"], ["300"])
        self.assertEqual(event["prev_status_code"], "03")
        # dict payload with "statuses" list also works
        event2 = extract_latest_event({"statuses": [{"statusCode": "23"}]})
        self.assertEqual(event2["status_code"], "23")


class StatusLabelTests(TestCase):
    def test_lt_labels_match_business_wording(self):
        expected = {
            "awaiting_stock": "Laukiama prekės iš nuotolinio sandėlio",
            "packing": "Perduota pakavimui",
            "packed": "Supakuota, laukiama paėmimo",
            "picked_up": "Siunta paimta",
            "in_transit": "Siunta kelyje",
            "at_pickup_point": "Pristatyta į paštomatą",
            "delivered": "Pristatyta",
            "delivered_pickup": "Atsiimta iš paštomato",
            "returning": "Grąžinama siuntėjui",
            "returned": "Siunta grąžinta siuntėjui",
        }
        for code, label in expected.items():
            self.assertEqual(get_status_label("delivery_status", code, "lt"), label, code)

    def test_labels_exist_in_all_languages(self):
        codes = [o["code"] for o in get_status_options("delivery_status", "en")]
        self.assertNotIn("delivered_pickup", codes)  # display-only variant
        for code in codes:
            for lang in ("lt", "en", "pl"):
                label = get_status_label("delivery_status", code, lang)
                self.assertTrue(label, f"{code}/{lang}")
                self.assertNotEqual(label, code, f"{code}/{lang} untranslated")

    def test_options_include_all_unified_codes(self):
        codes = {o["code"] for o in get_status_options("delivery_status", "en")}
        self.assertEqual(
            codes,
            {
                "pending", "awaiting_stock", "packing", "packed", "picked_up",
                "in_transit", "at_pickup_point", "delivered", "returning",
                "returned", "cancelled", "error",
            },
        )
