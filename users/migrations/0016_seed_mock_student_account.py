import os
from datetime import datetime

from django.contrib.auth.hashers import make_password
from django.db import migrations
from django.utils import timezone


STUDENT_PHONE = "+998999999999"
TEACHER_PHONE = "+998900000001"


def seed_student_account(apps, schema_editor):
    pass


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("groups", "0003_alter_group_id"),
        ("users", "0015_supportticketmessage"),
    ]

    operations = [
        migrations.RunPython(seed_student_account, noop_reverse),
    ]
