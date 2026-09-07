#!/usr/bin/env python3
"""Per-agent memory rubric loader.

Canonical path: ``agents/<AGENT>/rubric.yaml``.
Legacy fallback: ``agents/<AGENT>/rules/rubric.yaml`` (migration compat).

PyYAML is used when available so the loader can consume the structured team
schema. A tiny YAML-subset parser remains as a dependency-free fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - dependency-free fallback path
    yaml = None


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULE_DIR = REPO_ROOT / "agents"


@dataclass(frozen=True)
class AgentRubric:
    agent: str
    protected_categories: tuple[str, ...] = ("identity", "core_belief", "core", "trust")
    high_importance_categories: tuple[str, ...] = ("correction", "decision", "milestone", "trust")
    chat_excerpt_importance_cap: int = 5
    chat_excerpt_override_categories: tuple[str, ...] = ("correction", "trust")
    short_memory_token_threshold: int = 14
    short_memory_char_threshold: int = 180
    minimum_chars_for_importance_9: int = 300
    auto_consolidate_categories: tuple[str, ...] = ("dynamic", "fact", "insight", "pattern")
    never_consolidate_categories: tuple[str, ...] = ("core", "identity", "core_belief", "trust")
    notes: str = ""
    raw: dict[str, object] = field(default_factory=dict)


def _parse_scalar(value: str) -> object:
    value = value.strip()
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def _parse_simple_yaml(path: Path) -> dict[str, object]:
    data: dict[str, object] = {}
    current_key: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value:
                data[key] = _parse_scalar(value)
                current_key = None
            else:
                data[key] = []
                current_key = key
            continue
        stripped = line.strip()
        if current_key and stripped.startswith("- "):
            existing = data.setdefault(current_key, [])
            if isinstance(existing, list):
                existing.append(_parse_scalar(stripped[2:]))
    return data


def _parse_yaml(path: Path) -> dict[str, object]:
    if yaml is not None:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            return dict(loaded)
    return _parse_simple_yaml(path)


def _tuple(data: dict[str, object], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = data.get(key)
    if isinstance(value, list):
        return tuple(str(v) for v in value)
    if isinstance(value, str):
        return (value,)
    return default


def _int(data: dict[str, object], key: str, default: int) -> int:
    value = data.get(key)
    if isinstance(value, int):
        return value
    return default


def _nested_int(data: dict[str, object], path: tuple[str, ...], default: int) -> int:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if isinstance(current, int) else default


def _rubric_path(agent: str, rule_dir: Path) -> Path | None:
    canonical = rule_dir / agent / "rubric.yaml"
    if canonical.exists():
        return canonical
    legacy = rule_dir / agent / "rules" / "rubric.yaml"
    if legacy.exists():
        return legacy
    return None


def load_agent_rubric(agent: str, rule_dir: Path = DEFAULT_RULE_DIR) -> AgentRubric:
    agent_u = agent.upper()
    path = _rubric_path(agent_u, rule_dir)
    if not path:
        return AgentRubric(agent=agent_u)
    data = _parse_yaml(path)
    base = AgentRubric(agent=agent_u, raw=data)
    return AgentRubric(
        agent=agent_u,
        protected_categories=_tuple(data, "protected_categories", base.protected_categories),
        high_importance_categories=_tuple(data, "high_importance_categories", base.high_importance_categories),
        chat_excerpt_importance_cap=_int(data, "chat_excerpt_importance_cap", base.chat_excerpt_importance_cap),
        chat_excerpt_override_categories=_tuple(
            data, "chat_excerpt_override_categories", base.chat_excerpt_override_categories
        ),
        short_memory_token_threshold=_int(
            data,
            "short_memory_token_threshold",
            _nested_int(data, ("high_importance_gates", "min_unique_tokens"), base.short_memory_token_threshold),
        ),
        short_memory_char_threshold=_int(data, "short_memory_char_threshold", base.short_memory_char_threshold),
        minimum_chars_for_importance_9=_int(
            data,
            "minimum_chars_for_importance_9",
            _nested_int(data, ("high_importance_gates", "min_content_length_chars"), base.minimum_chars_for_importance_9),
        ),
        auto_consolidate_categories=_tuple(data, "auto_consolidate_categories", base.auto_consolidate_categories),
        never_consolidate_categories=_tuple(data, "never_consolidate_categories", base.never_consolidate_categories),
        notes=str(data.get("notes") or ""),
        raw=data,
    )


if __name__ == "__main__":
    import json
    import sys

    rubric = load_agent_rubric(sys.argv[1] if len(sys.argv) > 1 else "ADA")
    print(json.dumps(rubric.__dict__, ensure_ascii=False, indent=2))
