from django.db import migrations, models

TEMPLATES = {
    "lt": {
        "subject": "Užsakymas #{{ order_id }} apmokėtas",
        "body_text": (
            "Sveiki,\n\n"
            "Gavome Jūsų apmokėjimą už užsakymą #{{ order_id }} "
            "({{ total_gross }} {{ currency }}). "
            "{% if is_digital %}"
            "Dovanų kortelės kodas išsiųstas atskiru laišku "
            "{% if gift_recipient_email %}gavėjui {{ gift_recipient_email }}"
            "{% else %}šiuo el. paštu{% endif %}."
            "{% else %}"
            "Užsakymas pradėtas ruošti — apie išsiuntimą ir sąskaitą "
            "informuosime atskirai."
            "{% endif %}\n\n"
            "Užsakymą galite peržiūrėti: {{ order_url }}\n\n"
            "Pagarbiai,\n{{ site_name }}"
        ),
    },
    "en": {
        "subject": "Order #{{ order_id }} paid",
        "body_text": (
            "Hello,\n\n"
            "We have received your payment for order #{{ order_id }} "
            "({{ total_gross }} {{ currency }}). "
            "{% if is_digital %}"
            "The gift card code has been sent in a separate email "
            "{% if gift_recipient_email %}to {{ gift_recipient_email }}"
            "{% else %}to this address{% endif %}."
            "{% else %}"
            "Your order is now being prepared — we will let you know "
            "when it ships and send the invoice."
            "{% endif %}\n\n"
            "View your order: {{ order_url }}\n\n"
            "Best regards,\n{{ site_name }}"
        ),
    },
    "pl": {
        "subject": "Zamówienie #{{ order_id }} opłacone",
        "body_text": (
            "Dzień dobry,\n\n"
            "Otrzymaliśmy płatność za zamówienie #{{ order_id }} "
            "({{ total_gross }} {{ currency }}). "
            "{% if is_digital %}"
            "Kod karty podarunkowej został wysłany osobnym e-mailem."
            "{% else %}"
            "Zamówienie jest przygotowywane — o wysyłce i fakturze "
            "poinformujemy osobno."
            "{% endif %}\n\n"
            "Zamówienie: {{ order_url }}\n\n"
            "Pozdrawiamy,\n{{ site_name }}"
        ),
    },
}


def seed(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")
    for lang, tpl in TEMPLATES.items():
        EmailTemplate.objects.update_or_create(
            key="order_paid",
            language_code=lang,
            defaults={
                "name": "Order paid confirmation",
                "subject": tpl["subject"],
                "body_text": tpl["body_text"],
                "body_html": "",
                "is_active": True,
            },
        )


def unseed(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")
    EmailTemplate.objects.filter(key="order_paid").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("checkout", "0039_order_gift_recipient_email"),
        ("notifications", "0006_newslettersubscriber"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="paid_confirmation_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(seed, unseed),
    ]
