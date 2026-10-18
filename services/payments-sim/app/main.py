"""payments-sim: a local stand-in for a card payment provider (README sections 4 and 9).

Only service accounts can call it, each for its own narrow purpose:

    commerce (commerce-core)   POST /api/payments/charges, GET /api/payments/charges/{key},
                               POST /api/payments/charges/{key}/refunds
    ops (operators)            GET/PUT /api/payments/chaos  (only when CHAOS_ENABLED=true)

It sends webhooks to commerce-core with its own service account, at least
once, versioned so they can be applied in any order.

Card tokens: tok_visa and tok_mastercard succeed; tok_declined and
tok_insufficient fail; tok_timeout succeeds at the provider but answers too
late, so the caller times out and has to reconcile. Chaos modes apply to
everything: decline_all, slow, timeout_after_capture, duplicate_webhooks,
out_of_order.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Body, FastAPI, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from . import store as ledger
from .gateway import GatewayGuard
from .service_token import ServiceToken
from .tokens import JWKSCache, PrivilegedTokens
from .webhooks import Deliverer

log = logging.getLogger("payments")
KEY = r"^[A-Za-z0-9_:-]{8,100}$"
TIMEOUT_DELAY_SECONDS = 6  # longer than commerce-core's provider timeout


class ChargeIn(BaseModel):
    idempotency_key: str = Field(pattern=KEY)
    order_ref: str = Field(min_length=1, max_length=64)
    amount_minor: int = Field(gt=0, le=10**12)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    source: Literal["tok_visa", "tok_mastercard", "tok_declined", "tok_insufficient", "tok_timeout"]


class RefundIn(BaseModel):
    idempotency_key: str = Field(pattern=KEY)
    amount_minor: int = Field(gt=0, le=10**12)


class ChaosIn(BaseModel):
    mode: Literal["none", "decline_all", "slow", "timeout_after_capture", "duplicate_webhooks", "out_of_order"]
    latency_ms: int = Field(default=0, ge=0, le=10_000)


def problem(status, slug, detail, **extra):
    return JSONResponse({"type": f"https://shop.local/errors/{slug}", "title": slug.replace("-", " ").capitalize(),
                         "status": status, "detail": detail, **extra}, status_code=status,
                        media_type="application/problem+json")


def require_service(request: Request, *allowed):
    claims = getattr(request.state, "principal", None) or request.scope.get("state", {}).get("principal")
    service = (claims or {}).get("service")
    roles = (claims or {}).get("roles") or []
    if not claims or "system" not in roles or service not in allowed:
        raise HTTPException(status_code=403, detail="not allowed for this caller")
    return service


def create_app(pool=None, store=None, deliverer=None, gateway_secret=None, allow_unguarded=None, privileged=None,
               chaos_enabled=None, start_webhooks=True):
    schema = os.environ.get("PAYMENTS_DB_SCHEMA", "payments")
    if pool is None:
        pool = ConnectionPool(make_conninfo(
            host=os.environ.get("POSTGRES_HOST", "postgres"), port=os.environ.get("POSTGRES_PORT", "5432"),
            dbname=os.environ.get("POSTGRES_DB", "shop"), user=os.environ.get("PAYMENTS_DB_USER", "payments_sim"),
            password=os.environ.get("PAYMENTS_DB_PASSWORD", ""), connect_timeout=5),
            min_size=1, max_size=int(os.environ.get("PAYMENTS_DB_POOL", "8")), kwargs={"row_factory": dict_row},
            open=False)
    store = store or ledger.Store(pool, schema)
    chaos_enabled = (os.environ.get("CHAOS_ENABLED", "") == "true") if chaos_enabled is None else chaos_enabled

    @asynccontextmanager
    async def lifespan(app):
        if pool.closed:
            pool.open(wait=True, timeout=30)
        with pool.connection() as conn:
            ledger.ensure_schema(conn, schema)
        stop = None
        if start_webhooks:
            d = deliverer or Deliverer(pool, schema, ServiceToken(
                os.environ.get("PAYMENTS_SERVICE_CLIENT_ID", ""), os.environ.get("PAYMENTS_SERVICE_CLIENT_SECRET", "")))
            stop = d.start()
        yield
        if stop:
            stop.set()
        pool.close()

    app = FastAPI(title="payments-sim", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.exception_handler(HTTPException)
    def http_error(request, exc):
        return problem(exc.status_code, "forbidden" if exc.status_code == 403 else "error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    def invalid(request, exc):
        return problem(400, "validation", "One or more fields are invalid.",
                       errors={".".join(str(p) for p in e["loc"][1:]) or "body": e["msg"] for e in exc.errors()})

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "service": "payments-sim"}

    @app.get("/readyz")
    def readyz():
        try:
            with pool.connection(timeout=2) as conn:
                conn.execute("SELECT 1")
        except Exception:  # noqa: BLE001
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    @app.post("/api/payments/charges")
    async def create_charge(request: Request, body: ChargeIn):
        require_service(request, "commerce")
        mode, latency = await asyncio.to_thread(store.chaos)
        try:
            charge, created = await asyncio.to_thread(store.create_charge, body.idempotency_key, body.order_ref,
                                                      body.amount_minor, body.currency, body.source)
        except ledger.Conflict as exc:
            return problem(409, "idempotency-conflict", str(exc))
        if mode == "slow" and latency:
            await asyncio.sleep(latency / 1000)
        if created and (body.source == "tok_timeout" or mode == "timeout_after_capture"):
            # The charge is done at the provider; the caller just will not hear about it in time.
            await asyncio.sleep(TIMEOUT_DELAY_SECONDS)
        log.info("charge %s %s %s %d %s", charge["id"], charge["status"], body.idempotency_key, body.amount_minor,
                 body.currency)
        return JSONResponse(charge, status_code=201 if created else 200)

    @app.get("/api/payments/charges/{key}")
    def get_charge(request: Request, key: Annotated[str, Path(pattern=KEY)]):
        require_service(request, "commerce")
        try:
            return store.get_charge(key)
        except ledger.NotFound:
            return problem(404, "not-found", "No charge with that idempotency key.")

    @app.post("/api/payments/charges/{key}/refunds")
    def refund(request: Request, key: Annotated[str, Path(pattern=KEY)], body: RefundIn):
        require_service(request, "commerce")
        try:
            return store.refund(key, body.idempotency_key, body.amount_minor)
        except ledger.NotFound:
            return problem(404, "not-found", "No charge with that idempotency key.")
        except ledger.Conflict as exc:
            return problem(409, "refund-refused", str(exc))

    @app.get("/api/payments/chaos")
    def get_chaos(request: Request):
        require_service(request, "ops")
        mode, latency = store.chaos()
        return {"enabled": chaos_enabled, "mode": mode, "latency_ms": latency}

    @app.put("/api/payments/chaos")
    def set_chaos(request: Request, body: Annotated[ChaosIn, Body()]):
        require_service(request, "ops")
        if not chaos_enabled:
            return problem(403, "chaos-disabled", "Chaos controls are off (CHAOS_ENABLED=false).")
        store.set_chaos(body.mode, body.latency_ms)
        log.warning("CHAOS mode set to %s (%d ms)", body.mode, body.latency_ms)
        return {"enabled": True, "mode": body.mode, "latency_ms": body.latency_ms}

    secret = os.environ.get("GATEWAY_SECRET", "") if gateway_secret is None else gateway_secret
    unguarded = (os.environ.get("GATEWAY_ALLOW_UNGUARDED", "") == "true") if allow_unguarded is None else allow_unguarded
    if privileged is None:
        privileged = PrivilegedTokens(JWKSCache("http://auth-service:8081/oauth2/jwks"),
                                      issuer=os.environ.get("PUBLIC_AUTH_ISSUER_URL", "http://localhost:8080"))
    return GatewayGuard(app, secret, unguarded, privileged=privileged)

