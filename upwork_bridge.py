"""Localhost bridge between the menu bar app and the Upwork fill extension.

Binds 127.0.0.1 only. Every request must carry the pairing code in an
`X-FT-Token` header. CORS headers go only to browser-extension origins
(`chrome-extension://`, `moz-extension://`): Firefox-based browsers such as Zen
apply CORS to an extension's own requests and preflight the custom header, so
OPTIONS is answered for those origins only. Web pages (http/https origins) get
no CORS headers, so they cannot read the answers, and the token stops them from
writing.

    GET  /ping                         -> {"ok": true, "app": ..., "version": 1}
    GET  /hello  (no pairing code; needs X-FT-Hello: 1)  -> {"ok": true}
         An unpaired extension says it exists, so the app can ask for the
         code instead of claiming a stale connection. The custom header makes
         a web page's request need a CORS preflight, which pages never pass.
    GET  /upwork/queue?contract=<id>   -> upwork_push.queue_payload(...)
    POST /upwork/added {"contract", "key"} -> {"ok": true} | 404

The app supplies the behaviour through `handlers` so this module stays free
of Toggl and AppKit and can be tested with a real socket.
"""

from __future__ import annotations

import hmac
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
PORT = 47619
MAX_BODY = 16 * 1024
_EXTENSION_ORIGIN = re.compile(r"^(chrome-extension|moz-extension)://[A-Za-z0-9-]+$")


def extension_origin(origin):
    """The Origin header when it is a browser extension's, else None."""
    origin = (origin or "").strip()
    return origin if _EXTENSION_ORIGIN.match(origin) else None


class BridgeHandlers:
    """Override these. `token()` is read on every request so re-pairing works."""

    def token(self):  # pragma: no cover - interface
        raise NotImplementedError

    def ping(self):
        pass

    def hello(self):
        """An extension without a (valid) pairing code says it is there."""

    def queue(self, contract):  # pragma: no cover - interface
        raise NotImplementedError

    def added(self, contract, key):  # pragma: no cover - interface
        """Return True when recorded, False when the key is unknown."""
        raise NotImplementedError


def _make_handler(handlers):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FreelanceTrackerBridge/1"

        def log_message(self, *_args):  # keep the service log quiet
            pass

        def _cors(self):
            origin = extension_origin(self.headers.get("Origin"))
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            return origin

        def _send(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):  # noqa: N802 - CORS preflight, extensions only
            if not extension_origin(self.headers.get("Origin")):
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "X-FT-Token, X-FT-Hello, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _authorized(self):
            expected = handlers.token() or ""
            given = self.headers.get("X-FT-Token") or ""
            if expected and hmac.compare_digest(given.strip(), expected):
                return True
            self._send(401, {"ok": False, "error": "Wrong pairing code"})
            return False

        def do_GET(self):  # noqa: N802 - http.server API
            if urlparse(self.path).path == "/hello":
                if self.headers.get("X-FT-Hello") != "1":
                    self._send(400, {"ok": False, "error": "Missing X-FT-Hello"})
                    return
                handlers.hello()
                self._send(200, {"ok": True})
                return
            if not self._authorized():
                return
            url = urlparse(self.path)
            try:
                if url.path == "/ping":
                    handlers.ping()
                    self._send(200, {"ok": True, "app": "Freelance Tracker", "version": 1})
                elif url.path == "/upwork/queue":
                    contract = (parse_qs(url.query).get("contract") or [""])[0].strip()
                    if not contract.isdigit():
                        self._send(400, {"ok": False, "error": "contract must be digits"})
                        return
                    handlers.ping()
                    self._send(200, handlers.queue(contract))
                else:
                    self._send(404, {"ok": False, "error": "Not found"})
            except Exception as exc:
                self._send(500, {"ok": False, "error": str(exc)})

        def do_POST(self):  # noqa: N802 - http.server API
            if not self._authorized():
                return
            url = urlparse(self.path)
            if url.path != "/upwork/added":
                self._send(404, {"ok": False, "error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > MAX_BODY:
                    self._send(400, {"ok": False, "error": "Bad body"})
                    return
                body = json.loads(self.rfile.read(length) or b"{}")
                contract = str(body.get("contract") or "").strip()
                key = str(body.get("key") or "").strip()
                if not contract.isdigit() or not key:
                    self._send(400, {"ok": False, "error": "contract and key are required"})
                    return
                if handlers.added(contract, key):
                    self._send(200, {"ok": True})
                else:
                    self._send(404, {"ok": False, "error": "That entry changed in Toggl; reload the page"})
            except Exception as exc:
                self._send(500, {"ok": False, "error": str(exc)})

    return Handler


def start(handlers, host=HOST, port=PORT):
    """Serve on a daemon thread. Returns the server (call .shutdown() to stop)."""
    server = ThreadingHTTPServer((host, port), _make_handler(handlers))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="upwork-bridge", daemon=True)
    thread.start()
    return server
