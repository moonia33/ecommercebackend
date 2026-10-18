from django.db import migrations

TEMPLATES = {
    "lt": {
        "subject": "Grąžinimas {{ return_number }} patvirtintas (užsakymas #{{ order_id }})",
        "body_text": (
            "Sveiki,\n\n"
            "Jūsų grąžinimo prašymas {{ return_number }} užsakymui "
            "#{{ order_id }} patvirtintas.\n\n"
            "Grąžinamos prekės:\n{{ lines }}\n\n"
            "{% if tracking_number %}Grąžinimo siuntos sekimo numeris: "
            "{{ tracking_number }}\n\n{% endif %}"
            "Prekes supakuokite taip, kad jos nebūtų pažeistos "
            "transportuojant, ir išsiųskite per 14 dienų, jei nenurodyta "
            "kitaip. Gavę prekes patikrinsime jų būklę ir grąžinsime "
            "mokėjimą.\n\n"
            "Pagarbiai,\n{{ site_name }}"
        ),
    },
    "en": {
        "subject": "Return {{ return_number }} approved (order #{{ order_id }})",
        "body_text": (
            "Hello,\n\n"
            "Your return request {{ return_number }} for order "
            "#{{ order_id }} has been approved.\n\n"
            "Items to return:\n{{ lines }}\n\n"
            "{% if tracking_number %}Return shipment tracking number: "
            "{{ tracking_number }}\n\n{% endif %}"
            "Please pack the items so they are not damaged in transit and "
            "send them within 14 days unless instructed otherwise. Once "
            "received and inspected, we will issue your refund.\n\n"
            "Best regards,\n{{ site_name }}"
        ),
    },
    "pl": {
        "subject": "Zwrot {{ return_number }} zatwierdzony (zamówienie #{{ order_id }})",
        "body_text": (
            "Dzień dobry,\n\n"
            "Twoje zgłoszenie zwrotu {{ return_number }} do zamówienia "
            "#{{ order_id }} zostało zatwierdzone.\n\n"
            "Produkty do zwrotu:\n{{ lines }}\n\n"
            "{% if tracking_number %}Numer śledzenia przesyłki zwrotnej: "
            "{{ tracking_number }}\n\n{% endif %}"
            "Spakuj produkty tak, aby nie uległy uszkodzeniu w transporcie, "
            "i nadaj je w ciągu 14 dni, o ile nie wskazano inaczej. Po "
            "otrzymaniu i sprawdzeniu towaru zwrócimy płatność.\n\n"
            "Pozdrawiamy,\n{{ site_name }}"
        ),
    },
}


def seed(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")
    for lang, tpl in TEMPLATES.items():
        EmailTemplate.objects.update_or_create(
            key="return_instructions",
            language_code=lang,
            defaults={
                "name": "Return (RMA) instructions after approval",
                "subject": tpl["subject"],
                "body_text": tpl["body_text"],
                "body_html": "",
                "is_active": True,
            },
        )


def unseed(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")
    EmailTemplate.objects.filter(key="return_instructions").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("checkout", "0033_returnrequest_returnline_and_more"),
        ("notifications", "0005_outboundemail_attachments"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
