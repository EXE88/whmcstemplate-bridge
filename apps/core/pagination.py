"""
Pagination helpers.

WHMCS paginates server-side through ``limitstart``/``limitnum`` and returns a
``totalresults`` counter, so we expose a *page/page_size* API to the frontend and
translate it into offsets before hitting WHMCS - the SPA never learns WHMCS'
vocabulary.
"""

from dataclasses import dataclass

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

MAX_PAGE_SIZE = 100


class DefaultPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = MAX_PAGE_SIZE


@dataclass(frozen=True, slots=True)
class PageRequest:
    """Normalised, validated pagination input for a WHMCS-backed list view."""

    page: int
    page_size: int

    @property
    def limit_start(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit_num(self) -> int:
        return self.page_size

    @classmethod
    def from_query(cls, query, default_size: int = 20) -> "PageRequest":
        def _int(name: str, default: int) -> int:
            try:
                return max(1, int(query.get(name, default)))
            except (TypeError, ValueError):
                return default

        return cls(
            page=_int("page", 1),
            page_size=min(_int("page_size", default_size), MAX_PAGE_SIZE),
        )


def paginated_response(items: list, total: int, page: PageRequest) -> Response:
    """Envelope matching DefaultPagination so the SPA sees one shape everywhere."""
    return Response(
        {
            "count": total,
            "page": page.page,
            "page_size": page.page_size,
            "num_pages": max(1, -(-total // page.page_size)),
            "results": items,
        }
    )
