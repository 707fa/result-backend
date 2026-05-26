from django.db import migrations


def cleanup_seed_accounts(apps, schema_editor):
    User = apps.get_model("users", "User")
    DEMO_PHONES = [
        "+998999999999",
        "+998990000001",
        "+998990000011",
        "+998990000012",
        "+998990000013",
    ]
    User.objects.filter(phone__in=DEMO_PHONES).delete()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0019_force_admin_teacher_credentials"),
    ]

    operations = [
        migrations.RunPython(cleanup_seed_accounts, noop_reverse),
    ]
