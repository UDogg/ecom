import signal
import time

from django.core.management.base import BaseCommand

from orders.payments import reconcile
from orders.services import expire_reservations, purge_idempotency_keys


class Command(BaseCommand):
    help = "Background upkeep: expire unpaid orders, reconcile payments, purge old idempotency keys."

    def add_arguments(self, parser):
        parser.add_argument("--loop", type=float, default=0, help="repeat every N seconds (0: once)")

    def handle(self, loop, **options):
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(True))
        while True:
            try:
                expired = expire_reservations()
                payments = reconcile()
                purged = purge_idempotency_keys()
                if expired or payments["settled"] or payments["refunded"] or purged:
                    self.stdout.write(f"expired {expired} orders; payments settled {payments['settled']}, "
                                      f"refunded {payments['refunded']}; purged {purged} idempotency keys")
            except Exception as exc:  # noqa: BLE001 - keep the loop alive through a database restart
                self.stderr.write(f"maintenance round failed: {exc}")
            if not loop or stop:
                return
            for _ in range(int(loop * 10)):
                if stop:
                    return
                time.sleep(0.1)
