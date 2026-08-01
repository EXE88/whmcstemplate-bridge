import pytest
from django.contrib.auth import get_user_model

from apps.whmcs.client import reset_clients

User = get_user_model()

WHMCS_ENDPOINT = "https://whmcs.test/includes/api.php"


@pytest.fixture(autouse=True)
def whmcs_settings(settings):
    settings.WHMCS = {
        **settings.WHMCS,
        "SCHEME": "https",
        "HOST": "whmcs.test",
        "PORT": 443,
        "BASE_PATH": "",
        "API_PATH": "/includes/api.php",
        "IDENTIFIER": "test-identifier",
        "SECRET": "test-secret",
        "MAX_RETRIES": 0,
    }
    reset_clients()
    yield
    reset_clients()


@pytest.fixture(autouse=True)
def clear_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def customer(db):
    return User.objects.link_to_whmcs("customer@example.com", 42)


@pytest.fixture
def auth_client(customer):
    from rest_framework.test import APIClient

    from apps.accounts.serializers import issue_tokens

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(customer)['access']}")
    return client
