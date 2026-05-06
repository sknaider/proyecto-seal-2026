"""SMG entry point.

Starts the facade, launches the health monitor, and serves via uvicorn.
Run:
    python3 -m smg.main
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import uvicorn

from .config import load_config
from .facade import create_app
from .health import HealthMonitor
from .metrics import BACKEND_HEALTH, start_metrics_server
from .middleware.audit import AuditLogger
from .middleware.rate_limit import RateLimiter


def _build_health_targets(cfg) -> dict[str, str]:
    targets: dict[str, str] = {}
    primary = cfg.backends.get("primary_sse")
    if primary and primary.health_endpoint:
        targets["primary_sse"] = primary.health_endpoint
    return targets


async def _serve(cfg_path: str | None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cfg = load_config(cfg_path)
    logging.getLogger("smg.main").info(
        "Loaded config: server=%s:%d backends=%s",
        cfg.server.host,
        cfg.server.port,
        list(cfg.backends.keys()),
    )

    health = HealthMonitor(
        backends=_build_health_targets(cfg),
        failure_threshold=cfg.health.failure_threshold,
        recovery_check_s=cfg.health.recovery_check_s,
        interval_s=cfg.health.interval_s,
    )

    audit = None
    if cfg.audit.enabled:
        pg = cfg.backends.get("postgres")
        if pg and pg.dsn:
            audit = AuditLogger(pg.dsn)
            await audit.start()
        else:
            logging.getLogger("smg.main").warning("audit enabled but no postgres dsn; disabled")

    ratelimiter = RateLimiter(
        default_per_min=cfg.rate_limit.default_per_min,
        burst=cfg.rate_limit.burst,
        per_agent=cfg.rate_limit.per_agent,
    )

    if cfg.metrics.enabled:
        start_metrics_server(cfg.metrics.port)

    async def _health_exporter():
        while True:
            for name, state in health.state.items():
                BACKEND_HEALTH.labels(backend=name).set(0 if state.circuit_open else 1)
            await asyncio.sleep(10)

    app = create_app(cfg, health, audit=audit, ratelimiter=ratelimiter)

    config = uvicorn.Config(
        app,
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=cfg.server.log_level,
        loop="asyncio",
        lifespan="on",
    )
    server = uvicorn.Server(config)

    async def _run():
        try:
            tasks = [server.serve(), health.monitor_loop()]
            if cfg.metrics.enabled:
                tasks.append(_health_exporter())
            await asyncio.gather(*tasks)
        finally:
            if audit:
                await audit.stop()

    await _run()


def main() -> int:
    parser = argparse.ArgumentParser(prog="smg")
    parser.add_argument("--config", help="Path to smg.yaml")
    args = parser.parse_args()
    try:
        asyncio.run(_serve(args.config))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
