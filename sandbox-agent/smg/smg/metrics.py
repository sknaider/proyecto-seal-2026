"""Prometheus metrics exporter for SMG.

Exposes /metrics on a dedicated port (cfg.metrics.port).
"""
from __future__ import annotations

import logging
import threading

from prometheus_client import Counter, Histogram, Gauge, start_http_server

LOG = logging.getLogger("smg.metrics")

# Global registry metrics (module-level so facade middleware can import + emit)
REQUEST_COUNT = Counter(
    "smg_requests_total",
    "Total SMG requests",
    ["method", "path", "status", "agent"],
)
REQUEST_LATENCY = Histogram(
    "smg_request_latency_seconds",
    "SMG request latency",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
BACKEND_HEALTH = Gauge(
    "smg_backend_healthy",
    "Backend health status (1=healthy, 0=circuit open)",
    ["backend"],
)
RATE_LIMITED = Counter(
    "smg_rate_limited_total",
    "Requests rejected by rate limiter",
    ["agent"],
)
AUDIT_QUEUE = Gauge(
    "smg_audit_queue_depth",
    "Current depth of audit log queue (writes pending)",
)


def start_metrics_server(port: int) -> None:
    """Start Prometheus /metrics HTTP server on its own thread."""
    def _run():
        try:
            start_http_server(port)
            LOG.info("Prometheus metrics server listening on :%d", port)
        except Exception as e:
            LOG.error("Failed to start metrics server: %s", e)

    t = threading.Thread(target=_run, daemon=True, name="smg-metrics")
    t.start()
