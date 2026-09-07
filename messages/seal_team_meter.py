#!/usr/bin/env python3
"""seal_team_meter.py — One-line team context bar for tmux status-right.

Uses exact API usage tokens (input_tokens + cache tokens) from transcript.
Outputs: ADA:72% JARVIS:72% ALICE:75% NEXUS:0%
"""
import json, sys, time
from pathlib import Path

CONTEXT_LIMIT = 200_000
AGENTS        = ["ADA", "JARVIS", "ALICE", "NEXUS"]

AGENT_PROJECT_DIRS = {
    "JARVIS": "-home-dadito-IA-proyecto-seal",
    "NEXUS":  "-home-dadito-IA-proyecto-seal-sandbox-agent-NEXUS",
    "ALICE":  "-home-dadito-IA-proyecto-seal-alice",
    "ADA":    "-home-dadito-IA-proyecto-seal-ada-local",
}
DEFAULT_PROJECT_DIR = "-home-dadito-IA-proyecto-seal"


def _find_transcript(agent: str) -> Path | None:
    base = Path.home() / ".claude" / "projects"
    proj = AGENT_PROJECT_DIRS.get(agent.upper(), DEFAULT_PROJECT_DIR)
    d = base / proj
    if not d.exists():
        d = base / DEFAULT_PROJECT_DIR
    if not d.exists():
        return None
    candidates = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _exact_tokens(transcript: Path) -> int:
    last_usage = None
    try:
        with open(transcript, "rb") as f:
            for raw in f:
                try:
                    e = json.loads(raw)
                    msg = e.get("message", {})
                    usage = msg.get("usage") if isinstance(msg, dict) else None
                    if usage is None:
                        usage = e.get("usage")
                    if usage and isinstance(usage, dict):
                        last_usage = usage
                except Exception:
                    pass
    except Exception:
        pass
    if not last_usage:
        return 0
    return (last_usage.get("input_tokens", 0) or 0) + \
           (last_usage.get("cache_creation_input_tokens", 0) or 0) + \
           (last_usage.get("cache_read_input_tokens", 0) or 0)


def main():
    tmux = "--tmux" in sys.argv
    parts = []
    for agent in AGENTS:
        t = _find_transcript(agent)
        tokens = _exact_tokens(t) if t else 0
        pct = round(min(tokens / CONTEXT_LIMIT * 100, 100))
        if tmux:
            color = "#[fg=colour196]" if pct >= 85 else "#[fg=colour220]" if pct >= 60 else "#[fg=colour82]"
            parts.append(f"{color}{agent}:{pct}%#[default]")
        else:
            marker = "🔴" if pct >= 85 else "🟡" if pct >= 60 else ""
            parts.append(f"{agent}:{pct}%{marker}")
    print(" │ ".join(parts), end="")


if __name__ == "__main__":
    main()
