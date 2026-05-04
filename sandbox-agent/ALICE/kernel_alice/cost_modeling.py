"""ALICE cost_modeling — parametric cost models for SEAL infrastructure.

Models GPU, API, storage, network, and personnel costs with explicit
assumptions. Every cost line carries provenance (source + confidence).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


CostKind = Literal["gpu", "api", "storage", "network", "license", "personnel", "energy", "other"]


@dataclass
class CostLine:
    label: str
    kind: CostKind
    monthly_usd: float
    source: str = ""
    confidence: float = 0.7
    assumptions: list[str] = field(default_factory=list)
    notes: str = ""

    def annual(self) -> float:
        return round(self.monthly_usd * 12, 2)


@dataclass
class CostModel:
    name: str
    lines: list[CostLine] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def add(self, line: CostLine) -> "CostModel":
        self.lines.append(line)
        return self

    def total_monthly(self) -> float:
        return round(sum(l.monthly_usd for l in self.lines), 2)

    def total_annual(self) -> float:
        return round(self.total_monthly() * 12, 2)

    def by_kind(self) -> dict[CostKind, float]:
        out: dict[CostKind, float] = {}
        for l in self.lines:
            out[l.kind] = round(out.get(l.kind, 0.0) + l.monthly_usd, 2)
        return out

    def low_confidence_lines(self, threshold: float = 0.6) -> list[CostLine]:
        return [l for l in self.lines if l.confidence < threshold]

    def report(self) -> dict:
        return {
            "model": self.name,
            "created_at": self.created_at,
            "line_count": len(self.lines),
            "total_monthly_usd": self.total_monthly(),
            "total_annual_usd": self.total_annual(),
            "by_kind_monthly": self.by_kind(),
            "low_confidence_count": len(self.low_confidence_lines()),
            "lines": [
                {
                    "label": l.label,
                    "kind": l.kind,
                    "monthly_usd": l.monthly_usd,
                    "source": l.source,
                    "confidence": l.confidence,
                    "assumptions": l.assumptions,
                }
                for l in self.lines
            ],
        }


def gpu_line(label: str, hours_per_month: float, usd_per_hour: float, source: str, confidence: float = 0.8) -> CostLine:
    monthly = round(hours_per_month * usd_per_hour, 2)
    return CostLine(
        label=label,
        kind="gpu",
        monthly_usd=monthly,
        source=source,
        confidence=confidence,
        assumptions=[
            f"hours_per_month={hours_per_month}",
            f"usd_per_hour={usd_per_hour}",
        ],
    )


def api_line(label: str, calls_per_month: int, cost_per_1k: float, source: str, confidence: float = 0.8) -> CostLine:
    monthly = round((calls_per_month / 1000.0) * cost_per_1k, 2)
    return CostLine(
        label=label,
        kind="api",
        monthly_usd=monthly,
        source=source,
        confidence=confidence,
        assumptions=[
            f"calls_per_month={calls_per_month}",
            f"cost_per_1k_usd={cost_per_1k}",
        ],
    )
