import os

from django.contrib.auth.hashers import make_password
from django.db import migrations


DEV_PHONE = "+998978778177"
TEACHER_PHONE = "+998909788255"


def seed_admin_teacher_accounts(apps, schema_editor):
    developer_password = str(os.environ.get("LAUNCH_DEVELOPER_PASSWORD", "") or "").strip()
    teacher_password = str(os.environ.get("LAUNCH_TEACHER_PASSWORD", "") or "").strip()

    User = apps.get_model("users", "User")
    Group = apps.get_model("groups", "Group")

    developer = None
    teacher = None

    if developer_password:
        developer, _ = User.objects.update_or_create(
            phone=DEV_PHONE,
            defaults={
                "username": DEV_PHONE,
                "full_name": "Farrux Developer",
                "password": make_password(developer_password),
                "role": "teacher",
                "is_active": True,
                "is_staff": True,
                "is_superuser": True,
                "is_iman_student": True,
                "is_paid": True,
            },
        )

    if teacher_password:
        teacher, _ = User.objects.update_or_create(
            phone=TEACHER_PHONE,
            defaults={
                "username": TEACHER_PHONE,
                "full_name": "Iman | Bekhruz",
                "password": make_password(teacher_password),
                "role": "teacher",
                "is_active": True,
                "is_staff": True,
                "is_superuser": False,
                "is_iman_student": True,
                "is_paid": True,
            },
        )

    if developer and teacher:
        Group.objects.filter(teacher=developer).update(teacher=teacher)
    if teacher and not Group.objects.filter(teacher=teacher).exists():
        Group.objects.create(title="Beginner", time="15:30", days_pattern="mwf", teacher=teacher)


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
