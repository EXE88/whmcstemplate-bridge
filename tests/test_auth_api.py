import httpx
import pytest
import respx
from django.contrib.auth import get_user_model

from .conftest import WHMCS_ENDPOINT

pytestmark = pytest.mark.django_db
User = get_user_model()


@respx.mock
def test_login_creates_the_local_mirror_and_returns_tokens(client):
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "success",
                "userid": 42,
                "passwordhash": "x",
                "twoFactorEnabled": False,
            },
        )
    )

    response = client.post(
        "/api/v1/auth/login/",
        data={"email": "customer@example.com", "password": "correct-horse"},
        content_type="application/json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access"] and body["refresh"]
    assert body["user"]["whmcs_client_id"] == 42

    user = User.objects.get(email="customer@example.com")
    assert user.whmcs_client_id == 42
    # The customer password is never stored locally.
    assert not user.has_usable_password()


@respx.mock
def test_bad_credentials_return_401_without_detail(client):
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"result": "error", "message": "Invalid Email or Password"}
        )
    )

    response = client.post(
        "/api/v1/auth/login/",
        data={"email": "customer@example.com", "password": "wrong"},
        content_type="application/json",
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_profile_requires_authentication(client):
    assert client.get("/api/v1/auth/me/").status_code == 401
