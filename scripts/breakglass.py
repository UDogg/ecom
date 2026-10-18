"""Break glass and SYSTEM calls, from inside the private network (the opsbox container).

Run it through scripts/breakglass.sh, which supplies the secrets. Standard library only.

    system-token                                    print a SYSTEM token (service account `ops`)
    system METHOD SERVICE PATH [JSON]               call a core service as SYSTEM
    token USER_ID REASON                            print a break-glass token acting as that user
    call USER_ID REASON METHOD SERVICE PATH [JSON]  call a core service as that user, without the BFF
    audit                                           the break-glass audit trail (every attempt)

SERVICE: auth commerce catalog intelligence
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SERVICES = {
    "auth": "http://auth-service:8081",
    "commerce": "http://commerce-core:8000",
    "catalog": "http://catalog-reader:8082",
    "intelligence": "http://intelligence:8001",
}

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # private network: never a proxy


def die(message, code=1):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def request(method, url, token=None, body=None, form=None):
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        data, headers["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    elif body is not None:
        data, headers["Content-Type"] = (body if isinstance(body, str) else json.dumps(body)).encode(), "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _opener.open(req, timeout=15) as resp:
            raw = resp.read()
            return resp.status, raw
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except urllib.error.URLError as err:
        die(f"cannot reach {url}: {err.reason}")


def env(name):
    value = os.environ.get(name, "")
    if not value:
        die(f"{name} is not set (scripts/breakglass.sh reads it from .env.private)")
    return value


def system_token():
    status, raw = request("POST", SERVICES["auth"] + "/api/auth/service-token", form={
        "grant_type": "client_credentials", "client_id": env("OPS_CLIENT_ID"), "client_secret": env("OPS_CLIENT_SECRET")})
    if status != 200:
        die(f"service account sign-in refused ({status}): {raw.decode(errors='replace')}")
    return json.loads(raw)["access_token"]


def break_glass(user_id, reason):
    status, raw = request("POST", SERVICES["auth"] + "/api/auth/break-glass", token=system_token(), body={
        "break_glass_password": env("BREAK_GLASS_PASSWORD"), "user_id": user_id, "reason": reason})
    if status == 404:
        die("break glass is switched off (BREAK_GLASS_PASSWORD_SHA256 is not configured)")
    if status != 200:
        die(f"break glass refused ({status}): {raw.decode(errors='replace')}")
    grant = json.loads(raw)
    acting = grant["acting_as"]
    print(f"BREAK-GLASS: acting as {acting['email']} {acting['roles']} for {grant['expires_in'] // 60} min "
          f"(grant {grant['grant_id']}). Recorded.", file=sys.stderr)
    return grant["access_token"]


def service_url(service, path):
    if service not in SERVICES:
        die(f"unknown service {service!r}; one of: {', '.join(SERVICES)}")
    if not path.startswith("/"):
        die("PATH must start with /")
    return SERVICES[service] + path


def show(status, raw):
    print(status, file=sys.stderr)
    try:
        print(json.dumps(json.loads(raw), indent=2))
    except ValueError:
        print(raw.decode(errors="replace"))
    return 0 if status < 400 else 1


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0 if argv else 2
    cmd, args = argv[0], argv[1:]
    if cmd == "system-token" and not args:
        print(system_token())
    elif cmd == "token" and len(args) == 2:
        print(break_glass(*args))
    elif cmd == "system" and len(args) in (3, 4):
        method, service, path, *body = args
        return show(*request(method.upper(), service_url(service, path), system_token(), body[0] if body else None))
    elif cmd == "call" and len(args) in (5, 6):
        user_id, reason, method, service, path, *body = args
        url = service_url(service, path)  # validate before asking for a grant
        return show(*request(method.upper(), url, break_glass(user_id, reason), body[0] if body else None))
    elif cmd == "audit" and not args:
        return show(*request("GET", SERVICES["auth"] + "/api/auth/ops/break-glass-grants", system_token()))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
