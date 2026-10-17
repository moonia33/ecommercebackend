from django.db import migrations


def forwards(apps, schema_editor):
    Order = apps.get_model("checkout", "Order")
    Shipment = apps.get_model("checkout", "Shipment")

    Order.objects.filter(fulfillment_mode="dropship").update(fulfillment_mode="remote")

    for o in Order.objects.exclude(tracking_number=""):
        if not Shipment.objects.filter(order_id=o.id, kind="outbound").exists():
            Shipment.objects.create(
                order_id=o.id,
                kind="outbound",
                status=o.delivery_status or "pending",
                carrier_code=o.carrier_code or "",
                shipping_method=o.shipping_method or "",
                tracking_number=o.tracking_number or "",
                carrier_shipment_id=o.carrier_shipment_id or "",
                pickup_locker_id=o.pickup_locker_id,
                pickup_point_id=o.pickup_point_id or "",
                pickup_point_name=o.pickup_point_name or "",
                shipping_label_pdf=o.shipping_label_pdf.name or None,
                label_generated_at=o.shipping_label_generated_at,
            )


def backwards(apps, schema_editor):
    Order = apps.get_model("checkout", "Order")
    Shipment = apps.get_model("checkout", "Shipment")
    Order.objects.filter(fulfillment_mode="remote").update(fulfillment_mode="dropship")
    Shipment.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("checkout", "0028_alter_order_fulfillment_mode_shipment_surcharge"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
