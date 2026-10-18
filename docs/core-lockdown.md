# The core services answer only the BFF

Nobody can `curl` or use Postman against commerce-core, auth-service,
catalog-reader or intelligence, with a stolen token or without one. See
`request-signing.md` for the signature, and ADR 012 for the reasoning.

## Layers, outermost first

| Layer | Control | Where |
|---|---|---|
| Network | Only the BFF has a host port. `app` and `data` are `internal: true`: no host route and no egress | `docker-compose.yml` |
| Container | Read-only filesystem, `cap_drop: ALL`, `no-new-privileges`, non-root, distroless for Go | `docker-compose.yml`, Dockerfiles |
| BFF edge | Contract allow-list, honeypots, per-IP, per-user and per-route rate limits, CSRF and Origin checks, body size caps, security headers, strict CSP on the sign-in pages | `services/bff` |
| Gateway guard | Signature, 60 s window, single-use nonce; empty 403 before routing or parsing | each core service |
| Authentication | RS256 JWT re-verified (issuer, audience, expiry) with cached JWKS | each core service |
| Authorization | Role per route, ownership per object (someone else's order is a 404) | each core service |
| Database | One role per service: catalog-reader sees two views, intelligence two aggregate views, the publisher two outbox columns, the worker its own schema | `db/init`, migrations |

## Open doors (no signature needed)

- `/healthz` and `/readyz` on every service, for container probes. They
  reveal nothing.
- auth-service's `/oauth2/jwks`: public keys, which resource services fetch.
- auth-service's `/api/auth/service-token`: the super-credential sign-in. It
  checks its own credentials and is reachable only from the private network.

## The only other way in

A verified **SYSTEM** token (service account) or **break-glass** token, both
with audience `core` (`break-glass.md`). Every such request is logged at
WARNING by the service it reaches.

Everything else, including auth-service's token endpoint and discovery
document, needs the BFF's signature.

## Fail closed

- Every core service refuses to start without `GATEWAY_SECRET` (32+
  characters). `GATEWAY_ALLOW_UNGUARDED=true` is the local and test opt-out,
  and it logs a warning.
- The BFF refuses to start with a short `GATEWAY_SECRET` or
  `OIDC_CLIENT_SECRET`, or with an invalid contract.
- If a core response does not fit the contract, the BFF answers 502 and logs
  the mismatch. It never passes the response through.

## Prove it

```bash
./run.sh
curl -m 3 localhost:8000/healthz         # commerce-core: no route from the host
./run.sh api --role owner GET /api/admin/dashboard     # through the BFF: 200
./run.sh breakglass system GET commerce /api/commerce/ops/status   # SYSTEM, from the private network: 200
docker compose --profile core exec worker python -c \
  "import urllib.request; urllib.request.urlopen('http://commerce-core:8000/api/commerce/orders')"
# inside the network, unsigned: HTTP Error 403 with an empty body
```

## Not covered yet

- **Mutual TLS** between the BFF and the cores.
- **Shared session and nonce stores** for multiple BFF replicas.
- **Secret rotation** without a dual-secret window means a short outage.
