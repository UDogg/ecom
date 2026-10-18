-- Machine identities (client credentials). Secrets are 384-bit random and stored
-- only as SHA-256 hashes; several credentials may be live during a rotation.
CREATE TABLE service_account (
    id           uuid        PRIMARY KEY,
    name         text        NOT NULL UNIQUE CHECK (name ~ '^[a-z][a-z0-9-]{1,40}$'),
    client_id    text        NOT NULL UNIQUE,
    active       boolean     NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz
);

CREATE TABLE service_credential (
    id          uuid        PRIMARY KEY,
    account_id  uuid        NOT NULL REFERENCES service_account (id) ON DELETE CASCADE,
    secret_hash char(64)    NOT NULL CHECK (secret_hash ~ '^[0-9a-f]{64}$'),
    created_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at  timestamptz,
    UNIQUE (account_id, secret_hash)
);

-- Every break-glass attempt, granted or refused. Append-only: the application
-- cannot edit or delete history (the triggers refuse), so a grant nobody can
-- explain stays visible.
CREATE TABLE break_glass_grant (
    id             uuid        PRIMARY KEY,
    attempted_at   timestamptz NOT NULL DEFAULT now(),
    outcome        text        NOT NULL CHECK (outcome IN ('GRANTED', 'NOT_SYSTEM', 'WRONG_PASSWORD', 'BAD_REQUEST',
                                                           'NO_SUCH_USER', 'THROTTLED')),
    client_id      text        NOT NULL DEFAULT '',
    target_user_id uuid,
    reason         text        NOT NULL DEFAULT '',
    jti            text        NOT NULL DEFAULT '',
    expires_at     timestamptz,
    remote_addr    text        NOT NULL DEFAULT ''
);
CREATE INDEX break_glass_grant_recent ON break_glass_grant (attempted_at DESC);

CREATE FUNCTION break_glass_grant_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'break_glass_grant is append-only';
END $$;

CREATE TRIGGER break_glass_grant_no_update_delete BEFORE UPDATE OR DELETE ON break_glass_grant
    FOR EACH ROW EXECUTE FUNCTION break_glass_grant_append_only();
CREATE TRIGGER break_glass_grant_no_truncate BEFORE TRUNCATE ON break_glass_grant
    FOR EACH STATEMENT EXECUTE FUNCTION break_glass_grant_append_only();
