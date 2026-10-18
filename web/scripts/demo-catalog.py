#!/usr/bin/env python3
"""Regenerate src/api/demo-catalog.ts from the commerce-core seed, so the demo sells what ./run.sh seeds.

    python3 web/scripts/demo-catalog.py          (from the repository root; CI checks the file is current)
"""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "services/commerce-core/catalog/management/commands/seed.py"
OUT = ROOT / "web/src/api/demo-catalog.ts"


def uid(s):
    h = hashlib.sha256(s.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-8{h[17:20]}-{h[20:32]}"


vals = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(SEED.read_text()).body
        if isinstance(n, ast.Assign) and n.targets[0].id in ("CATEGORIES", "PRODUCTS")}
cats = [{"slug": s, "name": n} for s, n in vals["CATEGORIES"]]
prods = [{"id": uid(sku), "sku": sku, "name": name, "description": d, "price_minor": p, "category": c, "stock": st}
         for sku, name, d, p, c, st in vals["PRODUCTS"]]
OUT.write_text("// Generated from services/commerce-core/catalog/management/commands/seed.py (web/scripts/demo-catalog.py).\n"
               "// The demo shop sells exactly what `./run.sh` seeds.\n"
               f"export const CATEGORIES = {json.dumps(cats, indent=1)} as const;\n\n"
               f"export const PRODUCTS = {json.dumps(prods, indent=1)};\n")
