import os

from django.contrib.auth.hashers import make_password
from django.db import migrations


DEV_PHONE = "+998978778177"
TEACHER_PHONE = "+998909788255"


def seed_admin_teacher_accounts(apps, schema_editor):
    pass


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("groups", "0003_alter_group_id"),
        ("users", "0017_make_teachers_staff"),
    ]

    operations = [
        migrations.RunPython(seed_admin_teacher_accounts, noop_reverse),
    ]
