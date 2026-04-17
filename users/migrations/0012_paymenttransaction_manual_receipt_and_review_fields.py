from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0011_homeworktask_speaking_level_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymenttransaction",
            name="manual_detected_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="manual_receipt",
            field=models.FileField(blank=True, null=True, upload_to="payment_receipts/"),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="manual_receipt_uploaded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="manual_verdict",
            field=models.CharField(default="pending", max_length=24),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="manual_verdict_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="reviewed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reviewed_payment_transactions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="telegram_chat_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="telegram_message_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]

