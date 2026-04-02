from django.contrib import admin
from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "phone", "role", "points", "group")
    search_fields = ("full_name", "phone")
    list_filter = ("role",)