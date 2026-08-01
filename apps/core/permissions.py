from rest_framework.permissions import BasePermission


class IsLinkedToWhmcsClient(BasePermission):
    """
    The user must be bound to a WHMCS client id.

    Every WHMCS call made on behalf of an end user is scoped by that id, so an
    unlinked account must never reach the service layer - that is what stops one
    customer reading another customer's invoices (IDOR).
    """

    message = "This account is not linked to a billing profile."

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.whmcs_client_id)
