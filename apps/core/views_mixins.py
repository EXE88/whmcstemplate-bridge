"""Shared view base classes."""

from rest_framework.views import APIView

from . import http
from .pagination import PageRequest, paginated_response
from .permissions import IsLinkedToWhmcsClient
from .throttling import SustainedUserThrottle, WriteRateThrottle


class ClientScopedAPIView(APIView):
    """
    Base for every endpoint that reads or writes WHMCS data on behalf of the
    logged-in customer.

    ``self.whmcs_client_id`` is taken from the authenticated user row - never
    from a URL, query string or request body. That single rule is what keeps one
    customer out of another's data, no matter what the frontend sends.
    """

    permission_classes = [IsLinkedToWhmcsClient]
    # Setting throttle_classes *replaces* DEFAULT_THROTTLE_CLASSES, so the
    # per-user rate has to be repeated here - otherwise reads on every
    # client-scoped endpoint would be unthrottled.
    throttle_classes = [SustainedUserThrottle, WriteRateThrottle]
    throttle_scope = "user"

    @property
    def whmcs_client_id(self) -> int:
        return int(self.request.user.whmcs_client_id)

    @property
    def client_ip(self) -> str:
        return http.client_ip(self.request)

    def page(self) -> PageRequest:
        return PageRequest.from_query(self.request.query_params)

    def paginated(self, items: list, total: int):
        return paginated_response(items, total, self.page())
