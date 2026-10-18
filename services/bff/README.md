# bff

Babel Bazaar's backend-for-frontend, and the **only** service users can reach
(ADR 012). Go, standard library only, distroless image.

## What happens to a request

1. Security headers. Every response sets `nosniff`, `DENY` framing, a
   no-referrer policy, `no-store`, and a deny-all CSP. HSTS is added behind
   HTTPS.
2. Honeypots: `/.env`, `/wp-login.php`, `/actuator`, `/api/commerce/...` and
   similar answer exactly like an unknown path, and log `honeypot touched`
   on the `security` channel.
3. Per-IP rate limit.
4. Route by **`internal/contract/contract.json`**. Anything not declared is
   404, and a declared path with the wrong method is 405. Path parameters must
   be UUIDs.
5. Session and role check (`public`, `customer`, `shop_owner`). The core
   decides for real; this only stops obviously wrong calls from travelling.
6. CSRF: every state change needs `X-CSRF-Token` (from `GET /api/me`) and,
   if the browser sent one, an `Origin` matching the BFF's own.
7. Validate the query and body against the contract. Unknown or invalid
   fields get a 400 that lists every problem, and the body is re-encoded so
   only validated JSON travels.
8. Refresh the access token when it is close to expiry (rotating refresh
   tokens).
9. **Sign** the upstream call (`internal/seal`) and send it with the user's
   token.
10. Shape the response to the contract's schema. Fields the core adds are
    not exposed, and a response that doesn't fit is a 502.

## Sign-in

`GET /auth/login?return_to=/path` starts the OIDC code flow with PKCE. The BFF
proxies auth-service's `GET /oauth2/authorize`, `GET` and `POST /login`
(rate limited per IP) and the login stylesheet. It refuses any upstream
redirect off its own origin. `/auth/callback` checks the state in constant
time, exchanges the code as a confidential client, verifies the token,
**rotates the session id**, and redirects. `POST /auth/logout` (CSRF) revokes
the refresh token and forgets the session.

| Route | |
|---|---|
| `GET /api/me` | `{authenticated, user, csrf_token}`. Creates an anonymous session for the CSRF token |
| `/api/...` | Everything in `contract.json` |
| `GET /healthz`, `GET /readyz` | Liveness; readiness means auth-service's signing keys are loaded |

## Try it

```bash
python3 scripts/bff.py GET "/api/catalog/products?q=mug"
python3 scripts/bff.py --role owner GET /api/admin/dashboard
make api ROLE=customer CALL="GET /api/orders"
```

## Tests

`go test ./...` covers the shared signing vectors, JWT edge cases (alg
confusion, `none`, weak keys, an auth outage), sessions (fixation, expiry),
contract loading, validation and shaping, and full flows against fake
upstreams that verify every signature they receive.
