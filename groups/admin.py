from django.contrib import admin
from .models import Group


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "time", "days_pattern", "teacher")
    search_fields = ("title", "time")
    list_filter = ("days_pattern",)