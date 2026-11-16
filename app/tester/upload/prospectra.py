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
# Errors in the report itself: retrying will not help, the report goes to failed/.
# Not final (report waits in the queue): 401/403 = token problem (fix prospectra.env and the queue is sent),
# 404 = Magazyn/Integracje module switched off in ProSpectra.
FINAL_ERRORS = {400, 413, 422}

# Start-screen status of the ProSpectra connection, from GET /ping.
TOKEN_STATES = {
    "missing": "brak tokenu",
    "ok": "token OK",
    "rejected": "token odrzucony",
    "forbidden": "token bez uprawnień",
    "offline": "brak połączenia",
    "error": "błąd serwera",
}


def token_state(resp: "Response | None", configured: bool = True) -> str:
    """Map a GET /ping answer to a TOKEN_STATES key."""
    if not configured:
        return "missing"
    if resp is None or resp.status == 0:
        return "offline"
    if resp.status == 200 and (resp.body or {}).get("ok"):
        return "ok"
    if resp.status == 401:
        return "rejected"
    if resp.status == 403:
        return "forbidden"
    return "error"


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

    def ping(self) -> Response:
        """GET /ping: checks the token (401 = wrong or revoked) and returns the server time."""
        return self._request("GET", "/ping")

    def check_token(self, max_age: float = 60) -> str:
        """Token state for the start screen, cached so the 5-second status refresh does not hit the API each time."""
        now = time.monotonic()
        cached = getattr(self, "_token_cache", None)
        if cached and now - cached[0] < (max_age if cached[1] == "ok" else 15):
            return cached[1]
        state = token_state(self.ping() if self.configured else None, self.configured)
        self._token_cache = (now, state)
        return state

    def forget_token_state(self) -> None:
        self._token_cache = None

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
