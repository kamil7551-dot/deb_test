"""ProSpectra external API client (contract in INSTRUCTIONS.md § ProSpectra integration).

Standard library only (urllib), TLS verification always on.
"""

from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from tester import __version__

log = logging.getLogger("tester.upload")

RETRY_DELAYS = (2, 4, 8, 16)
FINAL_ERRORS = {400, 401, 403, 404, 413, 422}   # retrying will not help


@dataclass
class Response:
    status: int                     # HTTP status, or 0 for a network error
    body: dict | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in (200, 201)

    @property
    def retryable(self) -> bool:
        return self.status == 0 or self.status == 429 or self.status >= 500

    def message(self) -> str:
        if self.body and isinstance(self.body, dict) and self.body.get("error"):
            return f"{self.status}: {self.body['error']}"
        return f"{self.status or 'sieć'}: {self.error or ''}".strip()


class Client:
    def __init__(self, base_url: str, token: str | None, station_slug: str = "station", timeout: float = 30,
                 sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.user_agent = f"itad-tester/{__version__} ({station_slug})"
        self.timeout = timeout
        self._sleep = sleep
        self._ctx = ssl.create_default_context()

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _request(self, method: str, path: str, body: bytes | None = None,
                 content_type: str = "application/json") -> Response:
        req = urllib.request.Request(self.base_url + path, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("User-Agent", self.user_agent)
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                raw = resp.read()
                return Response(resp.status, _json(raw))
        except urllib.error.HTTPError as exc:
            return Response(exc.code, _json(exc.read()), str(exc.reason))
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return Response(0, None, str(getattr(exc, "reason", exc)))

    def request_with_retry(self, method: str, path: str, body: bytes | None = None,
                           content_type: str = "application/json") -> Response:
        resp = self._request(method, path, body, content_type)
        for delay in RETRY_DELAYS:
            if resp.ok or not resp.retryable:
                break
            log.warning("%s %s -> %s, retry in %ss", method, path, resp.message(), delay)
            self._sleep(delay)
            resp = self._request(method, path, body, content_type)
        return resp

    def lookup_unit(self, serial: str) -> Response:
        return self._request("GET", "/units/" + urllib.parse.quote(serial, safe=""))

    def send_test(self, body: dict) -> Response:
        data = json.dumps(body, ensure_ascii=False).encode()
        return self.request_with_retry("POST", "/tests", data)

    def put_report_pdf(self, test_id: str, pdf: bytes) -> Response:
        return self.request_with_retry("PUT", f"/tests/{urllib.parse.quote(test_id, safe='')}/report", pdf,
                                       "application/pdf")


def _json(raw: bytes) -> dict | None:
    try:
        v = json.loads(raw.decode() or "null")
        return v if isinstance(v, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None
