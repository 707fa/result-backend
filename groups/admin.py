from django.contrib import admin
from .models import Group


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "time", "days_pattern", "teacher", "students_count")
    list_editable = ("title", "time", "days_pattern", "teacher")
    search_fields = ("title", "time", "teacher__full_name", "teacher__phone")
    list_filter = ("days_pattern", "teacher")
    ordering = ("title", "time")

    def students_count(self, obj):
        return obj.students.filter(role="student", is_active=True, is_iman_student=True).count()

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        if request.user.role == "teacher":
            return queryset.filter(teacher=request.user)
        return queryset.none()

    def get_list_display(self, request):
        if request.user.is_superuser:
            return self.list_display
        return ("id", "title", "time", "days_pattern", "students_count")

    def get_list_editable(self, request):
        if request.user.is_superuser:
            return self.list_editable
        return ("title", "time", "days_pattern")

    def get_exclude(self, request, obj=None):
        if request.user.is_superuser:
            return super().get_exclude(request, obj)
        return ("teacher",)

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser and request.user.role == "teacher":
            obj.teacher = request.user
        super().save_model(request, obj, form, change)

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_staff

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or request.user.role == "teacher"

    def has_add_permission(self, request):
        return request.user.is_superuser or request.user.role == "teacher"

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if request.user.role != "teacher":
            return False
        return obj is None or obj.teacher_id == request.user.id

    def has_delete_permission(self, request, obj=None):
        return self.has_change_permission(request, obj)
