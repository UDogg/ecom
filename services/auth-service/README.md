# auth-service

Spring Boot 3.5 + Spring Authorization Server. It is the identity provider:
sign-in, OIDC discovery, JWT issuance, JWKS, and customer registration. It
owns the `identity` schema. Design decisions: `docs/adr/011-auth-service-token-design.md`.

| Endpoint | Purpose |
|---|---|
| `GET /.well-known/openid-configuration` | OIDC discovery (issuer = `PUBLIC_AUTH_ISSUER_URL`) |
| `GET /oauth2/authorize` | Authorization code flow; PKCE (S256) required |
| `GET /login`, `POST /login` | Sign-in form (e-mail + password, CSRF-protected) |
| `POST /oauth2/token` | Code exchange |
| `GET /oauth2/jwks` | Public signing key(s) |
| `GET /userinfo` | OIDC user info |
| `POST /api/auth/register` | `{"email", "password"}` creates a **customer** (password 12 to 128 chars) |
| `GET /healthz`, `GET /readyz` | Liveness and readiness (DB check) |

Access tokens are RS256 JWTs with `sub` (account UUID), `email`, `roles`
(`customer`, `shop_owner`), `iss`, `aud=storefront`, and a lifetime of
`ACCESS_TOKEN_TTL_SECONDS`.

## Sign in from a terminal

```bash
make token ROLE=owner        # seeded owner from .env, via the real browser flow
python3 scripts/login.py --email you@example.com --password ... --json
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
