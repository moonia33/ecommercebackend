# Backfill InvoiceLine.order_line for sales invoices issued before the
# FK existed. Product lines are matched to the order's lines by name
# (with qty as a tie-breaker); shipping/discount lines legitimately stay
# NULL, as do ambiguous matches.

from django.db import migrations


def backfill_order_line(apps, schema_editor):
    InvoiceLine = apps.get_model("billing", "InvoiceLine")
    OrderLine = apps.get_model("checkout", "OrderLine")

    qs = (
        InvoiceLine.objects.filter(
            invoice__kind="sales", order_line__isnull=True)
        .select_related("invoice")
        .iterator()
    )
    to_update = []
    for il in qs:
        cands = OrderLine.objects.filter(
            order_id=il.invoice.order_id, name=il.name)
        ids = list(cands.values_list("id", flat=True))
        if len(ids) > 1:
            ids = list(cands.filter(qty=il.qty).values_list("id", flat=True))
        if len(ids) == 1:
            il.order_line_id = ids[0]
            to_update.append(il)
    if to_update:
        InvoiceLine.objects.bulk_update(to_update, ["order_line"])


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0005_invoiceline_order_line"),
    ]

    operations = [
        migrations.RunPython(backfill_order_line, migrations.RunPython.noop),
    ]
