# ADR 012: A BFF in front, every core service gated

**Status:** Accepted. Adopted from Ensuretech's architecture (its README
"Security model", `docs/request-signing.md`, `docs/core-lockdown.md`).

## Context

Until now each service published a port and accepted any caller holding a
valid token. A stolen token, a misrouted request, or a port scan reached
business logic directly. Ensuretech solves this with two independent proofs on
every call. Babel Bazaar adopts the same model, extended to its Go and FastAPI
services.

## Decision

1. **One public entry point: the BFF** (`services/bff`, Go, standard library
   only). It is the only container with a host port. Browsers hold an opaque
   session cookie. Access and refresh tokens never leave the BFF.
2. **Every core service refuses anything the BFF did not sign.** The
   signature is HMAC-SHA256 over the method, raw path, raw query, a hash of
   the user's token, a timestamp and a single-use nonce. Each service checks
   it with its own guard before routing or parsing anything: Django
   (commerce-core), Spring (auth-service), Go (catalog-reader) and FastAPI
   (intelligence). A refusal is an empty 403. The wire format is
   byte-compatible with Ensuretech's `GatewaySeal`, which
   `docs/contracts/gateway-signing.vectors.json` proves (checked against
   Ensuretech's PHP signer).
3. **The core services still verify the user themselves.** They check the
   RS256 JWT (issuer, audience `bff`, expiry) against cached JWKS, then
   roles, then object-level ownership. The BFF's checks are a first line, not
   the authority.
4. **The API contract lives in the BFF** (`internal/contract/routes.go`, Go code
   compiled in). It is an allow-list of endpoints, typed query and body
   fields (unknown fields are refused), and response schemas that copy only
   the listed fields. Public paths are decoupled from core paths
   (`/api/cart` maps to commerce-core's `/api/commerce/cart`). The contract
   holds representation only, never business logic.
5. **Sign-in also goes through the BFF.** The BFF is a confidential OIDC
   client (secret, PKCE, rotating refresh tokens). It proxies exactly four
   auth-service routes: `GET /oauth2/authorize`, `GET` and `POST /login`, and
   the login page's stylesheet. The issuer users see is the BFF's origin, so
   auth-service has no public port either.
6. **Fail closed.** Every guard and the BFF refuse to start without a 32+
   character secret. The BFF refuses an invalid contract. The BFF answers a
   core service's contract violation with 502, never a guess.
7. **Network.** The `app` and `data` Compose networks are `internal: true`
   (no host access, no egress). Containers run read-only, with no
   capabilities and `no-new-privileges`.

## Same as Ensuretech

The signature format, the 60-second window, single-use nonces (remembered
only after verification; a full cache refuses rather than forgets), empty
403s, re-verification in every core, the contract allow-list with response
shaping, honeypots that answer like any unknown path and raise a silent
alert, rate limits, server-side tokens, and CSRF on state changes.

## Different from Ensuretech

| Ensuretech | Babel Bazaar | Why |
|---|---|---|
| Laravel/PHP BFF | Go BFF, standard library only | No third-party code on the internet-facing edge; small static binary in a distroless image |
| Guards in PHP, Java, Python (Django) | Guards in Java, Python (Django **and** FastAPI) and Go | The polyglot core: Go `catalog-reader`, FastAPI `intelligence` |
| BFF signs in with the identity service's password API | Full OIDC code flow, BFF as confidential client, auth pages proxied | auth-service is OIDC (ADR 011); keeps auth-service fully gated |
| Unknown body fields dropped | Unknown body fields **refused** (400) | Clients learn their request was wrong; no silent drift |
| SYSTEM tokens and break-glass | **Same model** (`docs/break-glass.md`), with the password and service secrets stored only as SHA-256 hashes, privileged tokens restricted to audience `core`, and an `opsbox` container as the private-network entry | Operators can work while the BFF is down, every grant audited |
| Emergency mitigations file | **Not yet** | The contract is compiled in; mitigations need their own review process |

## Consequences

- `curl` or Postman against a core service gets an empty 403, even with a
  stolen token. Operators debug through the BFF, or, from the private
  network, with SYSTEM or break-glass tokens (`docs/break-glass.md`).
- Anyone holding `GATEWAY_SECRET` can act as the BFF. Keep it in a secret
  manager and rotate it.
- Sessions and nonce caches are in memory: one BFF replica (or sticky
  sessions) until a shared store is added. Nonces are per core process.
- The signature proves origin and integrity, not secrecy. Add mutual TLS
  before BFF-to-core traffic leaves a private network.
