"""Client account: login validation, profile read/update, registration."""

import logging

from apps.core.cache import client_namespace, get_or_set
from apps.core.exceptions import ApplicationError

from ..actions import Action
from ..exceptions import WhmcsAPIError, WhmcsValidationError
from ..normalizers import collection, iso_date, pick, text, to_bool, to_int, to_money
from .base import BaseService

logger = logging.getLogger(__name__)

_PROFILE_MAP = {
    "whmcs_client_id": ("id", to_int),
    "first_name": ("firstname", text),
    "last_name": ("lastname", text),
    "company": ("companyname", text),
    "email": ("email", text),
    "address1": ("address1", text),
    "address2": ("address2", text),
    "city": ("city", text),
    "state": ("state", text),
    "postcode": ("postcode", text),
    "country": ("countrycode", text),
    "phone": ("phonenumber", text),
    "status": ("status", text),
    "currency_code": ("currency_code", text),
    "credit": ("credit", to_money),
    "email_opt_out": ("emailoptout", to_bool),
    "two_factor_enabled": ("twofaenabled", to_bool),
    "created_at": ("datecreated", iso_date),
}

#: Profile fields a customer is allowed to change about themselves.
EDITABLE_PROFILE_FIELDS = {
    "first_name": "firstname",
    "last_name": "lastname",
    "company": "companyname",
    "address1": "address1",
    "address2": "address2",
    "city": "city",
    "state": "state",
    "postcode": "postcode",
    "country": "countrycode",
    "phone": "phonenumber",
}


class AccountService(BaseService):
    cache_resources = ("profile",)

    # -- authentication ---------------------------------------------------

    def validate_login(self, email: str, password: str) -> dict:
        """
        Verify end-user credentials against WHMCS.

        The password only ever travels bridge -> WHMCS over TLS and is never
        stored locally: this backend holds no password hashes for customers.
        Returns the WHMCS client id and 2FA flag.
        """
        try:
            data = self.call(
                Action.VALIDATE_LOGIN, {"email": email, "password2": password}
            )
        except WhmcsValidationError as exc:
            # WHMCS says "Invalid Email or Password" - surface as a clean 401
            # without distinguishing which half was wrong.
            logger.info("login rejected for %s: %s", _mask_email(email), exc)
            raise InvalidCredentials() from exc

        return {
            "whmcs_client_id": to_int(data.get("userid")),
            "password_hash": text(data.get("passwordhash")),
            "two_factor_enabled": to_bool(data.get("twoFactorEnabled")),
        }

    # -- profile ----------------------------------------------------------

    def get_profile(self, whmcs_client_id: int) -> dict:
        def fetch() -> dict:
            data = self.call(
                Action.GET_CLIENTS_DETAILS,
                {"clientid": whmcs_client_id, "stats": False},
            )
            return pick(data.get("client") or data, _PROFILE_MAP)

        return get_or_set(
            client_namespace(whmcs_client_id, "profile"), "client", fetch, "detail"
        )

    def update_profile(self, whmcs_client_id: int, changes: dict) -> dict:
        params = {
            whmcs_field: changes[our_field]
            for our_field, whmcs_field in EDITABLE_PROFILE_FIELDS.items()
            if our_field in changes
        }
        if not params:
            raise ApplicationError("No editable fields supplied.", code="nothing_to_update")

        params["clientid"] = whmcs_client_id
        self.call(Action.UPDATE_CLIENT, params)
        self.invalidate_for(whmcs_client_id, "profile")
        return self.get_profile(whmcs_client_id)

    def change_password(
        self, whmcs_client_id: int, email: str, current_password: str, new_password: str
    ) -> None:
        """Re-authenticate before writing: possession of a JWT is not enough to
        change the password behind it."""
        verified = self.validate_login(email, current_password)

        # The email comes from the local mirror while the target comes from the
        # session. If WHMCS ever reassigns that email to another client the two
        # can drift apart, and proving knowledge of one account's password would
        # rewrite another's. Refuse unless they are the same client.
        if verified["whmcs_client_id"] != int(whmcs_client_id):
            logger.warning(
                "password change refused: session client %s, credentials belong to %s",
                whmcs_client_id,
                verified["whmcs_client_id"],
            )
            raise InvalidCredentials()

        self.call(
            Action.UPDATE_CLIENT,
            {"clientid": whmcs_client_id, "password2": new_password},
        )
        self.invalidate_for(whmcs_client_id, "profile")

    def get_contacts(self, whmcs_client_id: int) -> list[dict]:
        data = self.call(Action.GET_CONTACTS, {"userid": whmcs_client_id})
        return [
            pick(
                raw,
                {
                    "id": ("id", to_int),
                    "first_name": ("firstname", text),
                    "last_name": ("lastname", text),
                    "email": ("email", text),
                    "phone": ("phonenumber", text),
                },
            )
            for raw in collection(data, "contacts", "contact")
        ]

    # -- registration -----------------------------------------------------

    def register(self, payload: dict) -> int:
        """
        Create a WHMCS client. Returns the new client id.

        ``payload`` is already validated by the serializer; we only translate
        field names, so no unexpected key can reach WHMCS.
        """
        params = {
            "firstname": payload["first_name"],
            "lastname": payload["last_name"],
            "email": payload["email"],
            "password2": payload["password"],
            "address1": payload.get("address1", ""),
            "city": payload.get("city", ""),
            "state": payload.get("state", ""),
            "postcode": payload.get("postcode", ""),
            "country": payload.get("country", ""),
            "phonenumber": payload.get("phone", ""),
            "companyname": payload.get("company", ""),
            "clientip": payload.get("client_ip", ""),
            "noemail": payload.get("skip_welcome_email", False),
            "skipvalidation": False,
        }
        data = self.call(Action.ADD_CLIENT, params)
        return to_int(data.get("clientid"))

    def find_client_id_by_email(self, email: str) -> int | None:
        try:
            data = self.call(Action.GET_CLIENTS, {"search": email, "limitnum": 1})
        except WhmcsAPIError:
            return None
        clients = collection(data, "clients", "client")
        return to_int(clients[0].get("id")) if clients else None


class InvalidCredentials(ApplicationError):
    default_code = "invalid_credentials"
    status_code = 401

    def __init__(self) -> None:
        super().__init__("Email or password is incorrect.")


def _mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:2]}***@{domain}" if domain else "***"


def namespace_for(whmcs_client_id: int) -> str:
    return client_namespace(whmcs_client_id, "profile")
