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


def _ensure_default_variant(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Variant = apps.get_model("catalog", "Variant")

    for p in Product.objects.all().iterator():
        if Variant.objects.filter(product_id=p.id).exists():
            continue
        Variant.objects.create(
            product_id=p.id,
            sku=p.sku,
            barcode="",
            name="",
            price_eur=int(getattr(p, "price_eur", 0) or 0),
            is_active=p.is_active,
        )


class Migration(migrations.Migration):

    dependencies = [
        (
            "catalog",
            "0011_variant_height_cm_variant_length_cm_variant_weight_g_and_more",
        ),
    ]

    operations = [
        migrations.RunPython(_ensure_default_variant, migrations.RunPython.noop),
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
