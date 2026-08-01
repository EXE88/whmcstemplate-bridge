"""
Public catalogue: products, TLD pricing, promotions, currencies, announcements.

This data is identical for every visitor and changes rarely, so it carries the
longest TTL in the project and is the first thing worth warming from Celery.
"""

from apps.core.cache import get_or_set

from ..actions import Action
from ..normalizers import collection, iso, pick, text, to_int, to_money
from .base import BaseService

_PRODUCT_MAP = {
    "id": ("pid", to_int),
    "group_id": ("gid", to_int),
    "type": ("type", text),
    "name": ("name", text),
    "description": ("description", text),
    "module": ("module", text),
    "payment_type": ("paytype", text),
}

_CYCLE_FIELDS = (
    ("monthly", "monthly"),
    ("quarterly", "quarterly"),
    ("semiannually", "semiannually"),
    ("annually", "annually"),
    ("biennially", "biennially"),
    ("triennially", "triennially"),
    ("setup", "msetupfee"),
)


class CatalogueService(BaseService):
    def list_products(self, group_id: int | None = None):
        """GetProducts takes no currency filter - every configured currency is
        returned inside each product's ``pricing`` map, and the frontend picks."""

        def fetch() -> list[dict]:
            params: dict = {}
            if group_id:
                params["gid"] = group_id
            data = self.call(Action.GET_PRODUCTS, params)
            return [self._product(raw) for raw in collection(data, "products", "product")]

        return get_or_set("catalogue:products", "catalogue", fetch, group_id or 0)

    def _product(self, raw: dict) -> dict:
        product = pick(raw, _PRODUCT_MAP)
        pricing = raw.get("pricing") or {}
        product["pricing"] = {
            currency: {
                our_name: to_money(values.get(whmcs_name))
                for our_name, whmcs_name in _CYCLE_FIELDS
                if str(values.get(whmcs_name, "-1")) not in ("-1", "-1.00")
            }
            for currency, values in pricing.items()
            if isinstance(values, dict)
        }
        return product

    def list_currencies(self) -> list[dict]:
        def fetch() -> list[dict]:
            data = self.call(Action.GET_CURRENCIES)
            return [
                pick(
                    raw,
                    {
                        "id": ("id", to_int),
                        "code": ("code", text),
                        "prefix": ("prefix", text),
                        "suffix": ("suffix", text),
                        "rate": ("rate", to_money),
                        "is_default": ("default", to_int),
                    },
                )
                for raw in collection(data, "currencies", "currency")
            ]

        return get_or_set("catalogue:currencies", "catalogue", fetch, "list")

    def tld_pricing(self, currency_id: int | None = None) -> dict:
        def fetch() -> dict:
            params = {"currencyid": currency_id} if currency_id else {}
            data = self.call(Action.GET_TLD_PRICING, params)
            pricing = data.get("pricing") or {}
            return {
                tld: {
                    "categories": values.get("categories") or [],
                    "register": _cheapest(values.get("register")),
                    "transfer": _cheapest(values.get("transfer")),
                    "renew": _cheapest(values.get("renew")),
                    # WHMCS 8.x returns these as null unless configured.
                    "grace_period": values.get("grace_period"),
                    "redemption_period": values.get("redemption_period"),
                }
                for tld, values in pricing.items()
                if isinstance(values, dict)
            }

        return get_or_set("catalogue:tlds", "catalogue", fetch, currency_id or 0)

    def list_announcements(self, limit: int = 10) -> list[dict]:
        def fetch() -> list[dict]:
            data = self.call(Action.GET_ANNOUNCEMENTS, {"limitnum": limit})
            return [
                pick(
                    raw,
                    {
                        "id": ("id", to_int),
                        "title": ("title", text),
                        "body": ("announcement", text),
                        "published_at": ("date", iso),
                    },
                )
                for raw in collection(data, "announcements", "announcement")
            ]

        return get_or_set("catalogue:announcements", "catalogue", fetch, limit)


def _cheapest(periods) -> str | None:
    """TLD pricing arrives as ``{"1": "12.00", "2": "24.00"}``; expose year one.

    Periods only appear when a price is configured, so an empty/absent map means
    "not offered" rather than "free".
    """
    if not isinstance(periods, dict) or not periods:
        return None
    first = periods.get("1") or next(iter(periods.values()))
    return to_money(first)
