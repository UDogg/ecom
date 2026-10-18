from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import Category, Product
from catalog.services import create_product
from common.management.commands.dev_token import DEV_SUBJECTS

CATEGORIES = [
    ("kitchen", "Kitchen"),
    ("home", "Home"),
    ("stationery", "Stationery"),
    ("outdoor", "Outdoor"),
]

PRODUCTS = [
    ("MUG-001", "Ceramic Mug", "350 ml, dishwasher safe.", 1299, "kitchen", 40),
    ("MUG-002", "Enamel Camp Mug", "Lightweight steel mug with an enamel finish.", 1499, "outdoor", 25),
    ("KTL-001", "Gooseneck Kettle", "1 litre, precise pour for coffee.", 4999, "kitchen", 12),
    ("BWL-001", "Stoneware Bowl Set", "Four bowls, oven and microwave safe.", 3499, "kitchen", 3),
    ("CND-001", "Soy Candle", "Cedar and smoke, 40 hour burn.", 1899, "home", 60),
    ("THR-001", "Wool Throw", "Merino blend, 130 x 170 cm.", 8900, "home", 8),
    ("NTB-001", "Dot Grid Notebook", "A5, 160 pages, lay-flat binding.", 1599, "stationery", 100),
    ("PEN-001", "Brass Pen", "Solid brass body, refillable.", 3200, "stationery", 2),
    ("BTL-001", "Insulated Bottle", "750 ml, keeps cold 24 hours.", 2799, "outdoor", 0),
    ("LMP-001", "Camp Lantern", "Rechargeable, 300 lumens.", 3999, "outdoor", 15),
]


class Command(BaseCommand):
    help = "Load the demo catalog. Idempotent: existing SKUs are left untouched."

    @transaction.atomic
    def handle(self, **options):
        for slug, name in CATEGORIES:
            Category.objects.get_or_create(slug=slug, defaults={"name": name})
        created = 0
        for sku, name, description, price, category, stock in PRODUCTS:
            if Product.objects.filter(sku=sku).exists():
                continue
            create_product({
                "sku": sku, "name": name, "description": description, "price_minor": price,
                "currency": "USD", "category": category, "initial_stock": stock, "status": "active",
            }, actor_id=DEV_SUBJECTS["shop_owner"])
            created += 1
        self.stdout.write(f"seeded {created} products ({len(PRODUCTS) - created} already present)")
