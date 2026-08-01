"""
Background work.

Rule of thumb for what belongs here: anything that is slow, retryable and not
needed to render the current response. WHMCS calls that block on a registrar or
a control panel (domain registration, package changes, mass syncs) are exactly
that; a customer's invoice list is not.

While no broker is configured these run eagerly, so the code path is identical
in development.
"""

import logging

from celery import shared_task

from .exceptions import WhmcsTransportError
from .services import CatalogueService

logger = logging.getLogger(__name__)

RETRY_KWARGS = {
    "autoretry_for": (WhmcsTransportError,),
    "retry_backoff": 5,
    "retry_backoff_max": 300,
    "retry_jitter": True,
    "max_retries": 5,
}


@shared_task(name="whmcs.sync.warm_catalogue", **RETRY_KWARGS)
def warm_catalogue_cache() -> dict:
    """Pre-fill the public catalogue so the storefront never pays for a cold miss.

    Schedule from celery beat, e.g. every 10 minutes.
    """
    catalogue = CatalogueService()
    products = catalogue.list_products()
    currencies = catalogue.list_currencies()
    tlds = catalogue.tld_pricing()
    logger.info(
        "catalogue warmed: %s products, %s currencies, %s tlds",
        len(products),
        len(currencies),
        len(tlds),
    )
    return {"products": len(products), "currencies": len(currencies), "tlds": len(tlds)}


@shared_task(name="whmcs.sync.refresh_client_cache", **RETRY_KWARGS)
def refresh_client_cache(whmcs_client_id: int) -> None:
    """Re-read a customer's hot data after a webhook/write, off the request path."""
    from apps.core.pagination import PageRequest

    from .services import BillingService, ServiceService

    page = PageRequest(page=1, page_size=20)
    BillingService().list_invoices(whmcs_client_id, page)
    ServiceService().list_services(whmcs_client_id, page)


@shared_task(name="whmcs.provision.place_order", bind=True, **RETRY_KWARGS)
def place_order(self, whmcs_client_id: int, payload: dict) -> dict:
    """
    Placeholder for the heavy ordering flow.

    Ordering touches registrars and provisioning modules and can take tens of
    seconds, so the view should accept the request, queue this task and return
    ``202`` with a task id the SPA can poll (or receive over WebSocket later).
    """
    raise NotImplementedError("Wire up AddOrder/AcceptOrder when checkout ships.")
