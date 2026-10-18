# auth-service

Spring Boot 3.5 + Spring Authorization Server. It is the identity provider:
sign-in, OIDC discovery, JWT issuance, JWKS, and customer registration. It
owns the `identity` schema. Design decisions: `docs/adr/011-auth-service-token-design.md`
and `docs/adr/012-bff-and-gated-core.md`.

**It has no public port.** `GatewayGuardFilter` runs before Spring Security
and refuses anything the BFF did not sign. The BFF proxies the sign-in pages,
and its origin is the issuer. The only OAuth client is the BFF (confidential,
PKCE, rotating refresh tokens). Only `/healthz`, `/readyz`, `/oauth2/jwks` and
`/api/auth/service-token` are open. SYSTEM and break-glass tokens pass the
guard; see `docs/break-glass.md`.

| Endpoint | Purpose |
|---|---|
| `GET /.well-known/openid-configuration` | OIDC discovery (issuer = `PUBLIC_AUTH_ISSUER_URL`) |
| `GET /oauth2/authorize` | Authorization code flow; PKCE (S256) required |
| `GET /login`, `POST /login` | Sign-in form (e-mail + password, CSRF-protected) |
| `POST /oauth2/token` | Code exchange |
| `GET /oauth2/jwks` | Public signing key(s) |
| `GET /userinfo` | OIDC user info |
| `POST /api/auth/register` | `{"email", "password"}` creates a **customer** (password 12 to 128 chars) |
| `POST /api/auth/service-token` | Client credentials for a **SYSTEM** token (service accounts; an open door on the private network) |
| `POST /api/auth/break-glass` | SYSTEM token + break-glass password: a 15-minute token acting as one user; every attempt audited; 404 when switched off |
| `GET /api/auth/ops/break-glass-grants` | The audit trail (SYSTEM only) |
| `GET /healthz`, `GET /readyz` | Liveness and readiness (DB check) |

Access tokens are RS256 JWTs with `sub` (account UUID), `email`, `roles`
(`customer`, `shop_owner`), `iss` (the BFF's origin), `aud=bff`, and a
lifetime of `ACCESS_TOKEN_TTL_SECONDS`.

## Sign in from a terminal

Through the BFF (tokens stay there; you get a session):

```bash
python3 scripts/bff.py --role owner GET /api/me
```

## Tests

They need PostgreSQL with an `auth_test` database (`createdb auth_test`):

```bash
AUTH_TEST_DB_PASSWORD=... ./mvnw test     # or: make test-auth
```

They cover discovery, JWKS (public parts only), the full code and PKCE
exchange with claim checks, single-use codes, PKCE enforcement, redirect URI
allow-listing, form login and CSRF, registration (validation, duplicates, no
role escalation), and signing-key persistence.
