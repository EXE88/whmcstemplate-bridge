"""
Latency behaviour.

A WHMCS call costs ~1.1s of upstream PHP, so "how many calls does this make"
is a correctness-level concern here, not a micro-optimisation. These tests pin
the call counts and the cache semantics that keep them down.
"""

import time

import httpx
import pytest
import respx

from apps.core.aggregate import gather
from apps.core.cache import get_or_set
from apps.core.pagination import PageRequest

from .conftest import WHMCS_ENDPOINT

pytestmark = pytest.mark.django_db

TICKET = {
    "result": "success",
    "ticketid": 7,
    "tid": "123456",
    "userid": 42,
    "subject": "Slow",
    "status": "Open",
    "date": "2026-07-30 10:00:00",
    "replies": {"reply": []},
}

DOMAIN = {
    "result": "success",
    "totalresults": 1,
    "domains": {
        "domain": [{"id": 5, "userid": 42, "domainname": "example.ir", "status": "Active"}]
    },
}


def test_a_fresh_entry_is_served_without_calling_the_producer():
    calls = []

    def producer():
        calls.append(1)
        return "value"

    assert get_or_set("ns", "catalogue", producer, "k") == "value"
    assert get_or_set("ns", "catalogue", producer, "k") == "value"
    assert len(calls) == 1


def test_an_expired_entry_is_served_stale_while_it_refreshes(settings):
    settings.CACHE_TTL = {**settings.CACHE_TTL, "catalogue": 1}
    calls = []

    def producer():
        calls.append(len(calls))
        return f"value-{len(calls)}"

    assert get_or_set("ns2", "catalogue", producer, "k") == "value-1"
    time.sleep(1.1)

    # The expired value comes back immediately - the caller does not wait for
    # the upstream refresh.
    started = time.perf_counter()
    stale = get_or_set("ns2", "catalogue", producer, "k")
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert stale == "value-1"
    assert elapsed_ms < 50

    for _ in range(50):  # let the background refresh land
        if get_or_set("ns2", "catalogue", producer, "k") == "value-2":
            break
        time.sleep(0.05)

    assert get_or_set("ns2", "catalogue", producer, "k") == "value-2"


def test_stale_is_refused_where_it_would_be_wrong(settings):
    settings.CACHE_TTL = {**settings.CACHE_TTL, "catalogue": 1}
    calls = []

    def producer():
        calls.append(1)
        return len(calls)

    get_or_set("ns3", "catalogue", producer, "k", stale_ok=False)
    time.sleep(1.1)
    get_or_set("ns3", "catalogue", producer, "k", stale_ok=False)

    assert len(calls) == 2  # the caller waited for a fresh value both times


def test_gather_runs_sections_concurrently():
    def slow(value):
        def call():
            time.sleep(0.3)
            return value

        return call

    started = time.perf_counter()
    results = gather({f"s{i}": slow(i) for i in range(5)})
    elapsed = time.perf_counter() - started

    assert results == {f"s{i}": i for i in range(5)}
    assert elapsed < 0.9  # 1.5s if it were sequential


def test_gather_survives_one_failing_section():
    def boom():
        raise RuntimeError("upstream died")

    results = gather({"ok": lambda: 1, "broken": boom})

    assert results["ok"] == 1
    assert results["broken"] is None


@respx.mock
def test_replying_to_a_ticket_costs_two_upstream_calls_not_three(auth_client):
    """The ownership check reads from cache; only the write and the re-read
    reach WHMCS."""
    route = respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, json=TICKET))

    auth_client.get("/api/v1/support/tickets/7/")  # warms the cache: 1 call
    before = route.call_count

    auth_client.post(
        "/api/v1/support/tickets/7/replies/", data={"message": "still slow"}, format="json"
    )

    assert route.call_count - before == 2


@respx.mock
def test_updating_nameservers_does_not_re_read_the_domain(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=DOMAIN),  # ownership
            httpx.Response(200, json={"result": "success"}),  # update
            httpx.Response(200, json={"result": "success", "ns1": "a.ir", "ns2": "b.ir"}),
        ]
    )

    response = auth_client.put(
        "/api/v1/hosting/domains/5/nameservers/",
        data={"nameservers": ["a.ir", "b.ir"]},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["nameservers"] == ["a.ir", "b.ir"]


@respx.mock
def test_dashboard_answers_from_one_request(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "success",
                "totalresults": 0,
                "client": {"id": 42, "firstname": "Ali", "credit": "0.00"},
                "products": {"product": []},
                "domains": {"domain": []},
                "invoices": {"invoice": []},
                "tickets": {"ticket": []},
            },
        )
    )

    response = auth_client.get("/api/v1/dashboard/")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "profile",
        "summary",
        "services",
        "domains",
        "unpaid_invoices",
        "open_tickets",
        "unavailable",
    }
    assert body["unavailable"] == []


@respx.mock
def test_credit_balance_reuses_the_cached_profile(auth_client):
    route = respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "success",
                "client": {"id": 42, "credit": "10.00", "currency_code": "IRR"},
                "credits": {"credit": []},
            },
        )
    )

    auth_client.get("/api/v1/auth/me/")  # 1 call, fills the profile cache
    before = route.call_count

    auth_client.get("/api/v1/billing/credit/")

    # Only the credit log is fetched; the client record comes from cache.
    assert route.call_count - before == 1


def test_page_request_maps_to_whmcs_offsets():
    page = PageRequest(page=4, page_size=25)
    assert (page.limit_start, page.limit_num) == (75, 25)
