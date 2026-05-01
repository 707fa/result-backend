from datetime import datetime

from django.contrib.auth.hashers import make_password
from django.db import migrations
from django.utils import timezone


STUDENT_PHONE = "+998999999999"
STUDENT_PASSWORD = "123456789"
TEACHER_PHONE = "+998900000001"


def seed_student_account(apps, schema_editor):
    User = apps.get_model("users", "User")
    Group = apps.get_model("groups", "Group")

    teacher = User.objects.filter(role="teacher", is_active=True).order_by("id").first()
    if teacher is None:
        teacher, _ = User.objects.update_or_create(
            phone=TEACHER_PHONE,
            defaults={
                "username": TEACHER_PHONE,
                "full_name": "Iman Bakhruz",
                "password": make_password("Teacher2024!"),
                "role": "teacher",
                "is_active": True,
                "is_staff": True,
            },
        )

    group = (
        Group.objects.filter(title__iexact="Beginner", time="15:30", days_pattern="mwf").order_by("id").first()
        or Group.objects.filter(title__iexact="Beginner", time="15:30").order_by("id").first()
    )
    if group is None:
        group = Group.objects.create(
            title="Beginner",
            time="15:30",
            days_pattern="mwf",
            teacher=teacher,
        )

    paid_until = timezone.make_aware(datetime(2035, 1, 1, 0, 0, 0))
    User.objects.update_or_create(
        phone=STUDENT_PHONE,
        defaults={
            "username": STUDENT_PHONE,
            "full_name": "Ахроров Фаррух",
            "password": make_password(STUDENT_PASSWORD),
            "role": "student",
            "group": group,
            "is_active": True,
            "is_iman_student": True,
            "is_paid": True,
            "paid_until": paid_until,
        },
    )


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
