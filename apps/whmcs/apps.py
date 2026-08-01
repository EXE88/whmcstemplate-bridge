import logging

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


class WhmcsConfig(AppConfig):
    name = "apps.whmcs"
    label = "whmcs"

    def ready(self) -> None:
        """Validate the upstream configuration at boot.

        In production a misconfigured bridge should refuse to start rather than
        fail on the first customer request; in DEBUG we only warn so a fresh
        clone can run migrations before ``.env`` is filled in.
        """
        from .config import WhmcsSettings
        from .exceptions import WhmcsConfigError

        try:
            WhmcsSettings.from_django().validate()
        except WhmcsConfigError as exc:
            if settings.DEBUG:
                logger.warning("%s", exc)
            else:
                raise
