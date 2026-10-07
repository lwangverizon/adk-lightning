"""Internal development HTTP transport; no workspace state or endpoint policy.

The public server.Handler inherits these response and bounded-body mechanics.
Origin checks, workspace locking and error translation stay at that boundary.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler


class RequestError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class JsonHandler(BaseHTTPRequestHandler):
    server_version = "adkl-dev"
    MAX_BODY_BYTES = 1024 * 1024
    BODY_TIMEOUT = 10.0
    timeout = 10.0  # also bounds idle header reads on real sockets

    def handle_one_request(self):
        self.__dict__.pop("_parsed_body", None)
        return super().handle_one_request()

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write(f"  {self.command} {self.path}\n")

    # -- plumbing
    def _send(self, code: int, payload, ctype="application/json"):
        # Switch on the payload type, NOT the content type. Static assets
        # include .json files (version.json, AssetManifest.bin.json), and
        # keying off ctype tried to json.dumps() raw bytes -- which killed the
        # connection mid-response and rendered a blank page, since Flutter's
        # bootstrap needs version.json before it paints anything.
        if isinstance(payload, (bytes, bytearray)):
            body = bytes(payload)
        elif isinstance(payload, str):
            body = payload.encode()
        else:
            from adkl.diagnostics import enrich_payload
            body = json.dumps(enrich_payload(payload), indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Never cache: this is a dev server, and a stale bundle looks exactly
        # like a bug in the app.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,PUT,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def _query(self) -> dict[str, str]:
        q = urllib.parse.urlparse(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}

    def _body(self) -> dict:
        if hasattr(self, "_parsed_body"):
            return self._parsed_body
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) > 1:
            raise RequestError(400, "Ambiguous or unsupported request framing")
        raw = lengths[0] if lengths else "0"
        if not re.fullmatch(r"[0-9]+", raw) or len(raw) > 10:
            raise RequestError(400, "Invalid Content-Length")
        n = int(raw)
        if n > self.MAX_BODY_BYTES:
            raise RequestError(413, "Request body exceeds 1 MiB")
        remaining, chunks = n, []
        connection = getattr(self, "connection", None)
        deadline = time.monotonic() + self.BODY_TIMEOUT
        try:
            while remaining:
                budget = deadline - time.monotonic()
                if budget <= 0:
                    raise TimeoutError
                if connection is not None:
                    connection.settimeout(budget)
                chunk = getattr(self.rfile, "read1", self.rfile.read)(min(remaining, 65536))
                if not chunk:
                    raise RequestError(400, "Incomplete request body")
                chunks.append(chunk)
                remaining -= len(chunk)
        except TimeoutError:
            raise RequestError(408, "Request body timed out") from None
        finally:
            if connection is not None:
                connection.settimeout(self.timeout)
        try:
            body = json.loads(b"".join(chunks)) if n else {}
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            raise RequestError(400, "Request body must be valid JSON") from None
        if not isinstance(body, dict):
            raise RequestError(400, "Request body must be a JSON object")
        self._parsed_body = body
        return body
