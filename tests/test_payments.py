"""
Native Zibal checkout.

These tests exist because every bug in this flow is a bug about money: charging
without crediting, crediting without charging, or crediting twice.
"""

import httpx
import pytest
import respx

from apps.payments.models import PaymentAttempt, PaymentStatus
from apps.payments.services import PaymentService, to_rial

from .conftest import WHMCS_ENDPOINT

pytestmark = pytest.mark.django_db

ZIBAL_REQUEST = "https://gateway.zibal.ir/v1/request"
ZIBAL_VERIFY = "https://gateway.zibal.ir/v1/verify"

INVOICE = {
    "result": "success",
    "invoiceid": 34,
    "id": 34,
    "userid": 42,
    "status": "Unpaid",
    "date": "2026-07-01",
    "duedate": "2026-07-10",
    "total": "1250000.00",
    "balance": "1250000.00",
    "currencycode": "IRR",
}


@pytest.fixture(autouse=True)
def payment_settings(settings):
    settings.PAYMENTS = {
        **settings.PAYMENTS,
        "CALLBACK_BASE_URL": "https://api.lithiumhost.ir",
        "RESULT_URL": "https://lithiumhost.ir/checkout/result",
        "AMOUNT_MULTIPLIER": 1,
        "ZIBAL": {"MERCHANT": "test-merchant", "TIMEOUT": 5, "VERIFY_RETRIES": 0},
    }
    return settings


def _start(auth_client):
    return auth_client.post("/api/v1/billing/invoices/34/pay/", format="json")


@respx.mock
def test_starting_a_payment_returns_a_gateway_redirect(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, json=INVOICE))
    request_route = respx.post(ZIBAL_REQUEST).mock(
        return_value=httpx.Response(200, json={"result": 100, "trackId": 999888})
    )

    response = _start(auth_client)

    assert response.status_code == 201
    body = response.json()
    assert body["redirect_url"] == "https://gateway.zibal.ir/start/999888"
    assert body["amount_rial"] == 1250000

    sent = request_route.calls[0].request.content.decode()
    assert '"merchant": "test-merchant"' in sent
    # The callback must point back at this service, not at WHMCS.
    assert "https://api.lithiumhost.ir/api/v1/payments/callback/zibal/" in sent

    attempt = PaymentAttempt.objects.get()
    assert attempt.status == PaymentStatus.REDIRECTED
    assert attempt.track_id == "999888"


@respx.mock
def test_the_amount_comes_from_whmcs_not_from_the_request(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, json=INVOICE))
    request_route = respx.post(ZIBAL_REQUEST).mock(
        return_value=httpx.Response(200, json={"result": 100, "trackId": 1})
    )

    auth_client.post(
        "/api/v1/billing/invoices/34/pay/",
        data={"amount": "1", "amount_rial": 1, "balance": "1"},
        format="json",
    )

    assert '"amount": 1250000' in request_route.calls[0].request.content.decode()


@respx.mock
def test_paying_someone_elses_invoice_is_404(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(200, json={**INVOICE, "userid": 999})
    )

    assert _start(auth_client).status_code == 404
    assert PaymentAttempt.objects.count() == 0


@respx.mock
def test_a_paid_invoice_cannot_be_paid_again(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(200, json={**INVOICE, "status": "Paid"})
    )

    response = _start(auth_client)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invoice_not_payable"


@respx.mock
def test_successful_callback_records_the_payment_in_whmcs(auth_client, client):
    respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, json=INVOICE))
    respx.post(ZIBAL_REQUEST).mock(
        return_value=httpx.Response(200, json={"result": 100, "trackId": "T1"})
    )
    _start(auth_client)

    verify_route = respx.post(ZIBAL_VERIFY).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": 100,
                "status": 1,
                "amount": 1250000,
                "refNumber": "REF-77",
                "cardNumber": "621986******1234",
                "paidAt": "2026-08-01T12:00:00.000000",
            },
        )
    )
    whmcs_route = respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"result": "success"})
    )

    response = client.get("/api/v1/payments/callback/zibal/?trackId=T1&success=1&status=2")

    assert response.status_code == 302
    assert response["Location"].startswith("https://lithiumhost.ir/checkout/result?status=success")
    assert verify_route.called

    attempt = PaymentAttempt.objects.get()
    assert attempt.status == PaymentStatus.RECORDED
    assert attempt.ref_number == "REF-77"

    recorded = whmcs_route.calls[-1].request.content.decode()
    assert "action=AddInvoicePayment" in recorded
    assert "invoiceid=34" in recorded
    assert "transid=REF-77" in recorded


@respx.mock
def test_a_forged_success_callback_credits_nothing(auth_client, client):
    respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, json=INVOICE))
    respx.post(ZIBAL_REQUEST).mock(
        return_value=httpx.Response(200, json={"result": 100, "trackId": "T2"})
    )
    _start(auth_client)

    # The attacker opens the callback themselves. Zibal says it was never paid.
    respx.post(ZIBAL_VERIFY).mock(
        return_value=httpx.Response(200, json={"result": 202, "status": 3, "amount": 1250000})
    )
    whmcs_route = respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"result": "success"})
    )

    response = client.get("/api/v1/payments/callback/zibal/?trackId=T2&success=1&status=2")

    attempt = PaymentAttempt.objects.get()
    assert response["Location"].endswith(f"status=failed&invoice=34&payment={attempt.id}")
    assert attempt.status == PaymentStatus.FAILED
    assert "AddInvoicePayment" not in "".join(
        call.request.content.decode() for call in whmcs_route.calls
    )


@respx.mock
def test_a_replayed_callback_does_not_credit_twice(auth_client, client):
    respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, json=INVOICE))
    respx.post(ZIBAL_REQUEST).mock(
        return_value=httpx.Response(200, json={"result": 100, "trackId": "T3"})
    )
    _start(auth_client)

    respx.post(ZIBAL_VERIFY).mock(
        return_value=httpx.Response(
            200, json={"result": 100, "status": 1, "amount": 1250000, "refNumber": "REF-9"}
        )
    )
    whmcs_route = respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"result": "success"})
    )

    url = "/api/v1/payments/callback/zibal/?trackId=T3&success=1&status=2"
    client.get(url)
    client.get(url)
    client.get(url)

    payments = [
        call for call in whmcs_route.calls if "AddInvoicePayment" in call.request.content.decode()
    ]
    assert len(payments) == 1


@respx.mock
def test_a_verified_payment_for_the_wrong_amount_is_parked(auth_client, client):
    respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, json=INVOICE))
    respx.post(ZIBAL_REQUEST).mock(
        return_value=httpx.Response(200, json={"result": 100, "trackId": "T4"})
    )
    _start(auth_client)

    respx.post(ZIBAL_VERIFY).mock(
        return_value=httpx.Response(
            200, json={"result": 100, "status": 1, "amount": 10000, "refNumber": "REF-X"}
        )
    )
    whmcs_route = respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"result": "success"})
    )

    client.get("/api/v1/payments/callback/zibal/?trackId=T4&success=1&status=2")

    assert PaymentAttempt.objects.get().status == PaymentStatus.MISMATCH
    assert "AddInvoicePayment" not in "".join(
        call.request.content.decode() for call in whmcs_route.calls
    )


def test_unknown_track_id_redirects_without_leaking_anything(client):
    response = client.get("/api/v1/payments/callback/zibal/?trackId=nope")

    assert response.status_code == 302
    assert response["Location"] == "https://lithiumhost.ir/checkout/result?status=pending"


def test_the_result_redirect_cannot_be_pointed_elsewhere(client):
    response = client.get(
        "/api/v1/payments/callback/zibal/?trackId=x&callback=https://evil.example/steal"
    )

    assert response["Location"].startswith("https://lithiumhost.ir/")


def test_toman_installs_are_converted_to_rial(settings):
    settings.PAYMENTS = {**settings.PAYMENTS, "AMOUNT_MULTIPLIER": 10}

    assert to_rial("125000.00") == 1250000


@respx.mock
def test_customer_can_check_the_outcome_without_trusting_the_redirect(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, json=INVOICE))
    respx.post(ZIBAL_REQUEST).mock(
        return_value=httpx.Response(200, json={"result": 100, "trackId": "T5"})
    )
    payment_id = _start(auth_client).json()["payment_id"]

    response = auth_client.get(f"/api/v1/payments/{payment_id}/")

    assert response.status_code == 200
    assert response.json()["status"] == "redirected"
    # Internal gateway diagnostics are not part of the customer-facing shape.
    assert "gateway_result" not in response.json()


@respx.mock
def test_another_customers_payment_is_not_visible(auth_client, django_user_model):
    respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, json=INVOICE))
    respx.post(ZIBAL_REQUEST).mock(
        return_value=httpx.Response(200, json={"result": 100, "trackId": "T6"})
    )
    _start(auth_client)

    stranger = PaymentAttempt.objects.get()
    stranger.whmcs_client_id = 999
    stranger.save(update_fields=["whmcs_client_id"])

    assert auth_client.get(f"/api/v1/payments/{stranger.id}/").status_code == 404


@respx.mock
def test_reconciliation_records_a_payment_whmcs_missed(auth_client):
    """The customer paid, but WHMCS was down when they came back."""
    from io import StringIO

    from django.core.management import call_command

    respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, json=INVOICE))
    respx.post(ZIBAL_REQUEST).mock(
        return_value=httpx.Response(200, json={"result": 100, "trackId": "T9"})
    )
    _start(auth_client)

    attempt = PaymentAttempt.objects.get()
    attempt.status = PaymentStatus.PAID  # verified earlier, never recorded
    attempt.amount_whmcs = "1250000.00"
    attempt.save()

    respx.post(ZIBAL_VERIFY).mock(
        return_value=httpx.Response(
            200, json={"result": 201, "status": 1, "amount": 1250000, "refNumber": "REF-R"}
        )
    )
    whmcs_route = respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"result": "success"})
    )

    out = StringIO()
    call_command("reconcile_payments", stdout=out)

    attempt.refresh_from_db()
    assert attempt.status == PaymentStatus.RECORDED
    assert "AddInvoicePayment" in "".join(
        call.request.content.decode() for call in whmcs_route.calls
    )


def test_service_callback_url_points_at_this_service(payment_settings):
    assert (
        PaymentService(gateway=object()).callback_url()
        == "https://api.lithiumhost.ir/api/v1/payments/callback/zibal/"
    )
