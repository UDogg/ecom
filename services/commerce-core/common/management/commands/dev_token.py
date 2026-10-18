import uuid

from django.core.management.base import BaseCommand

from common.auth import KNOWN_ROLES, issue_dev_token

# Stable ids so seeded accounts keep their carts and orders across tokens.
DEV_SUBJECTS = {
    "shop_owner": uuid.UUID("00000000-0000-4000-8000-000000000001"),
    "customer": uuid.UUID("00000000-0000-4000-8000-000000000002"),
}


class Command(BaseCommand):
    help = "Print an HS256 dev token (requires JWT_DEV_HS256_SECRET). Stand-in until auth-service exists."

    def add_arguments(self, parser):
        parser.add_argument("--role", choices=sorted(KNOWN_ROLES), default="customer")
        parser.add_argument("--sub", type=uuid.UUID, help="Subject id; defaults to the seeded account for the role")
        parser.add_argument("--ttl", type=int, default=3600)

    def handle(self, role, sub, ttl, **options):
        roles = {"customer", "shop_owner"} if role == "shop_owner" else {"customer"}
        self.stdout.write(issue_dev_token(sub or DEV_SUBJECTS[role], roles, ttl_seconds=ttl))
