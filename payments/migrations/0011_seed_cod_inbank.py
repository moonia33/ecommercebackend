# Seed COD + Inbank payment methods and a store-pickup shipping method.
#
# COD semantics: payable on delivery/pickup — the intent stays pending
# until the manager confirms collection via ops mark-paid. It is scoped
# to Lithuania and to shipping methods that do not require a parcel
# terminal (courier + store pickup).

from django.db import migrations


def seed(apps, schema_editor):
    PaymentMethod = apps.get_model("payments", "PaymentMethod")
    ShippingMethod = apps.get_model("shipping", "ShippingMethod")
    ShippingRate = apps.get_model("shipping", "ShippingRate")

    pickup, created = ShippingMethod.objects.get_or_create(
        code="store_pickup",
        defaults={
            "name": "Atsiėmimas parduotuvėje",
            "carrier_code": "",
            "requires_pickup_point": False,
            "is_active": True,
            "sort_order": 90,
        },
    )
    ShippingRate.objects.get_or_create(
        method=pickup, country_code="LT",
        defaults={"net_eur": "0.00", "is_active": True},
    )

    cod, created = PaymentMethod.objects.get_or_create(
        code="cod",
        defaults={
            "name": "Mokėjimas atsiimant",
            "kind": "cod",
            "provider": "cod",
            "country_code": "LT",
            "is_active": True,
            "sort_order": 40,
            "instructions": (
                "Sumokėsite atsiimdami prekes grynaisiais arba kortele."),
        },
    )
    # COD is offered only for non-terminal delivery: LT courier and
    # store pickup (locker handover cannot take payment).
    allowed = ShippingMethod.objects.filter(
        is_active=True, requires_pickup_point=False)
    cod.allowed_shipping_methods.set(allowed)

    PaymentMethod.objects.get_or_create(
        code="bank_transfer",
        defaults={
            "name": "Paprastas pavedimas",
            "kind": "offline",
            "provider": "bank_transfer",
            "is_active": True,
            "sort_order": 50,
        },
    )

    PaymentMethod.objects.get_or_create(
        code="inbank",
        defaults={
            "name": "Inbank — mokėjimas dalimis",
            "kind": "gateway",
            "provider": "inbank",
            "country_code": "LT",
            "is_active": True,
            "sort_order": 30,
            "instructions": "",
        },
    )


def unseed(apps, schema_editor):
    PaymentMethod = apps.get_model("payments", "PaymentMethod")
    ShippingMethod = apps.get_model("shipping", "ShippingMethod")
    PaymentMethod.objects.filter(
        code__in={"cod", "inbank", "bank_transfer"}).delete()
    ShippingMethod.objects.filter(code="store_pickup").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0010_paymentmethod_allowed_shipping_methods"),
        ("shipping", "0009_shopshippingmethodpolicy"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
