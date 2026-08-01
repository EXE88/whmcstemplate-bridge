import logging
from typing import Any

from apps.core.cache import client_namespace, invalidate
from apps.core.pagination import PageRequest

from ..actions import Action
from ..client import WhmcsClient, get_client

logger = logging.getLogger(__name__)


class BaseService:
    """Common plumbing for every WHMCS-backed service."""

    #: Cache namespaces this service writes to; used by ``invalidate_for``.
    cache_resources: tuple[str, ...] = ()

    def __init__(self, client: WhmcsClient | None = None):
        self.client = client or get_client()

    # -- low level --------------------------------------------------------

    def call(self, action: Action, params: dict[str, Any] | None = None) -> dict:
        return self.client.call(action, params or {})

    @staticmethod
    def page_params(page: PageRequest) -> dict[str, int]:
        return {"limitstart": page.limit_start, "limitnum": page.limit_num}

    # -- cache ------------------------------------------------------------

    def invalidate_for(self, whmcs_client_id: int, *resources: str) -> None:
        """Drop this customer's cached data for the given resources."""
        for resource in resources or self.cache_resources:
            invalidate(client_namespace(whmcs_client_id, resource))


class OwnedResourceMixin:
    """
    Guard against IDOR.

    WHMCS happily returns invoice 5 to whoever asks for it, so every read of a
    single record is checked against the caller's client id *after* fetching and
    *before* returning. Services must call :meth:`assert_owned`.
    """

    @staticmethod
    def assert_owned(record_client_id: Any, whmcs_client_id: int) -> None:
        from apps.core.exceptions import ResourceNotFound

        from ..normalizers import to_int

        if to_int(record_client_id) != int(whmcs_client_id):
            logger.warning(
                "ownership check failed: record belongs to %s, caller is %s",
                record_client_id,
                whmcs_client_id,
            )
            # 404 rather than 403: do not confirm that the id exists.
            raise ResourceNotFound("Resource not found.")
