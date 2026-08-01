from django.contrib import admin

from .models import PaymentAttempt


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "invoice_id",
        "whmcs_client_id",
        "gateway",
        "amount_rial",
        "status",
        "track_id",
        "ref_number",
    )
    list_filter = ("status", "gateway", "created_at")
    search_fields = ("track_id", "ref_number", "invoice_id", "whmcs_client_id")
    date_hierarchy = "created_at"
    # Payment history is an audit trail: readable, never editable from here.
    readonly_fields = [field.name for field in PaymentAttempt._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
