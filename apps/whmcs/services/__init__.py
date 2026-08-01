"""
Service layer.

Views never call ``WhmcsClient`` directly. They call a service, which:

* accepts already-validated primitives (no request objects, no serializers)
* scopes every call to a WHMCS client id
* normalises the response into our own field names
* decides what may be cached and invalidates it on writes

That keeps business rules testable without HTTP and makes the same code reusable
from Celery tasks, management commands or a future WebSocket consumer.
"""

from .accounts import AccountService
from .billing import BillingService
from .catalogue import CatalogueService
from .hosting import DomainService, ServiceService
from .orders import OrderService
from .support import SupportService

__all__ = [
    "AccountService",
    "BillingService",
    "CatalogueService",
    "DomainService",
    "OrderService",
    "ServiceService",
    "SupportService",
]
