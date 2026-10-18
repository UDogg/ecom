# Break glass: working without the BFF

Adopted from Ensuretech (its `docs/break-glass.md` and `docs/core-lockdown.md`).
The core services refuse every request the BFF did not sign
(`core-lockdown.md`), except for two kinds of token. Both are minted only by
auth-service and always carry audience `core`, which no BFF token ever has.

| | SYSTEM token | Break-glass token |
|---|---|---|
| Who gets it | A **service account** (client id plus a 384-bit secret, stored only as a SHA-256 hash) | A SYSTEM-token holder **who also knows the break-glass password** |
| From | `POST /api/auth/service-token` (client credentials) | `POST /api/auth/break-glass` |
| Acts as | Nobody: `sub=svc:<client_id>`, `roles=["system"]`, no email | **One named, existing, enabled user**, with that user's real roles |
| Can | SYSTEM-only ops endpoints (`/api/commerce/ops/*`, the audit trail) | Exactly what that user could do through the BFF; object rules still apply |
| Cannot | Any customer or shop-owner route; break glass without the password | Ops endpoints; minting another break-glass token; anything with refresh |
| Lifetime | `SERVICE_TOKEN_SECONDS` (10 min) | `BREAK_GLASS_MINUTES` (15 min, never more than 60); no refresh token |
| Recorded | Every issue logged; `last_used_at` per account | **Every attempt**, granted or refused, in `identity.break_glass_grant` (append-only), and every request made with it is logged at WARNING by the service it reaches |

The break-glass endpoint **does not exist (404)** unless
`BREAK_GLASS_PASSWORD_SHA256` is set. It is rate limited per IP and per
service account. It needs a reason of at least 10 characters, and compares the
password in constant time.

A person's token can never become privileged by claiming it. The `system` role
is honoured only on a `svc:` subject, `break_glass` only with audience `core`,
and only auth-service can sign either.

## How an operator gets in

The core services have no host ports, so being "on the private network" means
running in the `opsbox` container on the `app` network: our equivalent of
Ensuretech's VPN. `scripts/breakglass.sh` (or `./run.sh breakglass`) runs it
for you and passes the secrets from `.env.private` as environment variables,
never as arguments.

```bash
./run.sh breakglass system GET commerce /api/commerce/ops/status        # SYSTEM: outbox, orders, reservations
./run.sh breakglass system POST commerce /api/commerce/ops/reservations/expire
./run.sh breakglass users meera                                         # find the user id to act as
./run.sh breakglass call USER_ID "BFF down, finishing a refund" GET commerce /api/commerce/orders
./run.sh breakglass token USER_ID "why"                                 # just the token
./run.sh breakglass audit                                               # who asked, for whom, and what happened
```

SERVICE is one of `auth`, `commerce`, `catalog` or `intelligence`.

## Where the secrets live

`./run.sh` makes them on first run:

| Secret | Plaintext | What the services see |
|---|---|---|
| `ops` service account secret | `.env.private` (mode 600, git-ignored) | `SERVICE_ACCOUNTS=ops=<client_id>:<sha256>` |
| Break-glass password | `.env.private` | `BREAK_GLASS_PASSWORD_SHA256` |

Rotation: put a second hash after a `|` in `SERVICE_ACCOUNTS`
(`ops=svc_x:<new>|<old>`), roll it out, then drop the old one. On start,
auth-service revokes every credential that is no longer listed, and disables
accounts that are no longer declared. For break glass, replace the hash and
restart.

## Before this goes anywhere real

- Generate the secrets in a secret manager. Rotate the break-glass password
  after every use.
- Decide who may hold the service-account secret: break glass needs it as
  well as the password.
- Review `identity.break_glass_grant` regularly (`./run.sh breakglass audit`).
  A grant nobody can explain is an incident.
- Keep the private network private: `opsbox`'s reach is the whole point, so
  treat access to the Docker host like VPN access.

## Tests

- auth-service `PrivilegedAccessTest`: issuing, overlapping rotation, generic
  failures, a grant's claims and lifetime, every audited refusal, no
  chaining, append-only, and that user tokens are never privileged.
  `BreakGlassDisabledTest`: the endpoint is 404 when off. `GatewayGuardTest`:
  admission.
- commerce-core `test_privileged.py`, catalog-reader `cmd/server/main_test.go`
  and `internal/gate`, intelligence `test_intelligence.py`: each guard admits
  SYSTEM and break-glass tokens, and refuses user tokens, wrong audiences, the
  `system` role on a person, and expired tokens.
