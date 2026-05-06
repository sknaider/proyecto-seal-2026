#!/usr/bin/env python3
"""seal_context_meter.py — Exact context window meter for SEAL agents.

Reads actual token usage from Claude Code transcript JSONL files.
No byte-estimation — uses real API usage fields (input_tokens + cache tokens).

Usage:
  python3 seal_context_meter.py [AGENT_NAME]          # single line (statusline)
  python3 seal_context_meter.py --watch               # all agents, auto-refresh
  python3 seal_context_meter.py NEXUS --watch         # single agent, auto-refresh
  python3 seal_context_meter.py --tmux ALICE          # tmux status-right format
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

CONTEXT_LIMIT = 200_000
BAR_WIDTH     = 10
REFRESH_S     = 10

# Agent → Claude project directory mapping
AGENT_PROJECT_DIRS = {
    "JARVIS": "-home-dadito-IA-proyecto-seal-memory",
    "NEXUS":  "-home-dadito-IA-proyecto-seal-sandbox-agent-NEXUS",
    "ALICE":  "-home-dadito-IA-proyecto-seal-alice",
    "ADA":    "-home-dadito-IA-proyecto-seal-ada-local",
}
DEFAULT_PROJECT_DIR = "-home-dadito-IA-proyecto-seal-memory"

AGENTS = ["JARVIS", "ALICE", "ADA", "NEXUS"]

RESET = "\033[0m"
DIM   = "\033[2m"
BOLD  = "\033[1m"


def _find_transcript(agent: str = "") -> Path | None:
    projects_root = Path.home() / ".claude" / "projects"
    agent_upper = agent.upper()

    # Primary mapped directory — exact match only to avoid cross-agent contamination
    primary = AGENT_PROJECT_DIRS.get(agent_upper, DEFAULT_PROJECT_DIR)
    candidates = []
    for d in projects_root.iterdir() if projects_root.exists() else []:
        if not d.is_dir():
            continue
        if d.name == primary:
            candidates.extend(d.glob("*.jsonl"))

    if not candidates:
        return None
    # Return the most recently modified JSONL across all matching directories
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _read_exact_usage(transcript: Path) -> tuple[int, int, float]:
    """Read exact token usage from transcript's last assistant usage entry.

    Returns: (context_tokens, output_tokens, mtime_of_last_usage)
    context_tokens = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
    """
    last_usage = None
    last_mtime = transcript.stat().st_mtime

    try:
        with open(transcript, "rb") as f:
            for raw in f:
                try:
                    e = json.loads(raw)
                    # Usage is nested inside message field for assistant entries
                    msg = e.get("message", {})
                    usage = None
                    if isinstance(msg, dict) and "usage" in msg:
                        usage = msg["usage"]
                    elif "usage" in e:
                        usage = e["usage"]

                    if usage and isinstance(usage, dict):
                        last_usage = usage
                        # Try to get timestamp from entry
                        ts = e.get("timestamp")
                        if ts:
                            try:
                                import dateutil.parser as _dp
                                last_mtime = _dp.parse(str(ts)).timestamp()
                            except Exception:
                                pass
                except Exception:
                    pass
    except Exception:
        pass

    if not last_usage:
        return 0, 0, last_mtime

    inp          = last_usage.get("input_tokens", 0) or 0
    cache_create = last_usage.get("cache_creation_input_tokens", 0) or 0
    cache_read   = last_usage.get("cache_read_input_tokens", 0) or 0
    output       = last_usage.get("output_tokens", 0) or 0
    context      = inp + cache_create + cache_read

    return context, output, last_mtime


def _fmt_tokens(n: int) -> str:
    if n >= 1_000:
        return f"{n // 1_000}K"
    return str(n)


def _fmt_age(elapsed_s: float) -> str:
    if elapsed_s < 60:
        return f"{int(elapsed_s)}s"
    if elapsed_s < 3600:
        return f"{int(elapsed_s / 60)}m"
    return f"{elapsed_s / 3600:.1f}h"


def _build_bar(pct: float, width: int = BAR_WIDTH) -> str:
    filled = round((min(pct, 100) / 100) * width)
    return f"[{'█' * filled}{'░' * max(0, width - filled)}]"


def _ansi_color(pct: float) -> str:
    if pct >= 85:
        return "\033[91m"
    if pct >= 60:
        return "\033[93m"
    return "\033[92m"


def _tmux_color(pct: float) -> str:
    if pct >= 85:
        return "#[fg=colour196]"
    if pct >= 60:
        return "#[fg=colour220]"
    return "#[fg=colour82]"


def meter_line(agent: str, no_color: bool = False) -> str:
    transcript = _find_transcript(agent)
    if not transcript:
        return f"⚕ {agent} │ no session"

    ctx_tokens, _, last_mtime = _read_exact_usage(transcript)
    if ctx_tokens == 0:
        return f"⚕ {agent} │ no data"

    pct     = min(ctx_tokens / CONTEXT_LIMIT * 100, 100)
    elapsed = time.time() - last_mtime
    bar     = _build_bar(pct)
    used    = _fmt_tokens(ctx_tokens)
    age     = _fmt_age(elapsed)
    pct_str = f"{pct:.0f}%"

    if no_color:
        return f"⚕ {agent} │ {used}/200K │ {bar} {pct_str} │ {age}"

    col = _ansi_color(pct)
    return f"{col}⚕ {BOLD}{agent}{RESET}{col} │ {used}/200K │ {bar} {pct_str}{RESET}{DIM} │ {age}{RESET}"


def main():
    args      = sys.argv[1:]
    watch     = "--watch" in args or "-w" in args
    no_color  = "--no-color" in args or "--plain" in args
    tmux_mode = "--tmux" in args
    agents    = [a.upper() for a in args if not a.startswith("-")] or (AGENTS if watch else ["NEXUS"])

    if watch:
        try:
            while True:
                os.system("clear")
                ts_now = datetime.now().strftime("%H:%M:%S")
                print(f"\n  {BOLD}Context Meter — SEAL Team{RESET}  [{ts_now}]")
                print(f"  {'─' * 58}")
                for a in (agents if len(agents) > 1 else AGENTS):
                    print(f"  {meter_line(a, no_color=no_color)}")
                print(f"  {'─' * 58}")
                print(f"  {DIM}Refresh {REFRESH_S}s — Ctrl+C to exit{RESET}\n")
                time.sleep(REFRESH_S)
        except KeyboardInterrupt:
            pass
        return

    if tmux_mode:
        raw_agent = agents[0] if agents else os.environ.get("SEAL_AGENT", "")
        agent_map = {"JARVIS": "JARVIS", "ALICE": "ALICE", "ADA": "ADA", "NEXUS": "NEXUS"}
        agent = agent_map.get(raw_agent.upper())

        if agent:
            transcript = _find_transcript(agent)
            if transcript:
                ctx, _, last_mtime = _read_exact_usage(transcript)
                if ctx > 0:
                    pct     = min(ctx / CONTEXT_LIMIT * 100, 100)
                    elapsed = time.time() - last_mtime
                    bar     = _build_bar(pct)
                    used    = _fmt_tokens(ctx)
                    line    = f"⚕ {agent} │ {used}/200K │ {bar} {pct:.0f}% │ {_fmt_age(elapsed)}"
                    print(f"{_tmux_color(pct)}{line}#[default]", end="")
                else:
                    print(f"#[fg=colour244]⚕ {agent} │ starting#[default]", end="")
            else:
                print(f"#[fg=colour244]⚕ {agent} │ no session#[default]", end="")
        else:
            # Session "SEAL" or unknown — mini dashboard of all agents
            parts = []
            for a in AGENTS:
                t = _find_transcript(a)
                if not t:
                    continue
                ctx, _, _ = _read_exact_usage(t)
                if ctx == 0:
                    continue
                pct = min(ctx / CONTEXT_LIMIT * 100, 100)
                parts.append(f"{_tmux_color(pct)}{a[:2]}:{pct:.0f}%#[default]")
            if parts:
                print(" ".join(parts), end="")
            else:
                print("#[fg=colour244]⚕ SEAL │ no sessions#[default]", end="")
        return

    # Normal single-line output
    agent = agents[0] if agents else "NEXUS"
    print(meter_line(agent, no_color=no_color))


if __name__ == "__main__":
    main()
