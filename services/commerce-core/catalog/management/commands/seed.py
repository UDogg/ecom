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
    ("coffee", "Coffee & Tea"),
    ("audio", "Audio"),
    ("wellness", "Wellness"),
    ("bags", "Bags & Travel"),
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
    ("KNF-001", "Chef's Knife", "20 cm, Damascus steel, walnut handle.", 12900, "kitchen", 9),
    ("PAN-001", "Cast Iron Skillet", "26 cm, pre-seasoned, lasts generations.", 5499, "kitchen", 18),
    ("BRD-001", "Olive Wood Board", "Hand-finished serving and chopping board.", 4200, "kitchen", 22),
    ("SPC-001", "Spice Tin Set", "Six airtight tins with magnetic rail.", 2900, "kitchen", 30),
    ("VSE-001", "Ribbed Glass Vase", "Mouth-blown, 24 cm, smoke grey.", 3800, "home", 14),
    ("PLW-001", "Linen Cushion", "Stonewashed linen, feather insert, 50 x 50 cm.", 4900, "home", 26),
    ("CLK-001", "Walnut Desk Clock", "Silent sweep movement, solid walnut.", 6900, "home", 7),
    ("DIF-001", "Ceramic Diffuser", "Ultrasonic, warm light, 8 hour mist.", 5900, "home", 19),
    ("PLN-001", "Weekly Planner", "Undated, 12 months, linen cover.", 2400, "stationery", 55),
    ("INK-001", "Fountain Pen Ink", "50 ml, deep teal, archival.", 1800, "stationery", 42),
    ("DSK-001", "Leather Desk Mat", "Vegetable-tanned, 80 x 40 cm.", 7500, "stationery", 11),
    ("TNT-001", "Two-Person Tent", "1.6 kg, three seasons, ten-minute pitch.", 22900, "outdoor", 6),
    ("STV-001", "Backpacking Stove", "Boils a litre in three minutes.", 6400, "outdoor", 17),
    ("HMK-001", "Travel Hammock", "Parachute nylon, 300 kg rating, with straps.", 4500, "outdoor", 24),
    ("GRD-001", "Burr Coffee Grinder", "40 conical steel burrs, 30 grind settings.", 9900, "coffee", 13),
    ("DRP-001", "Pour-Over Dripper", "Porcelain, flat bottom, fits most mugs.", 2600, "coffee", 37),
    ("BNS-001", "Single-Origin Beans", "Ethiopia Guji, washed, 250 g, roasted weekly.", 1950, "coffee", 80),
    ("TEA-001", "Loose Leaf Sampler", "Eight teas, from sencha to smoked lapsang.", 3400, "coffee", 33),
    ("PRS-001", "French Press", "1 litre, double-wall steel.", 4400, "coffee", 21),
    ("HPH-001", "Wireless Headphones", "Active noise cancelling, 40 hour battery.", 24900, "audio", 10),
    ("SPK-001", "Bookshelf Speaker", "Bluetooth and optical, oak veneer, pair.", 32900, "audio", 4),
    ("EAR-001", "True Wireless Earbuds", "IPX5, wireless charging case.", 12900, "audio", 28),
    ("TRN-001", "Record Player", "Belt drive, built-in preamp, walnut plinth.", 27900, "audio", 5),
    ("YGA-001", "Cork Yoga Mat", "Natural cork and rubber, 4 mm.", 6800, "wellness", 16),
    ("RLR-001", "Massage Roller", "Grid foam, firm, 33 cm.", 2900, "wellness", 40),
    ("BTH-001", "Bath Salts", "Dead Sea salt, eucalyptus and mint, 500 g.", 1600, "wellness", 65),
    ("BCK-001", "Waxed Canvas Backpack", "22 litres, leather trim, laptop sleeve.", 15900, "bags", 12),
    ("DUF-001", "Weekender Duffel", "45 litres, carry-on size, shoe pocket.", 18900, "bags", 8),
    ("WLT-001", "Slim Card Wallet", "Full-grain leather, six cards.", 3900, "bags", 50),
    ("PKC-001", "Packing Cube Set", "Four sizes, compression zips.", 3200, "bags", 1),
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
