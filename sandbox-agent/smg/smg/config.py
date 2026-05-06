"""Load SMG configuration from YAML + environment overrides."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ServerCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8767
    log_level: str = "info"


class BackendCfg(BaseModel):
    url: str | None = None
    health_endpoint: str | None = None
    dsn: str | None = None
    bolt: str | None = None
    timeout_s: int = 30


class HealthCfg(BaseModel):
    interval_s: int = 30
    failure_threshold: int = 3
    recovery_check_s: int = 60


class RateLimitCfg(BaseModel):
    default_per_min: int = 60
    burst: int = 10
    per_agent: dict[str, int] = Field(default_factory=dict)


class AuditCfg(BaseModel):
    enabled: bool = True
    table: str = "smg_audit_log"
    hmac_verify: bool = True


class MetricsCfg(BaseModel):
    enabled: bool = True
    port: int = 9091


class SMGConfig(BaseModel):
    server: ServerCfg = Field(default_factory=ServerCfg)
    backends: dict[str, BackendCfg] = Field(default_factory=dict)
    health: HealthCfg = Field(default_factory=HealthCfg)
    rate_limit: RateLimitCfg = Field(default_factory=RateLimitCfg)
    audit: AuditCfg = Field(default_factory=AuditCfg)
    metrics: MetricsCfg = Field(default_factory=MetricsCfg)


_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "smg.yaml"


def load_config(path: str | Path | None = None) -> SMGConfig:
    target = Path(path) if path else _DEFAULT_PATH
    if not target.exists():
        return SMGConfig()
    with open(target) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    # env overrides for secrets
    if "postgres" in raw.get("backends", {}):
        env_dsn = os.environ.get("SEAL_PG_DSN")
        if env_dsn:
            raw["backends"]["postgres"]["dsn"] = env_dsn
    return SMGConfig(**raw)
