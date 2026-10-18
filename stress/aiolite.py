"""A tiny asyncio HTTP/1.1 client: enough for the stress arena, standard library only.

The arena runs thousands of virtual shoppers at once. A thread per shopper would
cost gigabytes of stack; an asyncio task costs a few kilobytes, so the browsing
crowd is asyncio. Each shopper owns one keep-alive connection and reuses it, the
way a browser holds a connection open to one origin.

Scope on purpose: plaintext HTTP (the arena talks to the BFF on localhost), one
request in flight per connection, Content-Length or chunked response bodies,
`Connection: close` honoured. No TLS, no pipelining, no redirects (the JSON API
does not redirect). `python3 stress/aiolite.py --selftest` checks the parser
against an embedded server without needing the stack.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit


class HTTPError(Exception):
    """A transport-level failure (connection dropped, timeout, malformed reply)."""


class Response:
    __slots__ = ("status", "headers", "body", "elapsed")

    def __init__(self, status, headers, body, elapsed):
        self.status = status
        self.headers = headers          # lower-cased name -> value
        self.body = body                # bytes
        self.elapsed = elapsed          # seconds

    def json(self):
        import json
        return json.loads(self.body) if self.body else None


class Connection:
    """One keep-alive connection to a single host:port, driven by one task."""

    def __init__(self, host, port, timeout=15.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._reader = None
        self._writer = None

    async def _ensure(self):
        """Return True if an existing keep-alive socket is being reused, False if freshly opened."""
        if self._writer is not None and not self._writer.is_closing():
            return True
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.timeout)
        return False

    async def request(self, method, target, headers=None, body=None):
        # A reused keep-alive socket the server has since dropped fails on first use; that
        # is not a real error, so retry once on a fresh connection before giving up.
        try:
            return await self._attempt(method, target, headers, body)
        except HTTPError:
            if self._writer is not None:
                raise
            return await self._attempt(method, target, headers, body)

    async def _attempt(self, method, target, headers=None, body=None):
        reused = await self._ensure()
        head = [f"{method} {target} HTTP/1.1", f"Host: {self.host}:{self.port}",
                "Accept: application/json", "User-Agent: babel-arena/1"]
        for name, value in (headers or {}).items():
            head.append(f"{name}: {value}")
        if body is not None:
            head.append(f"Content-Length: {len(body)}")
        head.append("")
        head.append("")
        raw = "\r\n".join(head).encode()
        if body is not None:
            raw += body
        started = time.monotonic()
        try:
            self._writer.write(raw)
            await asyncio.wait_for(self._writer.drain(), self.timeout)
            status, resp_headers = await self._read_head()
            payload = await self._read_body(resp_headers)
        except HTTPError:
            await self.close()
            raise
        except (asyncio.TimeoutError, ConnectionError, OSError) as err:
            await self.close()
            raise HTTPError(str(err) or err.__class__.__name__) from err
        if resp_headers.get("connection", "").lower() == "close":
            await self.close()
        return Response(status, resp_headers, payload, time.monotonic() - started)

    async def _read_head(self):
        line = await asyncio.wait_for(self._reader.readline(), self.timeout)
        if not line:
            raise HTTPError("connection closed before any response")
        parts = line.decode("latin1").split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            raise HTTPError(f"bad status line: {line!r}")
        status = int(parts[1])
        headers = {}
        while True:
            hline = await asyncio.wait_for(self._reader.readline(), self.timeout)
            if hline in (b"\r\n", b"\n", b""):
                break
            name, _, value = hline.decode("latin1").partition(":")
            headers[name.strip().lower()] = value.strip()
        return status, headers

    async def _read_body(self, headers):
        if headers.get("transfer-encoding", "").lower() == "chunked":
            chunks = []
            while True:
                size_line = await asyncio.wait_for(self._reader.readline(), self.timeout)
                size = int(size_line.strip().split(b";", 1)[0] or b"0", 16)
                if size == 0:
                    await asyncio.wait_for(self._reader.readline(), self.timeout)  # trailing CRLF
                    break
                chunk = await asyncio.wait_for(self._reader.readexactly(size + 2), self.timeout)
                chunks.append(chunk[:-2])
            return b"".join(chunks)
        length = headers.get("content-length")
        if length is not None:
            n = int(length)
            return await asyncio.wait_for(self._reader.readexactly(n), self.timeout) if n else b""
        # No framing: read until the server closes.
        return await asyncio.wait_for(self._reader.read(), self.timeout)

    async def close(self):
        writer, self._writer, self._reader = self._writer, None, None
        if writer is not None:
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), self.timeout)
            except (OSError, asyncio.TimeoutError):
                pass


def split_base(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", ""):
        raise ValueError(f"aiolite speaks http only, not {parts.scheme!r} (the arena talks to the BFF on localhost)")
    return parts.hostname or "localhost", parts.port or 80


async def _selftest():
    async def handle(reader, writer):
        await reader.readline()
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b""):
                break
        if _selftest.chunked:
            writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                         b"4\r\nping\r\n3\r\n-ok\r\n0\r\n\r\n")
        else:
            writer.write(b"HTTP/1.1 201 Created\r\nContent-Length: 5\r\n\r\nhello")
        await writer.drain()
        writer.close()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        _selftest.chunked = False
        conn = Connection("127.0.0.1", port)
        r = await conn.request("GET", "/")
        assert r.status == 201 and r.body == b"hello", (r.status, r.body)
        await conn.close()
        _selftest.chunked = True
        conn = Connection("127.0.0.1", port)
        r = await conn.request("GET", "/")
        assert r.status == 200 and r.body == b"ping-ok", (r.status, r.body)
        await conn.close()
    print("aiolite self-test OK (content-length + chunked + keep-alive reconnect)")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        asyncio.run(_selftest())
    else:
        print(__doc__)
