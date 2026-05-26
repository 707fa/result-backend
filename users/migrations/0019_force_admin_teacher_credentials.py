import os

from django.contrib.auth.hashers import make_password
from django.db import migrations


DEV_PHONE = "+998978778177"
DEV_PHONE_VARIANTS = ["+998978778177", "998978778177", "978778177"]
TEACHER_PHONE = "+998909788255"
TEACHER_PHONE_VARIANTS = ["+998909788255", "998909788255", "909788255"]


def _upsert_user(User, variants, canonical_phone, defaults):
    users = list(User.objects.filter(phone__in=variants).order_by("id"))
    if not users:
        user = User(phone=canonical_phone, username=canonical_phone)
        for field, value in defaults.items():
            setattr(user, field, value)
        user.save()
        return user

    for user in users:
        for field, value in defaults.items():
            setattr(user, field, value)
        if not user.username:
            user.username = user.phone
        user.save()
    return users[0]


def force_admin_teacher_credentials(apps, schema_editor):
    pass


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0018_seed_admin_teacher_accounts"),
    ]

    operations = [
        migrations.RunPython(force_admin_teacher_credentials, noop_reverse),
    ]
