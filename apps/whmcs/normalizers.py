"""
WHMCS' JSON is inconsistent: collections are wrapped twice
(``{"invoices": {"invoice": [...]}}``), a single result may come back as a dict
instead of a list, numbers arrive as strings, dates as ``"0000-00-00"``, and
booleans as ``"on"``/``"yes"``/``1``.

Every response passes through here before it reaches a serializer, so the SPA
sees typed, predictable JSON and no WHMCS field names leak by accident.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_EMPTY_DATES = {"", "0000-00-00", "0000-00-00 00:00:00", None}
_TRUTHY = {"1", "on", "yes", "true", "y", "enabled"}


def collection(payload: dict, outer: str, inner: str) -> list[dict]:
    """Unwrap ``{"invoices": {"invoice": [...]}}`` into ``[...]``."""
    node = payload.get(outer) or {}
    if isinstance(node, list):
        return [item for item in node if isinstance(item, dict)]
    if not isinstance(node, dict):
        return []
    items = node.get(inner, [])
    if isinstance(items, dict):
        return [items]
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def total_of(payload: dict, fallback: int = 0) -> int:
    """WHMCS reports the unpaginated size in ``totalresults``."""
    return to_int(payload.get("totalresults"), fallback)


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def to_decimal(value: Any, default: str = "0.00") -> Decimal:
    if isinstance(value, Decimal):
        return value
    raw = str(value if value is not None else default).replace(",", "").strip()
    # WHMCS may prefix the currency symbol on *_formatted-ish fields.
    raw = "".join(ch for ch in raw if ch.isdigit() or ch in ".-")
    try:
        return Decimal(raw or default)
    except InvalidOperation:
        return Decimal(default)


def to_money(value: Any) -> str:
    """Money crosses the API as a decimal *string* - never a float."""
    return str(to_decimal(value).quantize(Decimal("0.01")))


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUTHY


def to_datetime(value: Any) -> datetime | None:
    if value in _EMPTY_DATES:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def to_date(value: Any) -> date | None:
    parsed = to_datetime(value)
    return parsed.date() if parsed else None


def iso(value: Any) -> str | None:
    parsed = to_datetime(value)
    return parsed.isoformat() if parsed else None


def iso_date(value: Any) -> str | None:
    parsed = to_date(value)
    return parsed.isoformat() if parsed else None


def pick(raw: dict, mapping: dict[str, tuple[str, Any]]) -> dict:
    """
    Project a WHMCS record onto our own field names.

        pick(raw, {"id": ("id", to_int), "total": ("total", to_money)})

    ``mapping`` is ``{our_name: (whmcs_name, converter)}``. Fields absent from
    the mapping are dropped - an accidental leak of an internal WHMCS column is
    therefore impossible.
    """
    out: dict[str, Any] = {}
    for our_name, (whmcs_name, converter) in mapping.items():
        value = raw.get(whmcs_name)
        out[our_name] = converter(value) if converter else value
    return out


def identity(value: Any) -> Any:
    return value


def text(value: Any) -> str:
    return "" if value is None else str(value)
