"""
Typed view over the ``WHMCS`` settings dict.

The whole point: the WHMCS host/port/path/credentials are described in exactly
one place, sourced from ``.env``, and nothing else in the project builds a URL
or knows a secret.
"""

from dataclasses import dataclass
from urllib.parse import urlunsplit

from django.conf import settings

from .exceptions import WhmcsConfigError

_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True, slots=True)
class WhmcsSettings:
    scheme: str = "https"
    host: str = ""
    port: int | None = None
    base_path: str = ""
    api_path: str = "/includes/api.php"
    host_header: str = ""
    identifier: str = ""
    secret: str = ""
    access_key: str = ""
    admin_username: str = ""
    admin_password: str = ""
    timeout: float = 15.0
    connect_timeout: float = 5.0
    max_retries: int = 2
    retry_backoff: float = 0.4
    verify_ssl: bool = True
    ca_bundle: str = ""
    max_connections: int = 20
    log_payloads: bool = False

    @classmethod
    def from_django(cls) -> "WhmcsSettings":
        raw = getattr(settings, "WHMCS", {}) or {}
        return cls(
            scheme=raw.get("SCHEME", "https"),
            host=raw.get("HOST", ""),
            port=raw.get("PORT"),
            base_path=raw.get("BASE_PATH", ""),
            api_path=raw.get("API_PATH", "/includes/api.php"),
            host_header=raw.get("HOST_HEADER", ""),
            identifier=raw.get("IDENTIFIER", ""),
            secret=raw.get("SECRET", ""),
            access_key=raw.get("ACCESS_KEY", ""),
            admin_username=raw.get("ADMIN_USERNAME", ""),
            admin_password=raw.get("ADMIN_PASSWORD", ""),
            timeout=float(raw.get("TIMEOUT", 15.0)),
            connect_timeout=float(raw.get("CONNECT_TIMEOUT", 5.0)),
            max_retries=int(raw.get("MAX_RETRIES", 2)),
            retry_backoff=float(raw.get("RETRY_BACKOFF", 0.4)),
            verify_ssl=bool(raw.get("VERIFY_SSL", True)),
            ca_bundle=raw.get("CA_BUNDLE", ""),
            max_connections=int(raw.get("MAX_CONNECTIONS", 20)),
            log_payloads=bool(raw.get("LOG_PAYLOADS", False)),
        )

    # -- derived ----------------------------------------------------------

    @property
    def netloc(self) -> str:
        if self.port and self.port != _DEFAULT_PORTS.get(self.scheme):
            return f"{self.host}:{self.port}"
        return self.host

    @property
    def endpoint(self) -> str:
        """Full URL of ``api.php``, e.g. https://1.2.3.4:8443/whmcs/includes/api.php"""
        path = f"{self.base_path}{self.api_path}"
        if not path.startswith("/"):
            path = "/" + path
        return urlunsplit((self.scheme, self.netloc, path, "", ""))

    @property
    def verify(self):
        """Value for httpx' ``verify`` argument."""
        if self.ca_bundle:
            return self.ca_bundle
        return self.verify_ssl

    @property
    def default_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": "whmcs-bridge/1.0",
            "Accept": "application/json",
        }
        if self.host_header:
            # Talking to a bare IP behind a name-based vhost.
            headers["Host"] = self.host_header
        return headers

    def credentials(self) -> dict[str, str]:
        """
        Auth fields appended to every request body.

        Preferred: API credentials (identifier/secret). Legacy admin
        username/password is supported for installs that cannot mint them.
        ``accesskey`` is optional and matches ``$api_access_key`` in
        configuration.php - it lets a non-whitelisted IP through.
        """
        if self.identifier and self.secret:
            creds = {"identifier": self.identifier, "secret": self.secret}
        elif self.admin_username and self.admin_password:
            # Pre-7.2 scheme, kept only for legacy installs: WHMCS expects the
            # MD5 hash of the admin password, not the password itself. Prefer
            # API credentials - an admin password in .env is full admin access
            # if the file ever leaks.
            import hashlib

            creds = {
                "username": self.admin_username,
                "password": hashlib.md5(  # noqa: S324 - required by the WHMCS legacy scheme
                    self.admin_password.encode()
                ).hexdigest(),
            }
        else:  # pragma: no cover - guarded by validate()
            raise WhmcsConfigError("No WHMCS credentials configured.")
        if self.access_key:
            creds["accesskey"] = self.access_key
        return creds

    def validate(self) -> None:
        problems: list[str] = []
        if not self.host:
            problems.append("WHMCS_HOST is empty")
        if self.scheme not in ("http", "https"):
            problems.append("WHMCS_SCHEME must be http or https")
        has_api_creds = bool(self.identifier and self.secret)
        has_admin_creds = bool(self.admin_username and self.admin_password)
        if not (has_api_creds or has_admin_creds):
            problems.append(
                "set WHMCS_IDENTIFIER/WHMCS_SECRET (or WHMCS_ADMIN_USERNAME/PASSWORD)"
            )
        if self.scheme == "http" and not settings.DEBUG:
            problems.append("refusing plaintext http to WHMCS outside DEBUG")
        if problems:
            raise WhmcsConfigError("Invalid WHMCS configuration: " + "; ".join(problems))


SENSITIVE_FIELDS = frozenset(
    {
        "identifier",
        "secret",
        "accesskey",
        "password",
        "password2",
        "username",
        "credit_card_number",
        "cvv",
    }
)


def redact(payload: dict) -> dict:
    """Copy of ``payload`` safe to write into a log line."""
    return {
        key: ("***" if key.lower() in SENSITIVE_FIELDS else value)
        for key, value in payload.items()
    }


def get_settings() -> WhmcsSettings:
    return WhmcsSettings.from_django()
