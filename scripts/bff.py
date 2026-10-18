#!/usr/bin/env python3
"""Talk to Babel Bazaar the way a browser does: through the BFF, with a session cookie.

Tokens never leave the BFF, so there is no "print a token" any more. This
signs in (BFF /auth/login, the proxied sign-in form, /auth/callback), keeps
the session cookie, and sends the CSRF token on state-changing calls.

    python3 scripts/bff.py --role owner GET /api/admin/dashboard
    python3 scripts/bff.py --role customer POST /api/checkout '{"items":[...]}' -H Idempotency-Key:abc
    python3 scripts/bff.py GET /api/catalog/products            # anonymous

Standard library only.
"""

import argparse
import http.cookiejar
import json
import os
import re
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


class BffSession:
    """A cookie-keeping client for the BFF. Follows redirects by hand, only on the BFF's origin."""

    def __init__(self, base="http://localhost:8080"):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect())
        self.csrf = None

    def _send(self, method, url, data=None, headers=None):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with self.opener.open(req, timeout=15) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read()

    def login(self, email, password, return_to="/"):
        url = f"{self.base}/auth/login?" + urllib.parse.urlencode({"return_to": return_to})
        form = None
        for _ in range(12):
            # Accept like a browser: Spring does not resume a request that asked for JSON after the login form.
            headers = {"Accept": "text/html"}
            if form is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            status, resp_headers, body = self._send("POST" if form else "GET", url,
                                                    urllib.parse.urlencode(form).encode() if form else None, headers)
            form = None
            if status in (301, 302, 303):
                location = resp_headers["Location"]
                if "/login?error" in location:
                    raise SystemExit("sign-in failed: wrong email or password")
                if urllib.parse.urlparse(url).path == "/auth/callback":
                    break  # the BFF exchanged the code and set the session cookie
                target = urllib.parse.urljoin(url, location)
                if not target.startswith(self.base + "/"):
                    raise SystemExit(f"refusing to follow a redirect off the BFF: {location}")
                url = target
                continue
            text = body.decode(errors="replace")
            if status == 200 and 'name="username"' in text:
                csrf = re.search(r'name="_csrf"[^>]*value="([^"]+)"', text)
                form = {"username": email, "password": password, **({"_csrf": csrf.group(1)} if csrf else {})}
                url = f"{self.base}/login"
                continue
            raise SystemExit(f"unexpected {status} from {url}: {text[:300]}")
        else:
            raise SystemExit("too many redirects while signing in")
        me = self.me()
        if not me.get("authenticated"):
            raise SystemExit("signed in, but the BFF does not see a session")
        return me

    def me(self):
        status, _, body = self.call("GET", "/api/me")
        self.csrf = body.get("csrf_token")
        return body

    def call(self, method, path, body=None, headers=None):
        headers = {"Accept": "application/json", **(headers or {})}
        data = None
        if body is not None:
            data = (body if isinstance(body, str) else json.dumps(body)).encode()
            headers["Content-Type"] = "application/json"
        if method not in ("GET", "HEAD"):
            if self.csrf is None and path != "/api/me":
                self.me()
            headers["X-CSRF-Token"] = self.csrf or ""
        status, resp_headers, raw = self._send(method, self.base + path, data, headers)
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = raw.decode(errors="replace")
        self.last_headers = resp_headers
        return status, resp_headers, parsed

    def logout(self):
        return self.call("POST", "/auth/logout")[0]


def main():
    env = {**read_env_file(Path(__file__).resolve().parent.parent / ".env"), **os.environ}
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("method")
    parser.add_argument("path")
    parser.add_argument("body", nargs="?")
    parser.add_argument("--role", choices=["owner", "customer"], help="sign in as the seeded account from .env")
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument("-H", "--header", action="append", default=[], help="Name:value")
    parser.add_argument("--base", default=env.get("PUBLIC_API_BASE_URL", "http://localhost:8080"))
    args = parser.parse_args()

    session = BffSession(args.base)
    if args.role:
        prefix = "SEED_OWNER" if args.role == "owner" else "SEED_CUSTOMER"
        args.email, args.password = args.email or env.get(f"{prefix}_EMAIL"), args.password or env.get(f"{prefix}_PASSWORD")
    try:
        if args.email:
            session.login(args.email, args.password)
        headers = dict(h.split(":", 1) for h in args.header)
        status, _, body = session.call(args.method.upper(), args.path, args.body, headers)
    except urllib.error.URLError as err:
        raise SystemExit(f"cannot reach the BFF at {args.base}: {err.reason}") from None
    print(status, file=sys.stderr)
    print(json.dumps(body, indent=2) if isinstance(body, (dict, list)) else (body or ""))
    return 0 if status < 400 else 1


if __name__ == "__main__":
    sys.exit(main())
