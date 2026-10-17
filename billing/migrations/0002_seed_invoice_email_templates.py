from django.db import migrations

TEMPLATES = {
    "lt": {
        "subject": "Sąskaita faktūra {{ invoice_number }}",
        "body_text": (
            "Sveiki,\n\n"
            "Jūsų užsakymas #{{ order_id }} pristatytas. "
            "Prie laiško pridedame sąskaitą faktūrą {{ invoice_number }} "
            "({{ issue_date }}), suma {{ total_gross }} {{ currency }}.\n\n"
            "Pagarbiai,\n{{ site_name }}"
        ),
    },
    "en": {
        "subject": "Invoice {{ invoice_number }}",
        "body_text": (
            "Hello,\n\n"
            "Your order #{{ order_id }} has been delivered. "
            "Invoice {{ invoice_number }} ({{ issue_date }}), "
            "total {{ total_gross }} {{ currency }}, is attached.\n\n"
            "Best regards,\n{{ site_name }}"
        ),
    },
    "pl": {
        "subject": "Faktura {{ invoice_number }}",
        "body_text": (
            "Dzień dobry,\n\n"
            "Twoje zamówienie #{{ order_id }} zostało dostarczone. "
            "W załączeniu faktura {{ invoice_number }} ({{ issue_date }}), "
            "kwota {{ total_gross }} {{ currency }}.\n\n"
            "Pozdrawiamy,\n{{ site_name }}"
        ),
    },
}


def seed(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")
    for lang, tpl in TEMPLATES.items():
        EmailTemplate.objects.update_or_create(
            key="invoice_issued",
            language_code=lang,
            defaults={
                "name": "Invoice issued (with PDF attachment)",
                "subject": tpl["subject"],
                "body_text": tpl["body_text"],
                "body_html": "",
                "is_active": True,
            },
        )


def unseed(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")
    EmailTemplate.objects.filter(key="invoice_issued").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_initial"),
        ("notifications", "0005_outboundemail_attachments"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
