"""Request timing and instrumentation for latency measurement.

Records stage-level durations for every API request without logging
API keys, repository text, or other sensitive content.
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Dict, List, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("repotalks.timing")


class RequestTimer:
    """Accumulates stage durations for a single request."""

    __slots__ = ("request_id", "stages", "_active_stage", "_active_start", "_start_time", "extras")

    def __init__(self, request_id: Optional[str] = None) -> None:
        self.request_id = request_id or uuid.uuid4().hex[:12]
        self.stages: List[Dict[str, Any]] = []
        self._active_stage: Optional[str] = None
        self._active_start: float = 0.0
        self._start_time: float = time.perf_counter()
        self.extras: Dict[str, Any] = {}

    # -- stage tracking --

    @contextmanager
    def stage(self, name: str):
        """Synchronous context manager for timing a named stage."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.stages.append({"stage": name, "ms": round(elapsed_ms, 2)})

    @asynccontextmanager
    async def async_stage(self, name: str):
        """Async context manager for timing a named stage."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.stages.append({"stage": name, "ms": round(elapsed_ms, 2)})

    def mark(self, name: str) -> None:
        """Record a point-in-time marker relative to request start."""
        elapsed_ms = (time.perf_counter() - self._start_time) * 1000
        self.stages.append({"stage": name, "ms": round(elapsed_ms, 2), "marker": True})

    def set_extra(self, key: str, value: Any) -> None:
        """Attach metadata (chunk_count, model, tokens, etc.)."""
        self.extras[key] = value

    # -- reporting --

    @property
    def total_ms(self) -> float:
        return round((time.perf_counter() - self._start_time) * 1000, 2)

    def summary(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "total_ms": self.total_ms,
            "stages": self.stages,
            **self.extras,
        }

    def log_summary(self, method: str = "", path: str = "", status: int = 0) -> None:
        """Emit a structured timing log line."""
        data = self.summary()
        data["method"] = method
        data["path"] = path
        data["status"] = status
        # Never log API keys or repository text.
        logger.info("request_timing %s", data)


# -- Starlette/FastAPI Middleware --

_TIMER_ATTR = "_request_timer"


def get_timer(request: Request) -> RequestTimer:
    """Retrieve the timer attached to the current request, or create one."""
    timer = getattr(request.state, _TIMER_ATTR, None)
    if timer is None:
        timer = RequestTimer()
        request.state._request_timer = timer  # noqa: SLF001
    return timer


class TimingMiddleware(BaseHTTPMiddleware):
    """Attaches a RequestTimer to every request and logs a summary on completion."""

    async def dispatch(self, request: Request, call_next) -> Response:
        timer = RequestTimer()
        request.state._request_timer = timer  # noqa: SLF001

        response: Response = await call_next(request)

        # Add request-id header for client correlation.
        response.headers["X-Request-Id"] = timer.request_id

        # Skip logging for static assets and health checks.
        path = request.url.path
        if not path.startswith("/api/"):
            return response

        timer.log_summary(
            method=request.method,
            path=path,
            status=response.status_code,
        )
        return response
