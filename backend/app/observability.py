"""Structured logging, request IDs and access logs for the FRIDAY API.

``FRIDAY_LOG_LEVEL`` (default INFO) and ``FRIDAY_LOG_FORMAT`` (``text`` or ``json``) control output.
Every response carries an ``X-Request-ID`` header that matches the log lines for that request.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
access_log = logging.getLogger("friday.access")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for key in ("method", "path", "status", "duration_ms", "client"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    if os.getenv("FRIDAY_LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"))
    logger = logging.getLogger("friday")
    logger.setLevel(os.getenv("FRIDAY_LOG_LEVEL", "INFO").upper())
    logger.addHandler(handler)
    logger.propagate = False
    _configured = True


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if 0 < len(incoming) <= 64 and incoming.replace("-", "").isalnum() else uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500
        try:
            try:
                response = await call_next(request)
            except Exception:
                logging.getLogger("friday.api").exception(
                    "unhandled error on %s %s", request.method, request.url.path)
                response = JSONResponse(
                    {"detail": "FRIDAY hit an internal error. Check the backend log.", "request_id": request_id},
                    status_code=500,
                )
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            if request.url.path not in {"/health"}:
                access_log.info(
                    "%s %s -> %s", request.method, request.url.path, status,
                    extra={"method": request.method, "path": request.url.path, "status": status,
                           "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                           "client": request.client.host if request.client else "-"},
                )
            request_id_var.reset(token)
