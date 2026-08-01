"""
``python manage.py whmcs_ping``

Verifies that the .env points at a reachable WHMCS install and that the API
credentials are accepted - the first thing to run after deploying to a new
server (and the quickest way to spot an IP that is not whitelisted).
"""

import time

from django.core.management.base import BaseCommand, CommandError

from apps.whmcs.actions import Action
from apps.whmcs.client import get_client
from apps.whmcs.config import get_settings
from apps.whmcs.exceptions import WhmcsAuthError, WhmcsError


class Command(BaseCommand):
    help = "Check connectivity and credentials against the configured WHMCS install."

    def handle(self, *args, **options):
        config = get_settings()
        config.validate()

        self.stdout.write(f"endpoint : {config.endpoint}")
        auth_mode = "identifier/secret" if config.identifier else "admin user"
        self.stdout.write(f"auth     : {auth_mode}")
        self.stdout.write(f"verify   : {config.verify}")

        started = time.perf_counter()
        try:
            data = get_client().call(Action.WHMCS_DETAILS)
        except WhmcsAuthError as exc:
            raise CommandError(
                f"Credentials rejected: {exc}\n"
                "Check WHMCS_IDENTIFIER/WHMCS_SECRET, the admin role's 'API Access' "
                "permission, and whether this server's IP is whitelisted "
                "(or set WHMCS_ACCESS_KEY)."
            ) from exc
        except WhmcsError as exc:
            raise CommandError(f"WHMCS unreachable: {exc}") from exc

        elapsed = (time.perf_counter() - started) * 1000
        whmcs = data.get("whmcs", {})
        self.stdout.write(
            self.style.SUCCESS(
                f"OK in {elapsed:.0f}ms - WHMCS {whmcs.get('version', '?')} "
                f"({whmcs.get('systemurl', '')})"
            )
        )
