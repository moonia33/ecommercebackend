from django.db import migrations

TEMPLATES = {
    "lt": {
        "subject": "Papildomas mokėjimas užsakymui #{{ order_id }}",
        "body_text": (
            "Sveiki,\n\n"
            "Jūsų užsakymui #{{ order_id }} pridėtas papildomas mokėjimas "
            "{{ amount }}. {{ reason }}\n\n"
            "{% if payment_url %}Apmokėti galite čia: {{ payment_url }}\n\n{% endif %}"
            "{% if bank_instructions %}{{ bank_instructions }}\n\n{% endif %}"
            "Pagarbiai,\n{{ site_name }}"
        ),
    },
    "en": {
        "subject": "Additional payment for order #{{ order_id }}",
        "body_text": (
            "Hello,\n\n"
            "An additional charge of {{ amount }} was added to your order "
            "#{{ order_id }}. {{ reason }}\n\n"
            "{% if payment_url %}Pay here: {{ payment_url }}\n\n{% endif %}"
            "{% if bank_instructions %}{{ bank_instructions }}\n\n{% endif %}"
            "Best regards,\n{{ site_name }}"
        ),
    },
    "pl": {
        "subject": "Dodatkowa płatność dla zamówienia #{{ order_id }}",
        "body_text": (
            "Dzień dobry,\n\n"
            "Do Twojego zamówienia #{{ order_id }} doliczono dopłatę "
            "{{ amount }}. {{ reason }}\n\n"
            "{% if payment_url %}Opłać tutaj: {{ payment_url }}\n\n{% endif %}"
            "{% if bank_instructions %}{{ bank_instructions }}\n\n{% endif %}"
            "Pozdrawiamy,\n{{ site_name }}"
        ),
    },
}


def seed(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")
    for lang, tpl in TEMPLATES.items():
        EmailTemplate.objects.update_or_create(
            key="order_surcharge",
            language_code=lang,
            defaults={
                "name": "Order surcharge / additional payment",
                "subject": tpl["subject"],
                "body_text": tpl["body_text"],
                "body_html": "",
                "is_active": True,
            },
        )


def unseed(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")
    EmailTemplate.objects.filter(key="order_surcharge").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("checkout", "0029_data_shipments_backfill"),
        ("notifications", "0005_outboundemail_attachments"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
