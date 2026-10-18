"""A service account's SYSTEM token, fetched from auth-service and cached until shortly before it expires."""

import json
import threading
import time
import urllib.parse
import urllib.request

AUTH_URL = "http://auth-service:8081/api/auth/service-token"  # fixed: the private network's name for auth-service


class ServiceToken:
    def __init__(self, client_id, client_secret, url=AUTH_URL, clock=time.time):
        self.client_id, self.client_secret, self.url, self.clock = client_id, client_secret, url, clock
        self._token, self._expires, self._lock = None, 0.0, threading.Lock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(self):
        with self._lock:
            if self._token and self.clock() < self._expires - 60:
                return self._token
            body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.client_id,
                                           "client_secret": self.client_secret}).encode()
            req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
            with self._opener.open(req, timeout=5) as resp:
                data = json.load(resp)
            self._token, self._expires = data["access_token"], self.clock() + int(data["expires_in"])
            return self._token

    def forget(self):
        with self._lock:
            self._token = None
