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
    actions = (
        "grant_30_days",
        "grant_90_days",
        "grant_365_days",
        "revoke_paid_access",
        "remove_from_group",
        "deactivate_students",
        "activate_students",
    )
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

    teacher_fieldsets = (
        ("Student", {"fields": ("phone", "full_name", "avatar", "group")}),
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
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        if request.user.role == "teacher":
            return queryset.filter(role="student", group__teacher=request.user)
        return queryset.none()

    def get_list_display(self, request):
        if request.user.is_superuser:
            return self.list_display
        return ("id", "full_name", "phone", "group", "points", "is_paid", "paid_until", "is_active")

    def get_list_editable(self, request):
        if request.user.is_superuser:
            return self.list_editable
        return ("group", "points", "is_paid", "paid_until", "is_active")

    def get_list_filter(self, request):
        if request.user.is_superuser:
            return self.list_filter
        return ("is_paid", "is_active", "group")

    def get_fieldsets(self, request, obj=None):
        if request.user.is_superuser:
            return super().get_fieldsets(request, obj)
        return self.teacher_fieldsets

    def get_readonly_fields(self, request, obj=None):
        if request.user.is_superuser:
            return super().get_readonly_fields(request, obj)
        return ("phone",)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if request.user.is_superuser:
            return actions
        allowed = {"grant_30_days", "grant_90_days", "grant_365_days", "revoke_paid_access", "remove_from_group"}
        return {name: action for name, action in actions.items() if name in allowed}

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "group" and not request.user.is_superuser and request.user.role == "teacher":
            kwargs["queryset"] = request.user.teaching_groups.order_by("title", "time")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if obj.role == "teacher":
            obj.is_staff = True
        if not request.user.is_superuser and request.user.role == "teacher":
            obj.role = "student"
        super().save_model(request, obj, form, change)

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_staff

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or request.user.role == "teacher"

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if request.user.role != "teacher":
            return False
        return obj is None or (obj.role == "student" and obj.group and obj.group.teacher_id == request.user.id)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.action(description="Give selected users free access for 30 days")
    def grant_30_days(self, request, queryset):
        paid_until = timezone.now() + timedelta(days=30)
        queryset.filter(role="student").update(is_paid=True, paid_until=paid_until)

    @admin.action(description="Give selected users free access for 90 days")
    def grant_90_days(self, request, queryset):
        paid_until = timezone.now() + timedelta(days=90)
        queryset.filter(role="student").update(is_paid=True, paid_until=paid_until)

    @admin.action(description="Give selected users free access for 1 year")
    def grant_365_days(self, request, queryset):
        paid_until = timezone.now() + timedelta(days=365)
        queryset.filter(role="student").update(is_paid=True, paid_until=paid_until)

    @admin.action(description="Remove paid access from selected students")
    def revoke_paid_access(self, request, queryset):
        queryset.filter(role="student").update(is_paid=False, paid_until=None)

    @admin.action(description="Remove selected students from their group")
    def remove_from_group(self, request, queryset):
        queryset.filter(role="student").update(group=None)

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
