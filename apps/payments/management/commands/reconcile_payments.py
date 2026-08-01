"""
``python manage.py reconcile_payments``

Closes the only gap the checkout flow can leave behind: a payment Zibal
confirmed but WHMCS was never told about, because WHMCS was down or slow at the
moment the customer returned. Those rows sit at status ``paid`` instead of
``recorded`` - the customer has been charged and their invoice still looks
unpaid.

Also re-verifies attempts the customer abandoned mid-flow: a browser that never
came back does not mean the card was not charged.

Safe to run repeatedly (cron every 15 minutes is reasonable); every path it
takes is idempotent.
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.payments.models import PaymentAttempt, PaymentStatus
from apps.payments.services import PaymentService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Record verified-but-unrecorded payments, and re-check abandoned ones."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-age-hours",
            type=int,
            default=72,
            help="How far back to look (default: 72).",
        )
        parser.add_argument(
            "--include-abandoned",
            action="store_true",
            help="Also re-verify attempts left at 'redirected'.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(hours=options["max_age_hours"])
        service = PaymentService()

        statuses = [PaymentStatus.PAID]
        if options["include_abandoned"]:
            statuses.append(PaymentStatus.REDIRECTED)

        pending = PaymentAttempt.objects.filter(
            status__in=statuses, created_at__gte=since
        ).exclude(track_id="")

        self.stdout.write(f"{pending.count()} attempt(s) to reconcile since {since:%Y-%m-%d %H:%M}")

        recorded = failed = 0
        for attempt in pending:
            label = f"{attempt.id} invoice={attempt.invoice_id} track={attempt.track_id}"
            if options["dry_run"]:
                self.stdout.write(f"  would settle {label} (currently {attempt.status})")
                continue
            try:
                settled = service.settle(attempt.track_id)
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the run
                failed += 1
                self.stderr.write(self.style.WARNING(f"  {label}: {exc}"))
                logger.exception("reconciliation failed for %s", attempt.id)
                continue

            if settled.status == PaymentStatus.RECORDED:
                recorded += 1
                self.stdout.write(self.style.SUCCESS(f"  recorded {label}"))
            else:
                self.stdout.write(f"  {label} -> {settled.status}")

        if not options["dry_run"]:
            summary = f"recorded {recorded}, still failing {failed}"
            style = self.style.SUCCESS if not failed else self.style.WARNING
            self.stdout.write(style(summary))
