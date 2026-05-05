from datetime import timedelta

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils import timezone

from .models import PaymentTransaction, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("id", "full_name", "phone", "role", "points", "group", "is_paid", "paid_until", "is_active")
    search_fields = ("full_name", "phone")
    list_filter = ("role", "is_paid", "is_active", "is_iman_student", "group")
    list_editable = ("group", "is_paid", "paid_until", "is_active")
    ordering = ("role", "full_name")
    actions = ("grant_30_days", "grant_90_days", "deactivate_students", "activate_students")
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Profile", {"fields": ("full_name", "avatar", "role", "group")}),
        ("Access", {"fields": ("is_active", "is_iman_student", "is_paid", "paid_until")}),
        (
            "Progress",
            {
                "fields": (
                    "points",
                    "status_badge",
                    "progress_grammar",
                    "progress_vocabulary",
                    "progress_homework",
                    "progress_speaking",
                    "progress_attendance",
                    "weekly_xp",
                    "level",
                    "streak_days",
                )
            },
        ),
        ("Admin permissions", {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("phone", "full_name", "role", "password1", "password2", "is_staff", "is_superuser"),
            },
        ),
    )

    @admin.action(description="Give selected users free access for 30 days")
    def grant_30_days(self, request, queryset):
        paid_until = timezone.now() + timedelta(days=30)
        queryset.update(is_paid=True, paid_until=paid_until)

    @admin.action(description="Give selected users free access for 90 days")
    def grant_90_days(self, request, queryset):
        paid_until = timezone.now() + timedelta(days=90)
        queryset.update(is_paid=True, paid_until=paid_until)

    @admin.action(description="Deactivate selected students")
    def deactivate_students(self, request, queryset):
        queryset.filter(role="student").update(is_active=False, is_iman_student=False, group=None)

    @admin.action(description="Activate selected students")
    def activate_students(self, request, queryset):
        queryset.filter(role="student").update(is_active=True, is_iman_student=True)


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "provider", "amount", "status", "created_at", "paid_at")
    search_fields = ("id", "external_id", "user__full_name", "user__phone")
    list_filter = ("provider", "status")
