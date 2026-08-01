"""The transport is the only place that speaks HTTP - test it directly."""

import httpx
import pytest
import respx

from apps.whmcs.actions import Action
from apps.whmcs.client import get_client
from apps.whmcs.config import get_settings, redact
from apps.whmcs.exceptions import (
    WhmcsAuthError,
    WhmcsConfigError,
    WhmcsNotFound,
    WhmcsTransportError,
)
from apps.whmcs.transport import flatten

from .conftest import WHMCS_ENDPOINT


def test_endpoint_is_built_from_env():
    assert get_settings().endpoint == WHMCS_ENDPOINT


def test_non_default_port_is_included(settings):
    settings.WHMCS = {**settings.WHMCS, "PORT": 8443, "BASE_PATH": "/whmcs"}
    assert get_settings().endpoint == "https://whmcs.test:8443/whmcs/includes/api.php"


def test_flatten_nested_params():
    assert flatten({"a": {"b": 1}, "c": [10, 20], "d": True, "e": None}) == {
        "a[b]": "1",
        "c[0]": "10",
        "c[1]": "20",
        "d": "1",
    }


def test_credentials_are_redacted():
    assert redact({"secret": "s3cr3t", "action": "GetInvoices"}) == {
        "secret": "***",
        "action": "GetInvoices",
    }


@respx.mock
def test_successful_call_sends_credentials_and_action():
    route = respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"result": "success", "totalresults": 0})
    )

    get_client().call(Action.GET_INVOICES, {"userid": 42})

    body = dict(pair.split("=", 1) for pair in route.calls[0].request.content.decode().split("&"))
    assert body["action"] == "GetInvoices"
    assert body["identifier"] == "test-identifier"
    assert body["responsetype"] == "json"


@respx.mock
def test_error_result_is_classified():
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"result": "error", "message": "Client Not Found"})
    )
    with pytest.raises(WhmcsNotFound):
        get_client().call(Action.GET_CLIENTS_DETAILS, {"clientid": 1})


@respx.mock
def test_invalid_ip_is_an_auth_error():
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"result": "error", "message": "Invalid IP 1.2.3.4"})
    )
    with pytest.raises(WhmcsAuthError):
        get_client().call(Action.WHMCS_DETAILS)


@respx.mock
def test_error_envelope_on_a_403_is_still_classified_as_auth():
    """A non-whitelisted IP answers 403 *with* the WHMCS error envelope - it must
    not be mistaken for a transport failure and retried."""
    route = respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            403, json={"result": "error", "message": "Invalid IP 203.0.113.9"}
        )
    )

    with pytest.raises(WhmcsAuthError):
        get_client().call(Action.WHMCS_DETAILS)

    assert route.call_count == 1


@respx.mock
def test_html_response_becomes_transport_error():
    respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, text="<html>502</html>"))
    with pytest.raises(WhmcsTransportError):
        get_client().call(Action.WHMCS_DETAILS)


def test_action_allowlist_blocks_unknown_and_forbidden_actions():
    with pytest.raises(WhmcsConfigError):
        get_client().call("DeleteClient", {"clientid": 1})
    with pytest.raises(WhmcsConfigError):
        get_client().call("MadeUpAction")
