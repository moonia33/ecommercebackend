from django.db import migrations


def seed_template(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")
    EmailTemplate.objects.get_or_create(
        key="gift_card_issued",
        language_code="lt",
        defaults={
            "name": "Dovanų kortelė (pirkimas)",
            "subject": "Jūsų dovanų kortelė — {{ card_code }}",
            "body_text": (
                "Sveiki,\n\n"
                "ačiū už pirkimą! Jūsų dovanų kortelė paruošta:\n\n"
                "Kodas: {{ card_code }}\n"
                "Nominalas: {{ amount }}\n"
                "Galioja iki: {{ expires_at }}\n\n"
                "Kodą įveskite atsiskaitymo metu — kortelės likutis "
                "padengs visą arba dalį užsakymo sumos. Likutį visada "
                "matysite paskyroje (Dovanų kortelės).\n\n"
                "Apsipirkti: {{ shop_url }}\n\n"
                "{{ site_name }}"
            ),
            "body_html": (
                "<p>Sveiki,</p>"
                "<p>ačiū už pirkimą! Jūsų dovanų kortelė paruošta:</p>"
                "<p style=\"font-size:18px\">"
                "<strong>Kodas: {{ card_code }}</strong><br>"
                "Nominalas: <strong>{{ amount }}</strong><br>"
                "Galioja iki: {{ expires_at }}</p>"
                "<p>Kodą įveskite atsiskaitymo metu — kortelės likutis "
                "padengs visą arba dalį užsakymo sumos. Likutį visada "
                "matysite paskyroje (Dovanų kortelės).</p>"
                "<p><a href=\"{{ shop_url }}\">Apsipirkti →</a></p>"
                "<p>{{ site_name }}</p>"
            ),
            "is_active": True,
        },
    )


def unseed_template(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")
    EmailTemplate.objects.filter(
        key="gift_card_issued", language_code="lt").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("giftcards", "0002_giftcardtransaction_reverses"),
        ("notifications", "0006_newslettersubscriber"),
    ]

    operations = [
        migrations.RunPython(seed_template, unseed_template),
    ]
