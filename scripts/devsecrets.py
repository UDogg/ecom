#!/usr/bin/env python3
"""Per-machine secrets for ./run.sh. Standard library only.

    python3 scripts/devsecrets.py ensure

* No .env yet: create it from .env.example with fresh random values for every
  secret, so no two machines share the public `dev-only` defaults.
* .env exists: only add keys it is missing (an existing database keeps its
  passwords).
* Privileged access (docs/break-glass.md): make the `ops` service account and
  the break-glass password if they do not exist. Their plaintext goes only to
  .env.private (mode 0600, git-ignored); .env gets the SHA-256 hashes the
  services are configured with.

Prints one line per change. Exit code 3 means a new .env was created.
"""

import hashlib
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV, EXAMPLE, PRIVATE = ROOT / ".env", ROOT / ".env.example", ROOT / ".env.private"

# Rotated on a fresh .env. Everything else keeps the example's value.
SECRETS = {
    "POSTGRES_SUPERUSER_PASSWORD": 24, "COMMERCE_DB_PASSWORD": 24, "CATALOG_READER_DB_PASSWORD": 24,
    "INTELLIGENCE_DB_PASSWORD": 24, "AUTH_DB_PASSWORD": 24, "OUTBOX_PUBLISHER_DB_PASSWORD": 24,
    "WORKER_DB_PASSWORD": 24, "RABBITMQ_PASSWORD": 24, "S3_SECRET_KEY": 24, "DJANGO_SECRET_KEY": 48,
    "OIDC_CLIENT_SECRET": 32, "GATEWAY_SECRET": 32, "SEED_OWNER_PASSWORD": 12, "SEED_CUSTOMER_PASSWORD": 12,
}
LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def token(nbytes):
    return secrets.token_urlsafe(nbytes)  # [A-Za-z0-9_-]: safe in .env files and URLs


def sha256(value):
    return hashlib.sha256(value.encode()).hexdigest()


def read(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = LINE.match(line.strip())
            if m:
                values[m.group(1)] = m.group(2).split(" #")[0].strip()
    return values


def set_values(path, updates):
    """Rewrite KEY= lines in place, append missing keys; keep comments and order."""
    lines = path.read_text().splitlines() if path.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        m = LINE.match(line.strip())
        if m and m.group(1) in updates:
            lines[i] = f"{m.group(1)}={updates[m.group(1)]}"
            seen.add(m.group(1))
    missing = [k for k in updates if k not in seen]
    if missing:
        lines += ["", "# added by ./run.sh"] + [f"{k}={updates[k]}" for k in missing]
    path.write_text("\n".join(lines) + "\n")


def write_private(values):
    PRIVATE.write_text(
        "# Operator secrets for scripts/breakglass.sh. Plaintext: never commit, never share.\n"
        "# The services only ever see their SHA-256 hashes (in .env).\n"
        + "".join(f"{k}={v}\n" for k, v in values.items()))
    os.chmod(PRIVATE, 0o600)


def ensure():
    created = False
    if not ENV.exists():
        lines = []
        for line in EXAMPLE.read_text().splitlines():
            m = LINE.match(line.strip())
            if m and m.group(1) in SECRETS:
                line = f"{m.group(1)}={token(SECRETS[m.group(1)])}"
            lines.append(line)
        ENV.write_text("\n".join(lines) + "\n")
        os.chmod(ENV, 0o600)
        print("created .env with fresh random secrets")
        created = True
    else:
        have, example = read(ENV), read(EXAMPLE)
        added = {k: (token(SECRETS[k]) if k in SECRETS else v) for k, v in example.items() if k not in have}
        if added:
            set_values(ENV, added)
            print(f"added to .env: {', '.join(added)}")

    private, env = read(PRIVATE), read(ENV)
    if not (private.get("OPS_CLIENT_ID") and private.get("OPS_CLIENT_SECRET") and private.get("BREAK_GLASS_PASSWORD")):
        private = {"OPS_CLIENT_ID": f"svc_ops_{secrets.token_hex(6)}", "OPS_CLIENT_SECRET": token(48),
                   "BREAK_GLASS_PASSWORD": token(18)}
        write_private(private)
        print("generated the `ops` service account and the break-glass password (in .env.private)")
    wanted = {"SERVICE_ACCOUNTS": f"ops={private['OPS_CLIENT_ID']}:{sha256(private['OPS_CLIENT_SECRET'])}",
              "BREAK_GLASS_PASSWORD_SHA256": sha256(private["BREAK_GLASS_PASSWORD"])}
    stale = {k: v for k, v in wanted.items() if env.get(k) != v}
    if stale:
        set_values(ENV, stale)
        print(f"set in .env: {', '.join(stale)}")
    return 3 if created else 0


if __name__ == "__main__":
    if sys.argv[1:] != ["ensure"]:
        print(__doc__)
        sys.exit(2)
    sys.exit(ensure())
