"""Request security for the FRIDAY API: authentication, trusted hosts, rate limits, size limits.

Policy (``FRIDAY_AUTH``):

* ``auto`` (default) - requests from this machine (loopback) or from ``FRIDAY_TRUSTED_NETWORKS``
  are allowed without a token; every other client must send the API token.
* ``required`` - every request except ``/health`` must send the API token, even from loopback.

The token comes from ``FRIDAY_API_TOKEN`` or is generated once into ``$FRIDAY_DATA_DIR/api_token``
(file mode 0600). Clients send it as ``Authorization: Bearer <token>`` or ``X-Friday-Token``.
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = logging.getLogger("friday.security")

PUBLIC_PATHS = {"/health", "/api/health"}
DEFAULT_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _data_dir() -> Path:
    return Path(os.getenv("FRIDAY_DATA_DIR", "./data"))


def auth_mode() -> str:
    mode = os.getenv("FRIDAY_AUTH", "auto").strip().lower()
    return mode if mode in {"auto", "required"} else "auto"


_TOKEN: str | None = None


def api_token() -> str:
    """Return the API token, creating a private token file on first use."""
    global _TOKEN
    configured = os.getenv("FRIDAY_API_TOKEN", "").strip()
    if configured:
        return configured
    if _TOKEN:
        return _TOKEN
    path = _data_dir() / "api_token"
    try:
        if path.is_file():
            _TOKEN = path.read_text(encoding="utf-8").strip()
        if not _TOKEN:
            path.parent.mkdir(parents=True, exist_ok=True)
            _TOKEN = secrets.token_urlsafe(32)
            path.write_text(_TOKEN, encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    except OSError:
        _TOKEN = _TOKEN or secrets.token_urlsafe(32)
    return _TOKEN


def _trusted_networks() -> list[ipaddress._BaseNetwork]:
    networks = []
    for item in os.getenv("FRIDAY_TRUSTED_NETWORKS", "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            log.warning("ignoring invalid FRIDAY_TRUSTED_NETWORKS entry %r", item)
    return networks


def is_trusted_client(host: str | None) -> bool:
    """Loopback clients and configured trusted networks (e.g. the Docker bridge)."""
    if not host:
        return False
    if host == "testclient":  # Starlette TestClient
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or any(address in net for net in _trusted_networks())


def allowed_hosts() -> set[str]:
    configured = {h.strip().lower() for h in os.getenv("FRIDAY_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return DEFAULT_HOSTS | {"testserver"} | configured


def _host_name(header: str) -> str:
    header = header.strip().lower()
    if header.startswith("["):
        return header.split("]")[0] + "]"
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


def presented_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-friday-token", "").strip()


def token_valid(value: str) -> bool:
    return bool(value) and hmac.compare_digest(value.encode(), api_token().encode())


class RateLimiter:
    """Sliding-window limiter keyed by (client, bucket)."""

    def __init__(self) -> None:
        self.hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, key: tuple[str, str], limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        bucket = self.hits[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def _bucket(path: str, method: str) -> tuple[str, int]:
    limit = lambda name, default: int(os.getenv(f"FRIDAY_RATE_{name}", str(default)))  # noqa: E731
    if path.startswith("/api/documents/upload"):
        return "upload", limit("UPLOAD", 20)
    if method == "POST" and path.endswith("/turns"):
        return "agent", limit("AGENT", 30)
    return "default", limit("DEFAULT", 600)


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_json_bytes: int = 1_048_576, max_upload_bytes: int = 26 * 1024 * 1024):
        super().__init__(app)
        self.limiter = RateLimiter()
        self.max_json_bytes = max_json_bytes
        self.max_upload_bytes = max_upload_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        client = request.client.host if request.client else ""

        host = _host_name(request.headers.get("host", ""))
        if host and host not in allowed_hosts():
            log.warning("rejected host header %r from %s", host, client)
            return JSONResponse({"detail": "Host not allowed. Set FRIDAY_ALLOWED_HOSTS."}, status_code=400)

        if request.method != "OPTIONS" and path not in PUBLIC_PATHS:
            trusted = is_trusted_client(client)
            if auth_mode() == "required" or not trusted:
                if not token_valid(presented_token(request)):
                    return JSONResponse({"detail": "Authentication required."}, status_code=401,
                                        headers={"WWW-Authenticate": "Bearer"})

        length = request.headers.get("content-length")
        if length and length.isdigit():
            limit = self.max_upload_bytes if path.startswith("/api/documents/upload") else self.max_json_bytes
            if int(length) > limit:
                return JSONResponse({"detail": "Request body too large."}, status_code=413)

        if request.method != "OPTIONS":
            bucket, limit = _bucket(path, request.method)
            if not self.limiter.allow((client, bucket), limit):
                return JSONResponse({"detail": "Too many requests. Slow down and retry shortly."},
                                    status_code=429, headers={"Retry-After": "10"})

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

