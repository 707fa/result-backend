from django.contrib import admin
from .models import PaymentTransaction, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "phone", "role", "points", "group", "is_paid", "paid_until", "is_active")
    search_fields = ("full_name", "phone")
    list_filter = ("role", "is_paid", "is_active")


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "provider", "amount", "status", "created_at", "paid_at")
    search_fields = ("id", "external_id", "user__full_name", "user__phone")
    list_filter = ("provider", "status")
