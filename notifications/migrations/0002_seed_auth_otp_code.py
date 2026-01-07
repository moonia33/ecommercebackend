from django.db import migrations


def seed_auth_otp_template(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")

    EmailTemplate.objects.update_or_create(
        key="auth_otp_code",
        defaults={
            "name": "Auth OTP code",
            "subject": "Prisijungimo kodas",
            "body_text": (
                "Jūsų prisijungimo kodas: {{ code }}\n"
                "Galioja {{ ttl_minutes }} min.\n"
            ),
            "body_html": "",
            "is_active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_auth_otp_template,
                             migrations.RunPython.noop),
    ]
