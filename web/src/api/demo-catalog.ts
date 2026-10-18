// Generated from services/commerce-core/catalog/management/commands/seed.py (web/scripts/demo-catalog.py).
// The demo shop sells exactly what `./run.sh` seeds.
export const CATEGORIES = [
 {
  "slug": "kitchen",
  "name": "Kitchen"
 },
 {
  "slug": "home",
  "name": "Home"
 },
 {
  "slug": "stationery",
  "name": "Stationery"
 },
 {
  "slug": "outdoor",
  "name": "Outdoor"
 },
 {
  "slug": "coffee",
  "name": "Coffee & Tea"
 },
 {
  "slug": "audio",
  "name": "Audio"
 },
 {
  "slug": "wellness",
  "name": "Wellness"
 },
 {
  "slug": "bags",
  "name": "Bags & Travel"
 }
] as const;

export const PRODUCTS = [
 {
  "id": "e6fb4d50-39ae-42c3-80c9-84b1c247b939",
  "sku": "MUG-001",
  "name": "Ceramic Mug",
  "description": "350 ml, dishwasher safe.",
  "price_minor": 1299,
  "category": "kitchen",
  "stock": 40
 },
 {
  "id": "c5929fb9-5810-4eba-818a-8f22f22c9c7c",
  "sku": "MUG-002",
  "name": "Enamel Camp Mug",
  "description": "Lightweight steel mug with an enamel finish.",
  "price_minor": 1499,
  "category": "outdoor",
  "stock": 25
 },
 {
  "id": "e9857685-0c02-4fc1-83e0-0164a68cd4dc",
  "sku": "KTL-001",
  "name": "Gooseneck Kettle",
  "description": "1 litre, precise pour for coffee.",
  "price_minor": 4999,
  "category": "kitchen",
  "stock": 12
 },
 {
  "id": "d53e8243-2ac2-46be-8f28-c66e8dbe8d5e",
  "sku": "BWL-001",
  "name": "Stoneware Bowl Set",
  "description": "Four bowls, oven and microwave safe.",
  "price_minor": 3499,
  "category": "kitchen",
  "stock": 3
 },
 {
  "id": "59bcc035-8637-4d5f-89ed-b302c5a8011f",
  "sku": "CND-001",
  "name": "Soy Candle",
  "description": "Cedar and smoke, 40 hour burn.",
  "price_minor": 1899,
  "category": "home",
  "stock": 60
 },
 {
  "id": "60e00652-0908-441c-893f-6d510dd8d616",
  "sku": "THR-001",
  "name": "Wool Throw",
  "description": "Merino blend, 130 x 170 cm.",
  "price_minor": 8900,
  "category": "home",
  "stock": 8
 },
 {
  "id": "c8f6da95-60df-49e1-806f-f39eae0edfe4",
  "sku": "NTB-001",
  "name": "Dot Grid Notebook",
  "description": "A5, 160 pages, lay-flat binding.",
  "price_minor": 1599,
  "category": "stationery",
  "stock": 100
 },
 {
  "id": "1ec25c9f-845f-4d7b-8bf8-d527096b0682",
  "sku": "PEN-001",
  "name": "Brass Pen",
  "description": "Solid brass body, refillable.",
  "price_minor": 3200,
  "category": "stationery",
  "stock": 2
 },
 {
  "id": "323a1599-dbae-4fc9-84dd-2aebb246c9c5",
  "sku": "BTL-001",
  "name": "Insulated Bottle",
  "description": "750 ml, keeps cold 24 hours.",
  "price_minor": 2799,
  "category": "outdoor",
  "stock": 0
 },
 {
  "id": "e6d35c32-e720-45a3-81be-28873b53d29f",
  "sku": "LMP-001",
  "name": "Camp Lantern",
  "description": "Rechargeable, 300 lumens.",
  "price_minor": 3999,
  "category": "outdoor",
  "stock": 15
 },
 {
  "id": "397662e0-6d11-4f54-8a07-e19861a09ad1",
  "sku": "KNF-001",
  "name": "Chef's Knife",
  "description": "20 cm, Damascus steel, walnut handle.",
  "price_minor": 12900,
  "category": "kitchen",
  "stock": 9
 },
 {
  "id": "87a6322e-9e85-44fe-850f-242abc5bfe16",
  "sku": "PAN-001",
  "name": "Cast Iron Skillet",
  "description": "26 cm, pre-seasoned, lasts generations.",
  "price_minor": 5499,
  "category": "kitchen",
  "stock": 18
 },
 {
  "id": "bb713d55-447c-4994-87dc-8f628b7253ed",
  "sku": "BRD-001",
  "name": "Olive Wood Board",
  "description": "Hand-finished serving and chopping board.",
  "price_minor": 4200,
  "category": "kitchen",
  "stock": 22
 },
 {
  "id": "b39711e6-5291-48eb-82b4-e987874f6670",
  "sku": "SPC-001",
  "name": "Spice Tin Set",
  "description": "Six airtight tins with magnetic rail.",
  "price_minor": 2900,
  "category": "kitchen",
  "stock": 30
 },
 {
  "id": "c3fface4-515b-483c-83c7-5e0b5dc6c81b",
  "sku": "VSE-001",
  "name": "Ribbed Glass Vase",
  "description": "Mouth-blown, 24 cm, smoke grey.",
  "price_minor": 3800,
  "category": "home",
  "stock": 14
 },
 {
  "id": "c66c56d9-0d5e-4d29-8705-f7d95b23ac78",
  "sku": "PLW-001",
  "name": "Linen Cushion",
  "description": "Stonewashed linen, feather insert, 50 x 50 cm.",
  "price_minor": 4900,
  "category": "home",
  "stock": 26
 },
 {
  "id": "c2a3ac6c-b3f5-4a6e-831d-32f74f0d0e9d",
  "sku": "CLK-001",
  "name": "Walnut Desk Clock",
  "description": "Silent sweep movement, solid walnut.",
  "price_minor": 6900,
  "category": "home",
  "stock": 7
 },
 {
  "id": "0ae20dc5-f7bf-4616-81cf-e46a13937060",
  "sku": "DIF-001",
  "name": "Ceramic Diffuser",
  "description": "Ultrasonic, warm light, 8 hour mist.",
  "price_minor": 5900,
  "category": "home",
  "stock": 19
 },
 {
  "id": "52389f2d-919d-4326-8b64-c3fae89ea0b6",
  "sku": "PLN-001",
  "name": "Weekly Planner",
  "description": "Undated, 12 months, linen cover.",
  "price_minor": 2400,
  "category": "stationery",
  "stock": 55
 },
 {
  "id": "d6c664ca-b731-4e75-8edc-d8014bc2e3d4",
  "sku": "INK-001",
  "name": "Fountain Pen Ink",
  "description": "50 ml, deep teal, archival.",
  "price_minor": 1800,
  "category": "stationery",
  "stock": 42
 },
 {
  "id": "38d616e9-1120-4090-861c-ce1666455e65",
  "sku": "DSK-001",
  "name": "Leather Desk Mat",
  "description": "Vegetable-tanned, 80 x 40 cm.",
  "price_minor": 7500,
  "category": "stationery",
  "stock": 11
 },
 {
  "id": "41c475ff-6ac6-45d0-80ec-c08cdde3cb5f",
  "sku": "TNT-001",
  "name": "Two-Person Tent",
  "description": "1.6 kg, three seasons, ten-minute pitch.",
  "price_minor": 22900,
  "category": "outdoor",
  "stock": 6
 },
 {
  "id": "547c7cca-8ca2-439e-8899-96676edd7e85",
  "sku": "STV-001",
  "name": "Backpacking Stove",
  "description": "Boils a litre in three minutes.",
  "price_minor": 6400,
  "category": "outdoor",
  "stock": 17
 },
 {
  "id": "738f8d03-2e9b-431b-8412-5d19a3804108",
  "sku": "HMK-001",
  "name": "Travel Hammock",
  "description": "Parachute nylon, 300 kg rating, with straps.",
  "price_minor": 4500,
  "category": "outdoor",
  "stock": 24
 },
 {
  "id": "26975380-77e8-47d7-89d2-fd6a2e392d83",
  "sku": "GRD-001",
  "name": "Burr Coffee Grinder",
  "description": "40 conical steel burrs, 30 grind settings.",
  "price_minor": 9900,
  "category": "coffee",
  "stock": 13
 },
 {
  "id": "2e380e8c-205f-4e4c-80bb-22bedf4d36dd",
  "sku": "DRP-001",
  "name": "Pour-Over Dripper",
  "description": "Porcelain, flat bottom, fits most mugs.",
  "price_minor": 2600,
  "category": "coffee",
  "stock": 37
 },
 {
  "id": "b41ac4b5-c9ae-4986-8cf4-78e29b53ab26",
  "sku": "BNS-001",
  "name": "Single-Origin Beans",
  "description": "Ethiopia Guji, washed, 250 g, roasted weekly.",
  "price_minor": 1950,
  "category": "coffee",
  "stock": 80
 },
 {
  "id": "6c51dce8-df52-4bbd-8f6c-2889a1da3ea6",
  "sku": "TEA-001",
  "name": "Loose Leaf Sampler",
  "description": "Eight teas, from sencha to smoked lapsang.",
  "price_minor": 3400,
  "category": "coffee",
  "stock": 33
 },
 {
  "id": "ad9fab48-86f3-417a-8b0e-9de3f11ebafa",
  "sku": "PRS-001",
  "name": "French Press",
  "description": "1 litre, double-wall steel.",
  "price_minor": 4400,
  "category": "coffee",
  "stock": 21
 },
 {
  "id": "6cb3fe1a-9c9c-42a7-835b-0dc218e7ebb4",
  "sku": "HPH-001",
  "name": "Wireless Headphones",
  "description": "Active noise cancelling, 40 hour battery.",
  "price_minor": 24900,
  "category": "audio",
  "stock": 10
 },
 {
  "id": "ebf3f4a8-bdc9-492e-84e3-90b67429c7b3",
  "sku": "SPK-001",
  "name": "Bookshelf Speaker",
  "description": "Bluetooth and optical, oak veneer, pair.",
  "price_minor": 32900,
  "category": "audio",
  "stock": 4
 },
 {
  "id": "09eb7a4a-b6e0-4d6e-828d-9f3440fd55bf",
  "sku": "EAR-001",
  "name": "True Wireless Earbuds",
  "description": "IPX5, wireless charging case.",
  "price_minor": 12900,
  "category": "audio",
  "stock": 28
 },
 {
  "id": "d3b2f080-b891-4c1a-8929-395e5e72a805",
  "sku": "TRN-001",
  "name": "Record Player",
  "description": "Belt drive, built-in preamp, walnut plinth.",
  "price_minor": 27900,
  "category": "audio",
  "stock": 5
 },
 {
  "id": "252d9df1-ffdd-45ea-8a3d-7a599853c3ef",
  "sku": "YGA-001",
  "name": "Cork Yoga Mat",
  "description": "Natural cork and rubber, 4 mm.",
  "price_minor": 6800,
  "category": "wellness",
  "stock": 16
 },
 {
  "id": "84ca86be-e700-451f-8faf-e943d1e05457",
  "sku": "RLR-001",
  "name": "Massage Roller",
  "description": "Grid foam, firm, 33 cm.",
  "price_minor": 2900,
  "category": "wellness",
  "stock": 40
 },
 {
  "id": "49b06871-05c3-4bf2-8978-c7ee6df53dcd",
  "sku": "BTH-001",
  "name": "Bath Salts",
  "description": "Dead Sea salt, eucalyptus and mint, 500 g.",
  "price_minor": 1600,
  "category": "wellness",
  "stock": 65
 },
 {
  "id": "40db6dda-55c0-4ae2-85b4-6170ec38014b",
  "sku": "BCK-001",
  "name": "Waxed Canvas Backpack",
  "description": "22 litres, leather trim, laptop sleeve.",
  "price_minor": 15900,
  "category": "bags",
  "stock": 12
 },
 {
  "id": "3da10eb1-5e5f-4f4d-8f4e-4a195a885601",
  "sku": "DUF-001",
  "name": "Weekender Duffel",
  "description": "45 litres, carry-on size, shoe pocket.",
  "price_minor": 18900,
  "category": "bags",
  "stock": 8
 },
 {
  "id": "8b6df76b-08d8-4059-8e19-e59302b38227",
  "sku": "WLT-001",
  "name": "Slim Card Wallet",
  "description": "Full-grain leather, six cards.",
  "price_minor": 3900,
  "category": "bags",
  "stock": 50
 },
 {
  "id": "25461e65-325b-40be-868e-a2303d588d6a",
  "sku": "PKC-001",
  "name": "Packing Cube Set",
  "description": "Four sizes, compression zips.",
  "price_minor": 3200,
  "category": "bags",
  "stock": 1
 }
];
