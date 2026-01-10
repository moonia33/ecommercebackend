from django.db import migrations


def seed_catalog_back_in_stock_template(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")

    EmailTemplate.objects.update_or_create(
        key="catalog_back_in_stock",
        defaults={
            "name": "Catalog back in stock",
            "subject": "{{ product_name }} vėl sandėlyje",
            "body_text": (
                "Prekė vėl sandėlyje: {{ product_name }}\n"
                "SKU: {{ product_sku }}\n"
                "{{ product_slug }}\n"
            ),
            "body_html": "",
            "is_active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0002_seed_auth_otp_code"),
    ]

    operations = [
        migrations.RunPython(
            seed_catalog_back_in_stock_template,
            migrations.RunPython.noop,
        ),
    ]
