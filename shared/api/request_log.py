"""Time-tracking write audit log (one INFO line per mutating request) + span attrs (U-663)."""

from __future__ import annotations

import logging
import time
from typing import Callable

logger = logging.getLogger("shared.api.request_log")

_TIME_WRITE_PREFIXES = ("/api/v1/time-entries", "/api/v1/time-logs")
_WRITE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


def set_request_span_attributes(**attrs) -> None:
    """Set OpenTelemetry span attributes when the distro is importable; no-op locally.

    Telemetry must never fail a request: this runs inside the error handlers, so a
    span/context implementation that throws would turn a clean 422 into a 500.
    Anything it raises is swallowed (the attribute is simply not recorded)."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span is None:
            return
        for key, value in attrs.items():
            if value is not None:
                span.set_attribute(key, value)
    except Exception:  # noqa: BLE001 — ImportError locally; never a request failure
        return


UNMATCHED_ROUTE = "<unmatched>"


def _route_from_scope(scope: dict) -> str:
    """The matched route TEMPLATE, never the raw path — a raw path carries the
    row's public_id, and the log line must not (it is a per-row identifier)."""
    route = scope.get("route")
    if route is not None:
        return route.path
    return UNMATCHED_ROUTE


def _state_attr(scope: dict, name: str):
    # Starlette backs `request.state` with the plain dict at scope["state"].
    return (scope.get("state") or {}).get(name)


class TimeTrackingWriteLogMiddleware:
    """Pure ASGI middleware — one INFO `time_write` line after each time-tracking mutation."""

    def __init__(self, app: Callable):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        if method not in _WRITE_METHODS or not path.startswith(_TIME_WRITE_PREFIXES):
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        # try/finally: an exception that escapes the app (an unhandled 500) must
        # still leave a line — the failures are exactly the writes worth auditing.
        # `status_code` stays 500 when no response start was ever sent.
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                "time_write",
                extra={
                    "method": method,
                    "route": _route_from_scope(scope),
                    "status": status_code,
                    "duration_ms": duration_ms,
                    "user_id": _state_attr(scope, "user_id"),
                    "error_code": _state_attr(scope, "error_code"),
                    "event_name": "api.time_write",
                },
            )
