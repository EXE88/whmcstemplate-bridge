"""Regression tests for the guarantees the API is supposed to make."""

import httpx
import pytest
import respx
from django.core.cache import cache
from django.test import RequestFactory

from apps.core.cache import build_key, client_namespace, invalidate
from apps.core.http import client_ip
from apps.core.throttling import LoginIPThrottle, LoginRateThrottle

from .conftest import WHMCS_ENDPOINT

pytestmark = pytest.mark.django_db


@respx.mock
def test_password_change_refuses_when_credentials_belong_to_another_client(auth_client):
    # ValidateLogin succeeds but reports a different client than the session.
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"result": "success", "userid": 999, "twoFactorEnabled": False}
        )
    )

    response = auth_client.post(
        "/api/v1/auth/me/password/",
        data={"current_password": "correct-horse", "new_password": "N3w-p4ssw0rd!"},
        format="json",
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


@respx.mock
def test_credential_stuffing_across_accounts_is_capped_per_ip(client, monkeypatch):
    # DRF binds THROTTLE_RATES on the class at import time, so overriding
    # settings.REST_FRAMEWORK would not reach it.
    rates = {
        **LoginIPThrottle.THROTTLE_RATES,
        "login": "100/min",  # per-account limit deliberately out of the way
        "login_ip": "3/min",
    }
    monkeypatch.setattr(LoginIPThrottle, "THROTTLE_RATES", rates)
    monkeypatch.setattr(LoginRateThrottle, "THROTTLE_RATES", rates)

    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"result": "error", "message": "Invalid Email or Password"}
        )
    )

    statuses = [
        client.post(
            "/api/v1/auth/login/",
            data={"email": f"victim{i}@example.com", "password": "guess"},
            content_type="application/json",
        ).status_code
        # A different account each time, so the per-account bucket never fills.
        for i in range(5)
    ]

    assert statuses[:3] == [401, 401, 401]
    assert statuses[3:] == [429, 429]


def test_forwarded_for_is_ignored_without_configured_proxies(settings):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": None}
    request = RequestFactory().get(
        "/", HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="10.0.0.5"
    )

    assert client_ip(request) == "10.0.0.5"


def test_forwarded_for_uses_the_hop_the_trusted_proxy_saw(settings):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
    request = RequestFactory().get(
        # The client prepended a fake hop; only the last one is trustworthy.
        "/",
        HTTP_X_FORWARDED_FOR="9.9.9.9, 203.0.113.7",
        REMOTE_ADDR="10.0.0.5",
    )

    assert client_ip(request) == "203.0.113.7"


def test_invalidation_survives_an_evicted_version_counter():
    namespace = client_namespace(42, "invoices")
    stale_key = build_key(namespace, "list")
    cache.set(stale_key, ["stale"], 60)

    cache.delete(f"ver:{namespace}")  # simulate eviction
    invalidate(namespace)

    assert build_key(namespace, "list") != stale_key


def test_cache_namespaces_never_collide_across_clients():
    assert build_key(client_namespace(1, "invoices"), "list") != build_key(
        client_namespace(2, "invoices"), "list"
    )
