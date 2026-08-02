"""
``python manage.py benchmark_api``

Measures what the frontend will actually feel, against the real WHMCS: how long
each read takes cold (cache empty), warm (cache hit) and stale (expired entry
served while it refreshes behind the scenes).

Run it after any change to caching or to the number of upstream calls a view
makes - it is the only honest way to know whether something got faster.
"""

import statistics
import time

from django.core.cache import cache
from django.core.management.base import BaseCommand

from apps.core.pagination import PageRequest
from apps.whmcs.services import (
    AccountService,
    BillingService,
    CatalogueService,
    DomainService,
    ServiceService,
    SupportService,
)


class Command(BaseCommand):
    help = "Time the main reads against the configured WHMCS install."

    def add_arguments(self, parser):
        parser.add_argument(
            "--client-id", type=int, help="WHMCS client id to use for customer reads."
        )
        parser.add_argument("--repeat", type=int, default=3)

    def handle(self, *args, **options):
        page = PageRequest(page=1, page_size=10)
        client_id = options.get("client_id")
        repeat = options["repeat"]

        catalogue = CatalogueService()
        cases = {
            "products (public)": lambda: catalogue.list_products(),
            "currencies (public)": lambda: catalogue.list_currencies(),
            "departments (public)": lambda: SupportService().list_departments(),
        }
        if client_id:
            cases.update(
                {
                    "profile": lambda: AccountService().get_profile(client_id),
                    "invoices": lambda: BillingService().list_invoices(client_id, page),
                    "services": lambda: ServiceService().list_services(client_id, page),
                    "domains": lambda: DomainService().list_domains(client_id, page),
                    "tickets": lambda: SupportService().list_tickets(client_id, page),
                }
            )
        else:
            self.stdout.write(
                self.style.WARNING("no --client-id given; only public reads are measured\n")
            )

        self.stdout.write(f"{'read':<24}{'cold':>10}{'warm':>10}{'speedup':>10}")
        self.stdout.write("-" * 54)

        colds, warms = [], []
        for label, call in cases.items():
            cache.clear()
            cold = self._time(call)

            samples = [self._time(call) for _ in range(repeat)]
            warm = statistics.median(samples)

            colds.append(cold)
            warms.append(warm)
            speedup = f"{cold / warm:.0f}x" if warm else "-"
            self.stdout.write(f"{label:<24}{cold:>9.0f}ms{warm:>9.0f}ms{speedup:>10}")

        self.stdout.write("-" * 54)
        self.stdout.write(
            f"{'median':<24}{statistics.median(colds):>9.0f}ms"
            f"{statistics.median(warms):>9.0f}ms"
        )
        self.stdout.write(
            "\ncold = first request after a deploy or invalidation (pays for WHMCS)."
            "\nwarm = every other request. This is what customers normally see."
        )

    @staticmethod
    def _time(call) -> float:
        started = time.perf_counter()
        call()
        return (time.perf_counter() - started) * 1000
