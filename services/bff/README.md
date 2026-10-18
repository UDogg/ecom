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
4. Route by the **compiled-in route table**, `internal/contract/routes.go`:
   one explicit `net/http` pattern per path, no catch-all proxy, fixed
   upstream paths. Anything not declared is 404, a declared path with the
   wrong method is 405, and path parameters must be UUIDs. Changing the API
   means changing Go code and shipping a new binary; nothing on a running host
   can widen it. The only way around the BFF is break-glass
   (`docs/break-glass.md`), which is audited and short-lived.
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

Public catalog reads (`Cache:` in the route table) are micro-cached for one to
five seconds and identical concurrent requests share one upstream call
(`internal/server/cache.go`): only anonymous, public GETs qualify, which the
contract checks at start-up.

## The storefront

The image also carries the storefront (`web/`, built in the Dockerfile's first
stage). `internal/web` loads it into memory at start with its precompressed
Brotli and gzip variants and strong ETags: fingerprinted `/assets/*` are cached
for a year, `index.html` is revalidated, any other path that is not an API or
sign-in route gets the app (client-side routing). Pages get a strict CSP
(`script-src 'self'`, no inline code). One origin serves the app, the API and
sign-in, so there is no CORS at all.

At start the BFF sets Go's memory limit to 90% of the container's cgroup limit;
`GOMAXPROCS` already follows the CPU quota, so the process uses what the
instance gives it and scales with it.

## Sign-in

`GET /auth/login?return_to=/path` starts the OIDC code flow with PKCE. The BFF
proxies auth-service's `GET /oauth2/authorize`, `GET` and `POST /login`
(rate limited per IP) and the sign-in page's stylesheet (`/auth-ui.css`); the page
is auth-service's own, styled like the storefront, with no scripts or inline styles. It refuses any upstream
redirect off its own origin. `/auth/callback` checks the state in constant
time, exchanges the code as a confidential client, verifies the token,
**rotates the session id**, and redirects. `POST /auth/logout` (CSRF) revokes
the refresh token and forgets the session.

| Route | |
|---|---|
| `GET /api/me` | `{authenticated, user, csrf_token}`. Creates an anonymous session for the CSRF token |
| `/api/...` | Everything in `internal/contract/routes.go` |
| everything else | The storefront (`WEB_ROOT`) |
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
contract validation at boot, request validation and shaping, routing (404/405, UUID ids), the micro-cache, the static site, and full flows against fake
upstreams that verify every signature they receive.
