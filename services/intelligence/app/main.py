"""intelligence: product recommendations, never on the checkout path.

Reads two aggregate views (commerce.intel_product_popularity and
commerce.intel_copurchase) through the `intelligence` role, which can see
nothing else: no customers, no orders, no payment data. Work is bounded by a
concurrency cap, so a burst of recommendation traffic sheds load (503) rather
than queueing up; the BFF then serves its degraded answer.
"""

import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from .gateway import GatewayGuard
from .tokens import JWKSCache, PrivilegedTokens

log = logging.getLogger("intelligence")

UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

POPULAR = sql.SQL("""
    SELECT product_id::text, sku, name, price_minor, currency, units
    FROM {schema}.intel_product_popularity
    WHERE NOT (product_id = ANY(%(exclude)s::uuid[]))
    ORDER BY units DESC, name
    LIMIT %(limit)s
""")

CO_PURCHASE = sql.SQL("""
    SELECT p.product_id::text, p.sku, p.name, p.price_minor, p.currency, c.together
    FROM {schema}.intel_copurchase c
    JOIN {schema}.intel_product_popularity p ON p.product_id = c.other_id
    WHERE c.product_id = %(product)s::uuid
    ORDER BY c.together DESC, p.units DESC, p.name
    LIMIT %(limit)s
""")


def item(row):
    return {"product_id": row[0], "sku": row[1], "name": row[2], "price_minor": row[3], "currency": row[4],
            "score": row[5]}


class Recommender:
    def __init__(self, pool, schema="commerce", max_concurrent=4):
        self.pool, self.schema = pool, sql.Identifier(schema)
        self.slots = threading.BoundedSemaphore(max_concurrent)

    def recommend(self, product_id, limit):
        if not self.slots.acquire(blocking=False):
            raise HTTPException(status_code=503, detail="busy")
        try:
            with self.pool.connection(timeout=2) as conn:
                picked, strategy = [], "popular"
                if product_id:
                    picked = [item(r) for r in conn.execute(CO_PURCHASE.format(schema=self.schema),
                                                            {"product": product_id, "limit": limit})]
                    if picked:
                        strategy = "co_purchase"
                if len(picked) < limit:
                    exclude = [p["product_id"] for p in picked] + ([product_id] if product_id else [])
                    picked += [item(r) for r in conn.execute(POPULAR.format(schema=self.schema),
                                                             {"exclude": exclude, "limit": limit - len(picked)})]
                return {"items": picked, "strategy": strategy, "degraded": False}
        finally:
            self.slots.release()


def create_app(pool=None, recommender=None, gateway_secret=None, allow_unguarded=None, privileged=None):
    @asynccontextmanager
    async def lifespan(app):
        yield
        if pool is not None:
            pool.close()

    if pool is None and recommender is None:
        conninfo = make_conninfo(host=os.environ.get("POSTGRES_HOST", "postgres"),
                                 port=os.environ.get("POSTGRES_PORT", "5432"),
                                 dbname=os.environ.get("POSTGRES_DB", "shop"),
                                 user=os.environ.get("INTELLIGENCE_DB_USER", "intelligence"),
                                 password=os.environ.get("INTELLIGENCE_DB_PASSWORD", ""),
                                 connect_timeout=5, options="-c default_transaction_read_only=on -c statement_timeout=2000")
        pool = ConnectionPool(conninfo, min_size=1, max_size=8, open=False)
        pool.open(wait=False)
    recommender = recommender or Recommender(pool, os.environ.get("INTELLIGENCE_DB_SCHEMA", "commerce"),
                                             int(os.environ.get("MAX_CONCURRENT_INFERENCES", "4")))

    app = FastAPI(title="intelligence", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "service": "intelligence"}

    @app.get("/readyz")
    def readyz():
        try:
            with recommender.pool.connection(timeout=2) as conn:
                conn.execute("SELECT 1")
        except Exception:  # noqa: BLE001 - any failure means not ready
            return JSONResponse({"status": "unavailable", "postgres": "down"}, status_code=503)
        return {"status": "ready", "postgres": "ok"}

    @app.get("/api/intelligence/recommendations")
    def recommendations(product_id: Annotated[str | None, Query(pattern=UUID_PATTERN)] = None,
                        limit: Annotated[int, Query(ge=1, le=20)] = 6):
        return recommender.recommend(product_id.lower() if product_id else None, limit)

    @app.exception_handler(HTTPException)
    def http_problem(request, exc):
        return JSONResponse({"type": "https://shop.local/errors/" + ("busy" if exc.status_code == 503 else "error"),
                             "title": "Busy" if exc.status_code == 503 else "Error", "status": exc.status_code,
                             "detail": str(exc.detail)}, status_code=exc.status_code,
                            media_type="application/problem+json")

    @app.exception_handler(RequestValidationError)
    def invalid(request, exc):
        errors = {".".join(str(p) for p in e["loc"][1:]) or "request": e["msg"] for e in exc.errors()}
        return JSONResponse({"type": "https://shop.local/errors/validation", "title": "Invalid request", "status": 400,
                             "detail": "One or more fields are invalid.", "errors": errors}, status_code=400,
                            media_type="application/problem+json")

    secret = os.environ.get("GATEWAY_SECRET", "") if gateway_secret is None else gateway_secret
    unguarded = (os.environ.get("GATEWAY_ALLOW_UNGUARDED", "") == "true") if allow_unguarded is None else allow_unguarded
    if privileged is None:
        privileged = PrivilegedTokens(JWKSCache(os.environ.get("INTERNAL_AUTH_JWKS_URL", "http://auth-service:8081/oauth2/jwks")),
                                      issuer=os.environ.get("PUBLIC_AUTH_ISSUER_URL", "http://localhost:8080"))
    return GatewayGuard(app, secret, unguarded, privileged=privileged)
