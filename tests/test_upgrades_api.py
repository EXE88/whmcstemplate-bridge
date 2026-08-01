"""Service upgrades: pricing preview and the upgrade order itself."""

import httpx
import pytest
import respx

from .conftest import WHMCS_ENDPOINT

pytestmark = pytest.mark.django_db

SERVICE = {
    "result": "success",
    "totalresults": 1,
    "products": {
        "product": [
            {
                "id": 100,
                "clientid": 42,
                "pid": 1,
                "name": "Respina-0001",
                "domain": "example.ir",
                "status": "Active",
                "billingcycle": "annually",
            }
        ]
    },
}

PRODUCTS = {
    "result": "success",
    "products": {
        "product": [
            {"pid": 1, "gid": 3, "name": "Respina-0001", "type": "hostingaccount"},
            {"pid": 2, "gid": 3, "name": "Respina-0002", "type": "hostingaccount"},
            {"pid": 9, "gid": 7, "name": "Unrelated internal plan", "type": "server"},
        ]
    },
}

GATEWAYS = {
    "result": "success",
    "paymentmethods": {"paymentmethod": [{"module": "zibal", "displayname": "Zibal"}]},
}

UPGRADE_OK = {
    "result": "success",
    "oldproductid": "1",
    "oldproductname": "Respina-0001",
    "newproductid": 2,
    "newproductname": "Respina-0002",
    "newproductbillingcycle": "annually",
    "daysuntilrenewal": 200,
    "totaldays": 365,
    "price": "1250000.00",
    "orderid": 55,
    "invoiceid": 77,
}


@respx.mock
def test_upgrade_options_are_limited_to_the_current_product_group(auth_client):
    group_products = {
        "result": "success",
        "products": {"product": PRODUCTS["products"]["product"][:2]},  # gid 3 only
    }
    route = respx.post(WHMCS_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=SERVICE),
            httpx.Response(200, json=PRODUCTS),  # locate the current product
            httpx.Response(200, json=group_products),  # products in that group
        ]
    )

    response = auth_client.get("/api/v1/hosting/services/100/upgrade-options/")

    assert response.status_code == 200
    offered = [product["id"] for product in response.json()]
    # The group of the current product is what WHMCS is asked for...
    assert "gid=3" in route.calls[2].request.content.decode()
    # ...and the plan they already have is not offered back to them.
    assert offered == [2]


@respx.mock
def test_quote_does_not_place_an_order(auth_client):
    route = respx.post(WHMCS_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=SERVICE),
            httpx.Response(200, json=PRODUCTS),
            httpx.Response(200, json=PRODUCTS),
            httpx.Response(200, json=GATEWAYS),  # calconly still needs a gateway
            httpx.Response(200, json={**UPGRADE_OK, "orderid": 0, "invoiceid": None}),
        ]
    )

    response = auth_client.post(
        "/api/v1/hosting/services/100/upgrade/quote/",
        data={"new_product_id": 2, "billing_cycle": "annually"},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["price"] == "1250000.00"
    assert "calconly=1" in route.calls[-1].request.content.decode()


@respx.mock
def test_upgrade_creates_an_invoice_and_a_payment_link(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=SERVICE),
            httpx.Response(200, json=PRODUCTS),
            httpx.Response(200, json=PRODUCTS),
            httpx.Response(200, json=GATEWAYS),
            httpx.Response(200, json=UPGRADE_OK),
        ]
    )

    response = auth_client.post(
        "/api/v1/hosting/services/100/upgrade/",
        data={"new_product_id": 2, "billing_cycle": "annually", "payment_method": "zibal"},
        format="json",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["new_product_name"] == "Respina-0002"
    assert body["invoice_id"] == 77
    assert body["payment_url"].endswith("/viewinvoice.php?id=77")


@respx.mock
def test_upgrading_onto_a_product_outside_the_group_is_refused(auth_client):
    route = respx.post(WHMCS_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=SERVICE),
            httpx.Response(200, json=PRODUCTS),
            httpx.Response(
                200,
                json={
                    "result": "success",
                    "products": {"product": [PRODUCTS["products"]["product"][1]]},
                },
            ),
        ]
    )

    response = auth_client.post(
        "/api/v1/hosting/services/100/upgrade/",
        data={"new_product_id": 9, "billing_cycle": "annually", "payment_method": "zibal"},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "upgrade_not_allowed"
    # UpgradeProduct was never called.
    assert route.call_count == 3


@respx.mock
def test_upgrading_somebody_elses_service_is_404(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "success",
                "totalresults": 1,
                "products": {"product": [{"id": 100, "clientid": 999, "pid": 1}]},
            },
        )
    )

    response = auth_client.post(
        "/api/v1/hosting/services/100/upgrade/",
        data={"new_product_id": 2, "billing_cycle": "annually", "payment_method": "zibal"},
        format="json",
    )

    assert response.status_code == 404
