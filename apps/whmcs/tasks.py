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


@shared_task(name="whmcs.provision.place_order", bind=True, max_retries=0)
def place_order(self, whmcs_client_id: int, payload: dict) -> dict:
    """
    Place an order off the request path.

    ``POST /orders/`` is synchronous because AddOrder is a single fast call, but
    a basket that registers several domains can make WHMCS talk to registrars
    and take tens of seconds. When that becomes a problem, the view can queue
    this instead and answer 202 with the task id.

    ``payload`` must already be serializer-validated - a task is not a way to
    skip validation.

    Deliberately **not retried**: if the AddOrder response is lost in transit the
    order may still have been created, and a retry would bill the customer
    twice. A failure surfaces to the customer, who retries with the same
    idempotency key.
    """
    from .services import OrderService

    return OrderService().create_order(
        whmcs_client_id,
        items=payload["items"],
        payment_method=payload["payment_method"],
        promo_code=payload.get("promo_code", ""),
        nameservers=payload.get("nameservers"),
        client_ip=payload.get("client_ip", ""),
    )
