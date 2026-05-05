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
