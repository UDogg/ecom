"""The gateway signature, re-implemented for the red team.

This is the same canonical string and HMAC as services/commerce-core/common/gateway.py
and the Go/Java/FastAPI guards (docs/request-signing.md). The red team has it so it
can forge structurally valid signatures and watch them fail: the point of the demo is
that without the real GATEWAY_SECRET, a perfect-looking signature is still worthless.

Kept deliberately separate from the real guards and given no access to the real secret.
"""

from __future__ import annotations

import hashlib
import hmac

VERSION = "v1"


def canonical(ts, nonce, method, path, query, authorization):
    return (f"{VERSION}\n{ts}\n{nonce}\n{method.upper()}\n{path}\n{query}\n"
            + hashlib.sha256((authorization or "").encode()).hexdigest())


def sign(secret, ts, nonce, method, path, query, authorization=""):
    key = secret if isinstance(secret, bytes) else secret.encode()
    return hmac.new(key, canonical(ts, nonce, method, path, query, authorization).encode(),
                    hashlib.sha256).hexdigest()
