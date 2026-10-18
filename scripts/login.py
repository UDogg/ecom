#!/usr/bin/env python3
"""Sign in through auth-service the way a browser does and print an access token.

Runs the OIDC authorization code flow with PKCE: /oauth2/authorize, the login
form (with its CSRF token), the redirect carrying the code, then the token
exchange. Standard library only, so it works on any machine with Python 3.

    python3 scripts/login.py --role owner            # seeded shop owner from .env
    python3 scripts/login.py --email a@b.c --password ...
    python3 scripts/login.py --role customer --json  # full token response
"""

import argparse
import base64
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def read_env_file(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.split(" #")[0].strip()
    return values


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Browser:
    """Cookie-keeping client that follows redirects by hand so we can stop at the callback."""

    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _NoRedirect())

    def request(self, url, data=None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        # Accept like a browser: Spring Security does not resume a request
        # that asked for JSON after the login form.
        req = urllib.request.Request(url, data=body, headers={"Accept": "text/html"})
        try:
            with self.opener.open(req, timeout=10) as resp:
                return resp.status, resp.headers, resp.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read().decode(errors="replace")


def login(issuer, client_id, redirect_uri, email, password, scope="openid email profile"):
    browser = Browser()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    authorize = f"{issuer}/oauth2/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": scope,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })

    url, form_data = authorize, None
    for _ in range(10):
        status, headers, body = browser.request(url, form_data)
        form_data = None
        if status in (301, 302, 303):
            location = urllib.parse.urljoin(url, headers["Location"])
            if location.startswith(redirect_uri):
                params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
                if "error" in params:
                    raise SystemExit(f"authorization failed: {params['error'][0]} {params.get('error_description', [''])[0]}")
                if params.get("state", [None])[0] != state:
                    raise SystemExit("state mismatch")
                code = params["code"][0]
                break
            if "/login?error" in location:
                raise SystemExit("sign-in failed: wrong email or password")
            url = location
        elif status == 200 and 'name="username"' in body:
            csrf = re.search(r'name="_csrf"[^>]*value="([^"]+)"', body)
            form_data = {"username": email, "password": password, **({"_csrf": csrf.group(1)} if csrf else {})}
            url = urllib.parse.urljoin(url, "/login")
        else:
            raise SystemExit(f"unexpected response {status} from {url}: {body[:300]}")
    else:
        raise SystemExit("too many redirects")

    status, _, body = browser.request(f"{issuer}/oauth2/token", {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": client_id, "code_verifier": verifier,
    })
    if status != 200:
        raise SystemExit(f"token exchange failed ({status}): {body}")
    return json.loads(body)


def main():
    env = {**read_env_file(Path(__file__).resolve().parent.parent / ".env"), **os.environ}
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--role", choices=["owner", "customer"], help="use the seeded account from .env")
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument("--issuer", default=env.get("PUBLIC_AUTH_ISSUER_URL", "http://localhost:8081"))
    parser.add_argument("--client-id", default=env.get("OIDC_CLIENT_ID", "storefront"))
    parser.add_argument("--redirect-uri",
                        default=env.get("OIDC_REDIRECT_URIS", "").split(",")[0].replace(
                            "${PUBLIC_WEB_URL}", env.get("PUBLIC_WEB_URL", "http://localhost:3000"))
                        or "http://localhost:3000/auth/callback")
    parser.add_argument("--json", action="store_true", help="print the whole token response")
    args = parser.parse_args()

    if args.role:
        prefix = "SEED_OWNER" if args.role == "owner" else "SEED_CUSTOMER"
        args.email = args.email or env.get(f"{prefix}_EMAIL")
        args.password = args.password or env.get(f"{prefix}_PASSWORD")
    if not args.email or not args.password:
        parser.error("give --role, or --email and --password")

    try:
        tokens = login(args.issuer.rstrip("/"), args.client_id, args.redirect_uri, args.email, args.password)
    except urllib.error.URLError as err:
        raise SystemExit(f"cannot reach auth-service at {args.issuer}: {err.reason}") from None
    print(json.dumps(tokens, indent=2) if args.json else tokens["access_token"])


if __name__ == "__main__":
    sys.exit(main())
