from django.db import migrations


def _backfill_inventory(apps, schema_editor):
    Warehouse = apps.get_model("catalog", "Warehouse")
    Variant = apps.get_model("catalog", "Variant")
    InventoryItem = apps.get_model("catalog", "InventoryItem")

    warehouse = Warehouse.objects.order_by("sort_order", "id").first()
    if warehouse is None:
        warehouse = Warehouse.objects.create(
            code="default",
            name="Default",
            country_code="LT",
            city="",
            dispatch_days_min=0,
            dispatch_days_max=0,
            is_active=True,
            sort_order=0,
        )

    existing = set(
        InventoryItem.objects.filter(warehouse_id=warehouse.id).values_list(
            "variant_id", flat=True
        )
    )

    to_create = []
    for v in Variant.objects.all().iterator():
        if v.id in existing:
            continue
        qty = int(getattr(v, "stock_qty", 0) or 0)
        if qty < 0:
            qty = 0
        to_create.append(
            InventoryItem(
                variant_id=v.id,
                warehouse_id=warehouse.id,
                qty_on_hand=qty,
                qty_reserved=0,
            )
        )

    if to_create:
        InventoryItem.objects.bulk_create(to_create, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        (
            "catalog",
            "0011_variant_height_cm_variant_length_cm_variant_weight_g_and_more",
        ),
    ]

    operations = [
        migrations.RunPython(_backfill_inventory, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="product",
            name="stock_qty",
        ),
        migrations.RemoveField(
            model_name="variant",
            name="stock_qty",
        ),
    ]
