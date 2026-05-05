from django.db import migrations


def make_teachers_staff(apps, schema_editor):
    User = apps.get_model("users", "User")
    User.objects.filter(role="teacher").update(is_staff=True, is_active=True)


def reverse_make_teachers_staff(apps, schema_editor):
    User = apps.get_model("users", "User")
    User.objects.filter(role="teacher", is_superuser=False).update(is_staff=False)


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0016_seed_mock_student_account"),
    ]

    operations = [
        migrations.RunPython(make_teachers_staff, reverse_make_teachers_staff),
    ]
