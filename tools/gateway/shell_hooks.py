"""Configurable shell hook system — lifecycle hooks without writing plugin code.

SEAL parity: SEAL lets users define pre/post tool_call hooks in config.yaml
without writing a plugin. SEAL now does the same natively.

Hooks are defined in JSON (default: ~/.config/seal/hooks.json) and executed
automatically by the SEAL hook infrastructure:

    {
      "hooks": [
        {
          "event": "pre_tool",
          "tool_pattern": "Bash",
          "command": "logger -t seal 'bash called'",
          "timeout": 3,
          "env": {"MY_VAR": "value"},
          "on_failure": "warn"
        },
        {
          "event": "post_tool",
          "tool_pattern": "*",
          "command": "/home/dadito/IA/scripts/notify.sh {{tool_name}}",
          "timeout": 5
        }
      ]
    }

Template variables in `command`:
    {{tool_name}}   — name of the tool being called
    {{agent}}       — SEAL_AGENT env var
    {{timestamp}}   — ISO8601 UTC timestamp
    {{result}}      — tool result (post_tool only, first 200 chars)

on_failure: "warn" (default, continue) | "block" (deny the tool call)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "seal" / "hooks.json"


class HookError(Exception):
    """Raised when hook loading fails."""


@dataclass
class HookConfig:
    """A single hook definition."""

    event: str
    command: str
    tool_pattern: str = "*"
    timeout: int = 5
    env: dict[str, str] = field(default_factory=dict)
    on_failure: str = "warn"


@dataclass
class HookResult:
    hook: HookConfig
    matched: bool
    executed: bool
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.executed and self.returncode == 0


def _glob_match(pattern: str, name: str) -> bool:
    """Simple glob — supports * wildcard only."""
    if pattern == "*":
        return True
    if "*" not in pattern:
        return pattern == name
    regex = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
    return bool(re.match(regex, name))


def _render_command(template: str, ctx: dict[str, str]) -> str:
    for key, value in ctx.items():
        template = template.replace("{{" + key + "}}", value)
    return template


class ShellHookRegistry:
    """Loads hook configs and runs them on lifecycle events.

    Usage:
        registry = ShellHookRegistry.from_file()
        results = registry.run("pre_tool", tool_name="Bash", agent="ADA")

    The registry is intentionally synchronous — hooks run in order and SEAL's
    pre_tool_hook.py is also synchronous (no event loop required).
    """

    def __init__(self, hooks: Optional[list[HookConfig]] = None) -> None:
        self._hooks: list[HookConfig] = hooks or []

    @classmethod
    def from_file(cls, path: Optional[Path] = None) -> "ShellHookRegistry":
        """Load hook configs from a JSON file."""
        config_path = path or Path(os.environ.get("SEAL_HOOKS_CONFIG", str(DEFAULT_CONFIG_PATH)))
        if not config_path.exists():
            return cls([])
        try:
            data = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            raise HookError(f"failed to load hooks from {config_path}: {e}")

        hooks = []
        for raw in data.get("hooks", []):
            hooks.append(HookConfig(
                event=raw["event"],
                command=raw["command"],
                tool_pattern=raw.get("tool_pattern", "*"),
                timeout=int(raw.get("timeout", 5)),
                env={str(k): str(v) for k, v in raw.get("env", {}).items()},
                on_failure=raw.get("on_failure", "warn"),
            ))
        return cls(hooks)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShellHookRegistry":
        """Build a registry from a plain dict — useful for tests and embedding."""
        hooks = []
        for raw in data.get("hooks", []):
            hooks.append(HookConfig(
                event=raw["event"],
                command=raw["command"],
                tool_pattern=raw.get("tool_pattern", "*"),
                timeout=int(raw.get("timeout", 5)),
                env={str(k): str(v) for k, v in raw.get("env", {}).items()},
                on_failure=raw.get("on_failure", "warn"),
            ))
        return cls(hooks)

    def register(self, hook: HookConfig) -> None:
        self._hooks.append(hook)

    def run(
        self,
        event: str,
        tool_name: str = "",
        agent: str = "",
        result_snippet: str = "",
    ) -> list[HookResult]:
        """Run all hooks matching (event, tool_name). Returns results for every hook tried."""
        ctx = {
            "tool_name": tool_name,
            "agent": agent or os.environ.get("SEAL_AGENT", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": result_snippet[:200],
        }
        results: list[HookResult] = []
        for hook in self._hooks:
            if hook.event != event:
                continue
            if not _glob_match(hook.tool_pattern, tool_name):
                results.append(HookResult(hook=hook, matched=False, executed=False))
                continue
            result = self._execute(hook, ctx)
            results.append(result)
        return results

    def should_block(self, results: list[HookResult]) -> Optional[str]:
        """Return a block reason if any blocking hook failed, else None."""
        for r in results:
            if r.matched and not r.success and r.hook.on_failure == "block":
                reason = r.stderr or r.error or f"hook exited {r.returncode}"
                return f"[shell_hook] {r.hook.command!r} failed: {reason}"
        return None

    def _execute(self, hook: HookConfig, ctx: dict[str, str]) -> HookResult:
        cmd = _render_command(hook.command, ctx)
        env = {**os.environ, **hook.env}
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=hook.timeout,
                env=env,
            )
            return HookResult(
                hook=hook,
                matched=True,
                executed=True,
                returncode=proc.returncode,
                stdout=proc.stdout.strip()[:500],
                stderr=proc.stderr.strip()[:500],
            )
        except subprocess.TimeoutExpired:
            return HookResult(
                hook=hook, matched=True, executed=True,
                returncode=-1,
                error=f"timed out after {hook.timeout}s",
            )
        except Exception as e:
            return HookResult(
                hook=hook, matched=True, executed=False,
                error=str(e),
            )

    def hooks_for(self, event: str) -> list[HookConfig]:
        return [h for h in self._hooks if h.event == event]

    def summary(self) -> dict[str, Any]:
        by_event: dict[str, int] = {}
        for h in self._hooks:
            by_event[h.event] = by_event.get(h.event, 0) + 1
        return {"total": len(self._hooks), "by_event": by_event}

    def to_dict(self) -> dict[str, Any]:
        return {
            "hooks": [
                {
                    "event": h.event,
                    "tool_pattern": h.tool_pattern,
                    "command": h.command,
                    "timeout": h.timeout,
                    "env": h.env,
                    "on_failure": h.on_failure,
                }
                for h in self._hooks
            ]
        }

    def save(self, path: Optional[Path] = None) -> Path:
        """Persist hook config to JSON file."""
        config_path = path or Path(os.environ.get("SEAL_HOOKS_CONFIG", str(DEFAULT_CONFIG_PATH)))
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(self.to_dict(), indent=2))
        return config_path
