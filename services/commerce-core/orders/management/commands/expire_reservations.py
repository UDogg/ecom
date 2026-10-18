from django.core.management.base import BaseCommand

from orders.services import expire_reservations, purge_idempotency_keys


class Command(BaseCommand):
    help = "Expire unpaid orders past their reservation TTL, release stock, and purge old idempotency keys."

    def handle(self, **options):
        expired = expire_reservations()
        purged = purge_idempotency_keys()
        self.stdout.write(f"expired {expired} orders, purged {purged} idempotency keys")
