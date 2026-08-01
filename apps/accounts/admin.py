from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "whmcs_client_id", "is_active", "is_staff", "last_synced_at")
    search_fields = ("email", "whmcs_client_id")
    list_filter = ("is_active", "is_staff")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("WHMCS", {"fields": ("whmcs_client_id", "last_synced_at")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),
    )
    readonly_fields = ("last_login", "date_joined")
