from django.contrib.auth.hashers import make_password
from django.db import migrations


ACCOUNTS = [
    {
        "phone": "+998978778177",
        "username": "+998978778177",
        "full_name": "Alex",
        "role": "admin",
        "is_staff": True,
        "is_superuser": True,
        "is_active": True,
        "password": "Alex2024",
    },
    {
        "phone": "+998909788255",
        "username": "+998909788255",
        "full_name": "Teacher",
        "role": "teacher",
        "is_staff": True,
        "is_superuser": False,
        "is_active": True,
        "password": "909788255@@",
    },
]


def ensure_accounts(apps, schema_editor):
    User = apps.get_model("users", "User")
    for account in ACCOUNTS:
        defaults = dict(account)
        raw_password = defaults.pop("password")
        hashed = make_password(raw_password)
        defaults["password"] = hashed
        User.objects.update_or_create(
            phone=account["phone"],
            defaults=defaults,
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0020_cleanup_seed_accounts"),
    ]

    operations = [
        migrations.RunPython(ensure_accounts, noop_reverse),
    ]
