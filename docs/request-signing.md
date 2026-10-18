# How a request reaches a core service

Adopted from Ensuretech's design (ADR 012). Every call from the BFF to a core
service carries **two separate proofs**, and a core service needs both.

| Question | Proof | Checked by |
|---|---|---|
| Did this call come through the BFF? | A **signature** made with a secret only the BFF and the core services share | The gateway guard in each core service, before anything is parsed or any database is touched |
| Who is the user, and what may they do? | The user's **access token** (RS256 JWT from auth-service) | The BFF first, then again by the core service |

## The signature (v1)

```text
X-Gateway-Timestamp  unix seconds
X-Gateway-Nonce      24 random hex characters (16 to 64 accepted), never reused
X-Gateway-Signature  hex HMAC-SHA256(secret,
                       "v1" LF timestamp LF nonce LF METHOD LF path LF query LF hex(sha256(Authorization header)))
```

- `METHOD` is upper-cased. `path` and `query` are the **raw** request target,
  exactly as sent (no decoding, no reordering). An empty Authorization header
  hashes the empty string.
- The signature is good for one method, path, query and user token, for 60
  seconds, once. A replayed nonce is refused. The nonce is remembered only
  after the signature verifies, so strangers cannot fill the cache, and a
  full cache refuses rather than forgets.
- The secret (`GATEWAY_SECRET`, 32+ characters, the same value on the BFF and
  on the cores) never travels.
- The format is **byte-compatible with Ensuretech's** `GatewaySeal`.
  `docs/contracts/gateway-signing.vectors.json` was checked against
  Ensuretech's own PHP signer.

## Implementations (change them together)

| Language | Where | Verified against the vectors by |
|---|---|---|
| Go (signer) | `services/bff/internal/seal` | `seal_test.go` |
| Go (guard) | `services/catalog-reader/internal/gate` | `gate_test.go` |
| Python, Django | `services/commerce-core/common/gateway.py` | `tests/test_gateway.py` |
| Python, FastAPI | `services/intelligence/app/gateway.py` | `tests/test_gateway.py` |
| Java, Spring | `services/auth-service/.../GatewayGuardFilter.java` | `GatewayGuardTest.java` |

## What a core service does with a request, in order

1. **Gateway guard.** Recompute the HMAC and compare it in constant time,
   check the 60-second window, and reject a reused nonce. On any failure it
   answers an **empty 403** (no body, no hint). The only exceptions are
   explicitly listed open doors: health probes, and auth-service's public
   JWKS.
2. **Authentication.** Verify the JWT again with auth-service's public keys:
   RS256, issuer, expiry. A core service can check tokens but never mint
   them.
3. **Authorization.** Role rules per route, and object-level checks
   (customer A cannot read customer B's order: 404). These live in the core
   service, so a mistake in the BFF cannot leak data.

## Fail closed

A core service **refuses to start** without `GATEWAY_SECRET` (32+
characters). `GATEWAY_ALLOW_UNGUARDED=true` is the explicit
local-development and test opt-out, and it logs a warning on every start.

## What it does not do

- The signature proves origin and integrity, **not secrecy**. BFF-to-core
  traffic is plain HTTP on a private network. Add mutual TLS before it
  crosses any network you don't control.
- Anyone holding `GATEWAY_SECRET` can act as the BFF. Keep it in a secret
  manager and rotate it (rotation needs a short dual-secret window, which is
  not implemented yet).
- Nonce caches are per process. With several replicas, a captured request
  could be replayed once per replica inside its 60-second window. It is
  still pinned to one method, path, query, and token.
