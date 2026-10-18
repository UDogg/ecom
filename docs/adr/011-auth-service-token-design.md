# ADR 011: auth-service token design

**Status:** Accepted (Phase 4 slice, built alongside Phase 3). Points 1 and 6
are amended by ADR 012: the only client is now the BFF (confidential,
PKCE, rotating refresh tokens), and the issuer is the BFF's origin.

## Decisions

1. **Spring Authorization Server, authorization code + PKCE only.** The
   storefront is a public client: it has no secret, and PKCE is required.
   There is no password grant (OAuth 2.1 removed it). Scripts sign in
   through the same browser flow (`scripts/login.py`).
2. **`sub` is the account's UUID, not its e-mail.** Orders reference
   `customer_id = sub`, so changing an e-mail never orphans history. Access
   tokens also carry `email` (for order e-mails) and `roles`.
3. **The signing key is persisted** (`AUTH_SIGNING_KEY_PATH`, on a volume).
   A key generated in memory would change on every restart and invalidate
   every token already issued.
4. **Resource services validate locally** against cached JWKS
   (`commerce-core/common/auth.py`). They refresh every 5 minutes, refetch
   early when they see an unknown `kid` (at most every 30 seconds), and
   **keep using the last good keys when auth-service is unreachable**. An
   auth outage therefore stops new sign-ins but not existing sessions
   (README section 8.4).
5. **Authorizations are stored in PostgreSQL** (Spring Authorization
   Server's JDBC tables in the `identity` schema), so codes survive
   restarts and replicas share them. A scheduled job purges expired rows.
6. **The registered client is configuration, not data.** Redirect URIs,
   the client id, and the token TTL come from the environment (README
   section 6).
7. **Self-registration creates customers only.** Shop owners come from seed
   configuration (`AUTH_SEED_ENABLED`, dev only) or, later, from an
   operator tool.

## Not yet

Refresh tokens for the browser (public clients get none by default), login
throttling and lockout, MFA, and key rotation tooling. These belong to
Phase 5 (security).
