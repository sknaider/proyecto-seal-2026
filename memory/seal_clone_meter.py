#!/usr/bin/env python3
"""seal_clone_meter.py — medidor de tokens/contexto para las terminales-espejo de los clones (device).

Corre EN el device (DaditoGamer). Ubica el transcript del claude del clon por su MARCADOR de boot
('Sos <AGENT>, corriendo como CLON-ESPEJO') entre los ~/.claude/projects/*/*.jsonl, lee el uso real de
tokens (input+cache) del último assistant, y saca la barra para el status-right de tmux del espejo.
Self-contained (el device no tiene el meter central). Pedido de William: «la barra de tokens».

Uso: python3 seal_clone_meter.py <AGENT>   → línea tmux (status-right)
"""
import json
import sys
import time
from pathlib import Path

LIMIT = 1_000_000
TAIL = 2_000_000


def find_transcript(agent: str):
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        return None
    # Preferido: el project-dir del workspace PER-CLON (inequívoco, la sesión actual del espejo).
    ws = Path.home() / ".seal" / "workspace" / agent.upper()
    pdir = root / str(ws).replace("/", "-").replace(".", "-")
    if pdir.exists():
        js = sorted(pdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if js:
            return js[0]
    # Fallback (compat): por marcador de boot entre todos los transcripts.
    marker = f"Sos {agent.upper()}, corriendo como CLON-ESPEJO"
    cands = sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for c in cands:
        try:
            with open(c, "r", errors="ignore") as fh:
                if marker in fh.read(65536):
                    return c
        except OSError:
            pass
    return None


def read_usage(t: Path) -> int:
    """Últimos tokens de contexto (input + cache) del transcript. Lee solo el TAIL (perf)."""
    last = None
    try:
        size = t.stat().st_size
        with open(t, "rb") as f:
            if size > TAIL:
                f.seek(size - TAIL)
                f.readline()
            for raw in f:
                try:
                    e = json.loads(raw)
                    msg = e.get("message", {})
                    u = msg.get("usage") if isinstance(msg, dict) else None
                    if u is None and "usage" in e:
                        u = e["usage"]
                    if isinstance(u, dict):
                        last = u
                except Exception:
                    pass
    except Exception:
        return 0
    if not last:
        return 0
    return ((last.get("input_tokens", 0) or 0)
            + (last.get("cache_creation_input_tokens", 0) or 0)
            + (last.get("cache_read_input_tokens", 0) or 0))


def bar(pct: float, w: int = 10) -> str:
    filled = round(min(pct, 100) / 100 * w)
    return "[" + "█" * filled + "░" * max(0, w - filled) + "]"


def color(pct: float) -> str:
    if pct >= 85:
        return "#[fg=colour196]"
    if pct >= 60:
        return "#[fg=colour220]"
    return "#[fg=colour82]"


def main():
    agent = (sys.argv[1] if len(sys.argv) > 1 else "").upper()
    t = find_transcript(agent)
    if not t:
        print("#[fg=colour244]⚕ iniciando#[default]", end="")
        return
    ctx = read_usage(t)
    if ctx <= 0:
        print("#[fg=colour244]⚕ iniciando#[default]", end="")
        return
    pct = min(ctx / LIMIT * 100, 100)
    used = f"{ctx // 1000}K" if ctx >= 1000 else str(ctx)
    print(f"{color(pct)}⚕ {used}/1M {bar(pct)} {pct:.0f}%#[default]", end="")


if __name__ == "__main__":
    main()
